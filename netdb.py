# -*- coding: utf-8 -*-
"""لایه‌ی شبکه‌ی فاکتور یار (بدون وابستگی به Qt، فقط کتابخانه‌ی استاندارد پایتون).

سرور: روی کامپیوتر اصلی، تنها صاحب فایل دیتابیس. کلاینت‌ها فقط از طریق HTTP (شبکه‌ی داخلی) حرف می‌زنند.
کلاینت: RemoteConnection جای sqlite3.Connection می‌نشیند؛ هر `with db() as con:` یک تراکنش کامل روی سرور است
(همان کدهای حسابداری قبلی بدون تغییر) پس ثبت فاکتور + اقلام + شماره‌ی بعدی یا کامل ثبت می‌شود یا هیچ.

تضمین‌ها:
 • هر درخواست تراکنش شناسه‌ی یکتا و شماره‌ی ترتیب دارد؛ تکرار درخواست بعد از timeout هرگز دوباره اجرا نمی‌شود (نتیجه‌ی ذخیره‌شده برمی‌گردد).
 • نتیجه‌ی نامشخص commit (قطع شبکه) با پرسش از سرور روشن می‌شود؛ اگر هنوز نامشخص بود خطا می‌دهد و «موفق» نشان نمی‌دهد.
 • دسترسی‌ها در سرور اعمال می‌شود (نه فقط مخفی‌کردن دکمه‌ها). دستورهای مخرب (DROP/ATTACH/VACUUM/PRAGMA نوشتن) رد می‌شوند.
"""
import json, base64, threading, time, secrets, sqlite3, re, os, ipaddress, uuid, http.client
from collections import OrderedDict
from datetime import datetime, date
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib import request as _rq, error as _er

DEFAULT_PORT = 8750
CONFIG_NAME = "network.json"
TX_IDLE_WRITE = 60        # ثانیه: تراکنشِ نوشتنیِ رهاشده بعد از این مدت برگشت می‌خورد (قفل نوشتن آزاد شود)
TX_IDLE_READ = 600
TOKEN_IDLE = 12 * 3600


# ───────────────────────── سریال‌سازی ─────────────────────────
def _default(o):
    if isinstance(o, (bytes, bytearray)): return {"$b": base64.b64encode(bytes(o)).decode("ascii")}
    if isinstance(o, (datetime, date)): return o.isoformat()
    raise TypeError(f"نوع داده‌ی پشتیبانی‌نشده: {type(o).__name__}")

def _hook(d): return base64.b64decode(d["$b"]) if len(d) == 1 and "$b" in d else d
def dumps(o): return json.dumps(o, ensure_ascii=False, default=_default).encode("utf-8")
def loads(b): return json.loads(b.decode("utf-8") if isinstance(b, (bytes, bytearray)) else b, object_hook=_hook)


def load_config(app_dir):
    try: return json.loads((Path(app_dir) / CONFIG_NAME).read_text(encoding="utf-8"))
    except Exception: return {}

def save_config(app_dir, cfg):
    Path(app_dir).mkdir(parents=True, exist_ok=True)
    (Path(app_dir) / CONFIG_NAME).write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


# ───────────────────────── خطاها ─────────────────────────
class NetError(sqlite3.OperationalError):
    """قطع ارتباط یا خطای سرور. ambiguous=True یعنی نتیجه‌ی ثبت معلوم نیست؛ قبل از ثبت دوباره، فهرست را ببین."""
    ambiguous = False

class NetPermissionError(sqlite3.DatabaseError):
    pass

_ETYPES = {"IntegrityError": sqlite3.IntegrityError, "OperationalError": sqlite3.OperationalError,
           "ProgrammingError": sqlite3.ProgrammingError, "DatabaseError": sqlite3.DatabaseError,
           "PermissionError": NetPermissionError, "NetExpired": NetError}

class _Err(Exception):
    def __init__(self, msg, code=400, etype="DatabaseError"): super().__init__(msg); self.code = code; self.etype = etype


# ───────────────────────── سیاست دستورها (سمت سرور) ─────────────────────────
_COMMENT = re.compile(r"^(\s|--[^\n]*\n|/\*.*?\*/)*", re.S)
_READ_PRAGMAS = {"table_info", "table_xinfo", "index_list", "quick_check", "integrity_check", "foreign_key_check", "foreign_key_list"}

def classify(sql):
    """r=خواندن، w=نوشتن داده، d=ساخت امنِ جدول/ستون (IF NOT EXISTS / ADD COLUMN)، None=ممنوع."""
    s = _COMMENT.sub("", sql, count=1).strip().rstrip(";").strip()
    m = re.match(r"([A-Za-z]+)", s); kw = m.group(1).upper() if m else ""
    if kw in ("SELECT", "WITH"): return "r"
    if kw in ("INSERT", "REPLACE", "UPDATE", "DELETE"): return "w"
    if kw == "PRAGMA":
        p = re.match(r"PRAGMA\s+([A-Za-z_]+)", s, re.I)
        return "r" if p and p.group(1).lower() in _READ_PRAGMAS and "=" not in s else None
    if re.match(r"CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\b", s, re.I) or re.match(r"ALTER\s+TABLE\s+\w+\s+ADD\s+COLUMN\b", s, re.I): return "d"
    return None

def split_script(script):
    out, buf = [], ""
    for line in script.splitlines(True):
        buf += line
        if sqlite3.complete_statement(buf): out.append(buf.strip()); buf = ""
    if buf.strip(): out.append(buf.strip())
    return [x for x in out if x.strip(";").strip()]

def allowed(user, sql, kind, params):
    """مجوزهای سمت سرور. مدیر: همه‌چیز (با همان محدودیت دستورها). بقیه: جدول کاربران فقط رکورد و رمز خودشان؛ گزارش حسابرسی فقط مدیر؛
    حذف فاکتور/مشتری/چک/برگشتی فقط با مجوز «حذف»."""
    if user["role"] == "admin": return True
    perms = user.get("perms") or []; low = " ".join(sql.lower().split())
    if re.search(r"\busers\b", low):
        if low == "select * from users where username=?" and params and str(params[0]).strip() == user["username"]: return True
        if low == "update users set salt=?, hash=?, iters=? where id=?" and len(params) == 4 and params[3] == user["id"]: return True
        return False
    if kind == "r" and re.search(r"\baudit_log\b", low): return False
    if kind == "w" and re.match(r"delete from (invoices|customers|cheques|returns|purchases|suppliers|supplier_payments)\b", low): return "delete" in perms
    if kind in ("w", "d") and re.search(r"\b(purchases|purchase_items|suppliers|supplier_payments)\b", low) and not re.match(r"create (unique )?(table|index)", low): return "purchases" in perms
    return True


def _norm_user(u):
    try: perms = json.loads(u.get("perms") or "[]")
    except Exception: perms = []
    return {"id": u["id"], "username": u["username"], "display": u.get("display") or "", "role": u["role"], "perms": perms, "active": u.get("active", 1)}


# ───────────────────────── سرور ─────────────────────────
class DbServer:
    def __init__(self, db_path, verify_login, count_users, host="0.0.0.0", port=DEFAULT_PORT, backup_dir=None, keep=30,
                 interval_min=0, allowed_cidrs=None, log=None, audit=None):
        self.db_path = str(db_path); self.verify_login = verify_login; self.count_users = count_users
        self.host, self.port = host, int(port); self.backup_dir = Path(backup_dir or Path(db_path).parent / "backups")
        self.keep = max(3, int(keep)); self.interval_min = int(interval_min); self.audit = audit
        self.nets = [ipaddress.ip_network(c, strict=False) for c in (allowed_cidrs or [])]
        self._log = log or (lambda m: None)
        self._meta = threading.RLock(); self.tokens = {}; self.txs = {}; self._tl = {}; self.done = OrderedDict()
        self._pool = []; self.maint = False; self.fails = {}; self.httpd = None; self.started = None
        self.last_backup = ""; self.last_backup_msg = ""; self._stop = threading.Event()

    # —— اتصال‌ها ——
    def _open(self):
        with self._meta:
            if self._pool: return self._pool.pop()
        c = sqlite3.connect(self.db_path, timeout=15, check_same_thread=False, isolation_level=None); c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA busy_timeout=15000"); c.execute("PRAGMA synchronous=FULL")
            if str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal": c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error: pass
        return c

    def _release(self, c, ok=True):
        with self._meta:
            if ok and len(self._pool) < 8: self._pool.append(c); return
        try: c.close()
        except Exception: pass

    def db_ok(self):
        try:
            c = self._open()
            try: return c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally: self._release(c)
        except Exception: return False

    # —— نشست‌ها ——
    def ip_ok(self, ip):
        try: a = ipaddress.ip_address(ip.split("%")[0])
        except ValueError: return False
        if self.nets: return any(a in n for n in self.nets) or a.is_loopback
        return a.is_private or a.is_loopback or a.is_link_local          # پیش‌فرض: فقط شبکه‌ی داخلی (نه اینترنت)

    def login(self, ip, username, password):
        key = (ip, (username or "").strip().lower()); now = time.time()
        with self._meta:
            n, last = self.fails.get(key, (0, 0))
            if n >= 5 and now - last < 60: raise _Err("تعداد تلاش ناموفق زیاد است؛ یک دقیقه صبر کن.", 429, "PermissionError")
        u = self.verify_login(username or "", password or "")
        if not u:
            with self._meta: self.fails[key] = (self.fails.get(key, (0, 0))[0] + 1, now)
            self._log(f"ورود ناموفق از {ip}: {username}"); self._aud(username, "ورود ناموفق", ip); raise _Err("نام کاربری یا رمز درست نیست، یا حساب غیرفعال است.", 401, "PermissionError")
        with self._meta:
            self.fails.pop(key, None); tok = secrets.token_urlsafe(32); nu = _norm_user(u)
            self.tokens[tok] = {"user": nu, "seen": now, "ip": ip}
        self._log(f"ورود موفق: {nu['username']} از {ip}"); self._aud(nu["username"], "ورود موفق", ip)
        return tok, nu

    def _aud(self, user, action, detail=""):
        if self.audit:
            try: self.audit(user, action, detail)
            except Exception: pass

    def user_for(self, tok):
        with self._meta:
            t = self.tokens.get(tok)
            if not t: return None
            if time.time() - t["seen"] > TOKEN_IDLE: self.tokens.pop(tok, None); return None
            t["seen"] = time.time(); return t["user"]

    def logout(self, tok):
        with self._meta: self.tokens.pop(tok, None)

    def active_users(self):
        with self._meta:
            now = time.time()
            return sorted({t["user"]["username"] for t in self.tokens.values() if now - t["seen"] < 120})

    # —— تراکنش‌ها ——
    def _txlock(self, tx):
        with self._meta: return self._tl.setdefault(tx, threading.Lock())

    def _finish(self, tx, st, state):
        with self._meta:
            self.txs.pop(tx, None); self.done[tx] = state; self._tl.pop(tx, None)
            while len(self.done) > 5000: self.done.popitem(last=False)
        self._release(st["con"], ok=(state != "expired"))

    def tx_exec(self, user, tx, seq, req):
        with self._txlock(tx):
            st = self.txs.get(tx)
            if st is None:
                if tx in self.done or seq != 1: raise _Err("این عملیات دیگر روی سرور فعال نیست (منقضی یا برگشت‌خورده). دوباره انجام بده.", 409, "NetExpired")
                if self.maint: raise _Err("سرور در حال بازیابی/نگهداری است؛ چند لحظه بعد دوباره تلاش کن.", 503, "NetExpired")
                con = self._open()
                try: con.execute("BEGIN")
                except Exception: self._release(con, ok=False); raise
                st = {"con": con, "uid": user["id"], "t": time.time(), "res": {}, "wrote": False}
                with self._meta: self.txs[tx] = st
            if st["uid"] != user["id"]: raise _Err("دسترسی نامعتبر.", 403, "PermissionError")
            st["t"] = time.time()
            if seq in st["res"]: return st["res"][seq]
            res = self._run(user, st, req)
            st["res"] = {seq: res}                       # فقط آخرین نتیجه برای تکرارِ احتمالی نگه داشته می‌شود
            return res

    def _run(self, user, st, req):
        con = st["con"]
        if "script" in req:
            for stmt in split_script(req["script"]):
                k = classify(stmt)
                if k not in ("d", "w") or not allowed(user, stmt, k, ()): raise _Err("دستور در اسکریپت مجاز نیست.", 403, "PermissionError")
            for stmt in split_script(req["script"]): con.execute(stmt)
            st["wrote"] = True
            return {"cols": [], "rows": [], "lastrowid": None, "rowcount": -1}
        sql = req.get("sql", ""); params = req.get("params") or []; kind = classify(sql)
        if kind is None: raise _Err("این دستور مجاز نیست.", 403, "PermissionError")
        chk = req["many"][0] if req.get("many") else params
        if not allowed(user, sql, kind, chk): raise _Err("اجازه‌ی این عملیات را نداری.", 403, "PermissionError")
        try:
            cur = con.executemany(sql, req["many"]) if req.get("many") else con.execute(sql, params)
        except sqlite3.Error as e:
            raise _Err(str(e), 400, type(e).__name__ if type(e).__name__ in _ETYPES else "DatabaseError")
        if kind in ("w", "d"): st["wrote"] = True
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()] if cols else []
        return {"cols": cols, "rows": rows, "lastrowid": cur.lastrowid, "rowcount": cur.rowcount}

    def tx_end(self, user, tx, action):
        with self._txlock(tx):
            st = self.txs.get(tx)
            if st is None:
                state = self.done.get(tx)
                if state == "commit" and action == "commit": return {"ok": True, "state": "commit"}        # تکرارِ درخواست commit: قبلاً ثبت شده
                if state == "rollback" and action == "rollback": return {"ok": True, "state": "rollback"}
                if state is None and action == "rollback": return {"ok": True, "state": "none"}
                if action == "commit" and state == "ro-expired": return {"ok": True, "state": "ro"}
                raise _Err("ثبت انجام نشد: عملیات روی سرور منقضی یا برگشت خورده است.", 409, "NetExpired")
            if st["uid"] != user["id"]: raise _Err("دسترسی نامعتبر.", 403, "PermissionError")
            try:
                st["con"].execute("COMMIT" if action == "commit" else "ROLLBACK")
            except sqlite3.Error as e:
                try: st["con"].execute("ROLLBACK")
                except Exception: pass
                self._finish(tx, st, "rollback"); raise _Err(str(e), 400, "OperationalError")
            self._finish(tx, st, "commit" if action == "commit" else "rollback")
            return {"ok": True, "state": action}

    def _reaper(self):
        while not self._stop.wait(5):
            now = time.time()
            with self._meta: items = list(self.txs.items())
            for tx, st in items:
                if now - st["t"] > (TX_IDLE_WRITE if st["wrote"] else TX_IDLE_READ):
                    lk = self._txlock(tx)
                    if not lk.acquire(blocking=False): continue
                    try:
                        if self.txs.get(tx) is st:
                            try: st["con"].execute("ROLLBACK")
                            except Exception: pass
                            self._finish(tx, st, "expired" if st["wrote"] else "ro-expired")
                            self._log(f"تراکنش رهاشده برگشت خورد ({'نوشتنی' if st['wrote'] else 'فقط‌خواندنی'})")
                    finally:
                        try: lk.release()
                        except RuntimeError: pass
            with self._meta:
                for t in [k for k, v in self.tokens.items() if now - v["seen"] > TOKEN_IDLE]: self.tokens.pop(t, None)

    # —— پشتیبان‌گیری و بازیابی ——
    @staticmethod
    def _stamp(p):
        s = []
        for suf in ("", "-wal"):
            f = Path(str(p) + suf)
            if f.exists(): s.append(f"{f.stat().st_mtime_ns}:{f.stat().st_size}")
        return "|".join(s)

    def backup_now(self, dest=None):
        """نسخه‌ی پشتیبانِ هم‌سازگار با API پشتیبان SQLite (وسط ثبت تراکنش هم ناسازگار نمی‌شود) و تأیید سلامت آن."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        keep_old = dest is None
        dest = Path(dest or self.backup_dir / f"factordesk-{datetime.now():%Y%m%d-%H%M%S}.db")
        src = sqlite3.connect(self.db_path, timeout=30); dst = sqlite3.connect(str(dest))
        try: src.backup(dst)
        finally: dst.close(); src.close()
        chk = sqlite3.connect(str(dest))
        try: ok = chk.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally: chk.close()
        if not ok:
            try: dest.unlink()
            except Exception: pass
            self.last_backup_msg = f"{datetime.now():%Y-%m-%d %H:%M} — ناموفق (پشتیبان سالم نبود)"; self._log(self.last_backup_msg)
            raise RuntimeError("نسخه‌ی پشتیبان تولیدشده سالم نبود.")
        if keep_old:
            for old in sorted(self.backup_dir.glob("factordesk-*.db"))[:-self.keep]:
                try: old.unlink()
                except Exception: pass
        self.last_backup = str(dest); self.last_backup_msg = f"{datetime.now():%Y-%m-%d %H:%M} — موفق"; self._log(f"پشتیبان: {dest.name}")
        return dest

    def _scheduler(self):
        last = self._stamp(self.db_path)
        while not self._stop.wait(max(30, self.interval_min * 60)):
            if self.interval_min <= 0: continue
            cur = self._stamp(self.db_path)
            if cur == last: continue
            try: self.backup_now(); last = cur
            except Exception as e: self._log(f"پشتیبان‌گیری زمان‌بندی‌شده شکست خورد: {e}")

    def restore(self, path):
        """بازیابی با نگه‌داشتن نسخه‌ی ایمنی از وضعیت فعلی. تراکنش‌های باز تمام می‌شوند و در حین کار درخواست جدید رد می‌شود."""
        chk = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            if chk.execute("PRAGMA quick_check").fetchone()[0] != "ok": raise RuntimeError("فایل پشتیبان سالم نیست.")
            if not chk.execute("SELECT 1 FROM sqlite_master WHERE name='invoices'").fetchone(): raise RuntimeError("این فایل، پشتیبان فاکتور یار نیست.")
        finally: chk.close()
        self.maint = True
        try:
            for _ in range(40):
                if not self.txs: break
                time.sleep(0.5)
            with self._meta: left = list(self.txs.items())
            for tx, st in left:
                try: st["con"].execute("ROLLBACK")
                except Exception: pass
                self._finish(tx, st, "expired")
            safety_copy = self.backup_dir / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"; self.backup_now(safety_copy)
            with self._meta: pool, self._pool = self._pool, []
            for c in pool:
                try: c.close()
                except Exception: pass
            src = sqlite3.connect(str(path)); dst = sqlite3.connect(self.db_path, timeout=30)
            try: src.backup(dst)
            finally: dst.close(); src.close()
            self._log(f"بازیابی از {Path(path).name} انجام شد (نسخه‌ی ایمنی: {safety_copy.name})")
            return safety_copy
        finally: self.maint = False

    # —— راه‌اندازی ——
    def start(self):
        app = self
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *a): pass
            def _send(self, code, obj):
                b = dumps(obj); self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def _user(self):
                h = self.headers.get("Authorization", ""); tok = h[7:] if h.startswith("Bearer ") else ""
                u = app.user_for(tok) if tok else None
                if not u: raise _Err("ورود لازم است.", 401, "PermissionError")
                return u, tok
            def _guard(self, fn):
                try:
                    if not app.ip_ok(self.client_address[0]): return self._send(403, {"error": "دسترسی از این آدرس مجاز نیست."})
                    fn()
                except _Err as e: self._send(e.code, {"error": str(e), "etype": e.etype})
                except (BrokenPipeError, ConnectionResetError): pass
                except Exception as e:
                    app._log(f"خطای داخلی: {type(e).__name__}")
                    try: self._send(500, {"error": "خطای داخلی سرور.", "etype": "DatabaseError"})
                    except Exception: pass
            def do_GET(self): self._guard(self._get)
            def do_POST(self): self._guard(self._post)
            def _get(self):
                if self.path == "/api/status":
                    return self._send(200, {"app": "FactorDesk", "v": 1, "users": app.count_users(), "maint": app.maint})
                if self.path == "/api/backup/download":
                    u, _ = self._user()
                    if u["role"] != "admin": raise _Err("فقط مدیر.", 403, "PermissionError")
                    import tempfile
                    fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
                    try:
                        app.backup_now(tmp); data = Path(tmp).read_bytes()
                    finally:
                        try: os.unlink(tmp)
                        except Exception: pass
                    app._aud(u["username"], "دریافت پشتیبان از سرور", "")
                    self.send_response(200); self.send_header("Content-Type", "application/octet-stream"); self.send_header("Content-Length", str(len(data)))
                    self.end_headers(); self.wfile.write(data); return
                raise _Err("آدرس نامعتبر.", 404)
            def _post(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 64 * 1024 * 1024: raise _Err("درخواست بیش از حد بزرگ است.", 413)
                req = loads(self.rfile.read(n)) if n else {}
                p = self.path
                if p == "/api/login":
                    tok, u = app.login(self.client_address[0], req.get("username"), req.get("password")); return self._send(200, {"token": tok, "user": u})
                u, tok = self._user()
                if p == "/api/logout": app.logout(tok); return self._send(200, {"ok": True})
                if p == "/api/tx/exec": return self._send(200, app.tx_exec(u, str(req["tx"]), int(req["seq"]), req))
                if p == "/api/tx/end": return self._send(200, app.tx_end(u, str(req["tx"]), "commit" if req.get("action") == "commit" else "rollback"))
                if p == "/api/backup/now":
                    if u["role"] != "admin": raise _Err("فقط مدیر.", 403, "PermissionError")
                    return self._send(200, {"file": app.backup_now().name})
                raise _Err("آدرس نامعتبر.", 404)
        self.httpd = ThreadingHTTPServer((self.host, self.port), H); self.httpd.daemon_threads = True
        if self.port == 0: self.port = self.httpd.server_address[1]
        self.started = datetime.now()
        for fn in (self.httpd.serve_forever, self._reaper, self._scheduler): threading.Thread(target=fn, daemon=True).start()
        self._log(f"سرور روی {self.host}:{self.port} شروع شد")

    def stop(self):
        self._stop.set()
        if self.httpd:
            try: self.httpd.shutdown(); self.httpd.server_close()
            except Exception: pass
        with self._meta: left = list(self.txs.items())
        for tx, st in left:
            try: st["con"].execute("ROLLBACK")
            except Exception: pass
            self._finish(tx, st, "expired")
        with self._meta: pool, self._pool = self._pool, []
        for c in pool:
            try: c.close()
            except Exception: pass
        self._log("سرور متوقف شد")


# ───────────────────────── کلاینت ─────────────────────────
_opener = _rq.build_opener(_rq.ProxyHandler({}))             # پروکسیِ سیستم برای آدرس شبکه‌ی داخلی دخالت نکند

def _http(method, url, body, token, timeout):
    req = _rq.Request(url, data=body, method=method); req.add_header("Content-Type", "application/json; charset=utf-8")
    if token: req.add_header("Authorization", "Bearer " + token)
    try:
        with _opener.open(req, timeout=timeout) as r: return r.status, loads(r.read())
    except _er.HTTPError as e:
        try: return e.code, loads(e.read())
        except Exception: return e.code, {"error": "خطای سرور"}

_NETERR = (_er.URLError, OSError, TimeoutError, ConnectionError, http.client.HTTPException)


class Remote:
    """وضعیت اتصال کلاینت (یکی در کل برنامه)."""
    def __init__(self):
        self.host = ""; self.port = DEFAULT_PORT; self.token = ""; self.user = None; self._cred = None
        self.online = None; self.last_error = ""; self.timeout = 20; self.server_users = None; self._mon = None

    def configure(self, host, port=DEFAULT_PORT): self.host = (host or "").strip(); self.port = int(port or DEFAULT_PORT)
    def url(self, path): return f"http://{self.host}:{self.port}{path}"
    def _set(self, ok, msg=""): self.online = ok; self.last_error = "" if ok else msg

    def call(self, path, payload=None, method="POST", retries=2, auth=True, timeout=None, seq=None, _re=False):
        body = dumps(payload) if payload is not None else None
        for i in range(retries + 1):
            try: code, data = _http(method, self.url(path), body, self.token if auth else "", timeout or self.timeout); break
            except _NETERR as e:
                self._set(False, str(e))
                if i < retries: time.sleep(0.4 * (i + 1)); continue
                raise NetError(f"ارتباط با سرور ({self.host}:{self.port}) برقرار نیست. شبکه و روشن‌بودن سرور را بررسی کن.")
        self._set(True)
        if code == 401 and auth and not _re and self._cred:
            if seq not in (None, 1): raise NetError("نشست شما منقضی شد (احتمالاً سرور دوباره راه‌اندازی شد). این عملیات انجام نشد؛ دوباره انجام بده.")
            self.login(*self._cred); return self.call(path, payload, method, retries, auth, timeout, seq, _re=True)
        if code >= 400:
            msg = (data or {}).get("error") or f"خطای سرور ({code})"; et = (data or {}).get("etype", "")
            if code == 503: raise NetError(msg)
            raise _ETYPES.get(et, sqlite3.DatabaseError)(msg)
        return data

    def login(self, username, password):
        d = self.call("/api/login", {"username": username, "password": password}, auth=False, retries=1)
        self.token = d["token"]; self.user = d["user"]; self._cred = (username, password); return d["user"]

    def status(self, timeout=3):
        d = self.call("/api/status", method="GET", auth=False, retries=0, timeout=timeout); self.server_users = d.get("users"); return d

    def download_backup(self, dest):
        req = _rq.Request(self.url("/api/backup/download")); req.add_header("Authorization", "Bearer " + self.token)
        try:
            with _opener.open(req, timeout=120) as r: Path(dest).write_bytes(r.read())
        except _er.HTTPError as e: raise NetPermissionError("دریافت پشتیبان مجاز نیست." if e.code in (401, 403) else f"خطای سرور ({e.code})")
        except _NETERR: raise NetError("ارتباط با سرور برقرار نیست.")

    def start_monitor(self, interval=5):
        if self._mon: return
        def loop():
            while True:
                try: self.status(timeout=3)
                except Exception: pass
                time.sleep(interval)
        self._mon = threading.Thread(target=loop, daemon=True); self._mon.start()


REMOTE = Remote()


class RRow(tuple):
    """جایگزین sqlite3.Row: هم با اندیس، هم با نام ستون."""
    def __new__(cls, cols, vals): o = super().__new__(cls, vals); o._cols = cols; return o
    def keys(self): return list(self._cols)
    def __getitem__(self, k):
        if isinstance(k, str):
            try: return tuple.__getitem__(self, self._cols.index(k))
            except ValueError: raise IndexError("No item with that key")
        return tuple.__getitem__(self, k)


class RCursor:
    def __init__(self, res):
        cols = res.get("cols") or []; self.description = [(c,) + (None,) * 6 for c in cols] if cols else None
        self._rows = [RRow(cols, r) for r in res.get("rows", [])]; self._i = 0
        self.lastrowid = res.get("lastrowid"); self.rowcount = res.get("rowcount", -1)
    def fetchone(self):
        if self._i >= len(self._rows): return None
        self._i += 1; return self._rows[self._i - 1]
    def fetchall(self): r = self._rows[self._i:]; self._i = len(self._rows); return r
    def fetchmany(self, n=1): r = self._rows[self._i:self._i + n]; self._i += len(r); return r
    def __iter__(self): return iter(self.fetchall())


class RemoteConnection:
    """جایگزین sqlite3.Connection برای کلاینت: هیچ فایلی باز نمی‌کند و همه‌چیز را از طریق سرور انجام می‌دهد."""
    row_factory = None
    def __init__(self, remote=None): self.tx = None; self.seq = 0; self.remote = remote or REMOTE
    def _x(self, payload):
        if self.tx is None: self.tx = uuid.uuid4().hex
        self.seq += 1
        return RCursor(self.remote.call("/api/tx/exec", dict(payload, tx=self.tx, seq=self.seq), seq=self.seq))
    def execute(self, sql, params=()): return self._x({"sql": sql, "params": list(params)})
    def executemany(self, sql, seq_params): return self._x({"sql": sql, "many": [list(p) for p in seq_params]})
    def executescript(self, script): return self._x({"script": script})
    def commit(self):
        if self.tx is None: return
        tx, self.tx = self.tx, None
        try: self.remote.call("/api/tx/end", {"tx": tx, "action": "commit"}, retries=4)
        except NetError as e:
            if "برقرار نیست" in str(e):
                err = NetError("ارتباط حین ثبت قطع شد و نتیجه‌ی ثبت نامشخص است. قبل از ثبت دوباره، فهرست فاکتورها/دریافت‌ها را بررسی کن."); err.ambiguous = True; raise err
            raise
    def rollback(self):
        if self.tx is None: return
        tx, self.tx = self.tx, None
        try: self.remote.call("/api/tx/end", {"tx": tx, "action": "rollback"}, retries=1)
        except Exception: pass
    def close(self): self.rollback()
