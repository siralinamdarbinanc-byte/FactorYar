# -*- coding: utf-8 -*-
"""لایه‌ی امنیت و پایداری داده (فاکتور یار).

- WAL + synchronous=FULL: هر ثبت در لحظه روی دیسک نوشته می‌شود و با قطع برق/هنگ، دیتابیس خراب نمی‌شود.
- doc_history: دفتر تاریخچه‌ی فقط‌افزودنی (با تریگر قفل شده؛ نه ویرایش نه حذف). هر حذف، یک کپی کامل از سند
  (JSON) نگه می‌دارد و از «سطل بازیافت» قابل بازگردانی است. ثبت حذف و خودِ حذف در یک تراکنش‌اند.
- محافظ چاپ: جلوی چاپ هم‌زمان و چاپ تکراری ناخواسته را می‌گیرد و هر چاپ را ثبت می‌کند.
این فایل عمداً به Qt وابسته نیست تا مستقل تست شود.
"""
import json, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime

KIND_FA = {"invoice": "فاکتور", "customer": "مشتری", "payment": "دریافت", "cheque": "چک", "return": "مرجوعی", "receipt": "چاپ فیش"}
ACTION_FA = {"create": "ثبت", "edit": "ویرایش", "convert": "تبدیل پیش‌فاکتور", "delete": "حذف", "restore": "بازگردانی",
             "to_proforma": "برگشت به پیش‌فاکتور", "print": "چاپ فیش", "print_fail": "خطای چاپ"}
_INSERT_ORDER = ["customers", "invoices", "invoice_items", "returns", "return_items", "cheques", "payments"]


class RestoreError(Exception):
    pass


class PrintBusy(Exception):
    pass


# ───────────── اتصال ─────────────
def apply_pragmas(con):
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA synchronous=FULL")
    try:
        if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass            # مثلاً فایل فقط‌خواندنی؛ برنامه با حالت قبلی ادامه می‌دهد


def quick_check(path):
    """(ok, message) — بررسی سلامت فایل دیتابیس هنگام باز شدن برنامه."""
    try:
        con = sqlite3.connect(str(path), timeout=15)
        try:
            r = con.execute("PRAGMA quick_check").fetchall()
        finally:
            con.close()
        if len(r) == 1 and str(r[0][0]).lower() == "ok":
            return True, "ok"
        return False, "؛ ".join(str(x[0]) for x in r[:5])
    except sqlite3.Error as e:
        return False, str(e)


def data_stamp(db_path):
    """امضای تغییر دیتابیس برای پشتیبان‌گیری؛ فایل -wal را هم می‌شمارد (در WAL فایل اصلی دیرتر تغییر می‌کند)."""
    from pathlib import Path
    parts = []
    for suffix in ("", "-wal"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            st = p.stat(); parts.append(f"{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


# ───────────── جدول تاریخچه ─────────────
def ensure_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS doc_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, user TEXT DEFAULT '', action TEXT NOT NULL,
      kind TEXT NOT NULL, ref_id INTEGER, label TEXT DEFAULT '', reason TEXT DEFAULT '',
      snapshot TEXT DEFAULT '', link_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_hist_action ON doc_history(action, id);
    CREATE INDEX IF NOT EXISTS idx_hist_link ON doc_history(link_id);
    CREATE TRIGGER IF NOT EXISTS doc_history_no_update BEFORE UPDATE ON doc_history
      BEGIN SELECT RAISE(ABORT, 'doc_history is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS doc_history_no_delete BEFORE DELETE ON doc_history
      BEGIN SELECT RAISE(ABORT, 'doc_history is append-only'); END;
    """)


def _q(con, sql, params=()):
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def snapshot(con, kind, rid):
    """کپی کامل سند و وابسته‌هایش به‌صورت dict؛ خالی یعنی سند پیدا نشد."""
    if kind == "invoice":
        d = {"invoices": _q(con, "SELECT * FROM invoices WHERE id=?", (rid,))}
        if d["invoices"]:
            d["invoice_items"] = _q(con, "SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY row_no", (rid,))
            d["payments"] = _q(con, "SELECT * FROM payments WHERE invoice_id=?", (rid,))
            d["cheques"] = _q(con, "SELECT * FROM cheques WHERE invoice_id=?", (rid,))
        return d if d["invoices"] else {}
    if kind == "customer":
        r = _q(con, "SELECT * FROM customers WHERE id=?", (rid,)); return {"customers": r} if r else {}
    if kind == "payment":
        r = _q(con, "SELECT * FROM payments WHERE id=?", (rid,)); return {"payments": r} if r else {}
    if kind == "return":
        r = _q(con, "SELECT * FROM returns WHERE id=?", (rid,))
        return {"returns": r, "return_items": _q(con, "SELECT * FROM return_items WHERE return_id=? ORDER BY row_no", (rid,))} if r else {}
    if kind == "cheque":
        r = _q(con, "SELECT * FROM cheques WHERE id=?", (rid,))
        return {"cheques": r, "payments": _q(con, "SELECT * FROM payments WHERE cheque_id=?", (rid,))} if r else {}
    raise ValueError(kind)


def _cname(con, cid):
    r = con.execute("SELECT name FROM customers WHERE id=?", (cid,)).fetchone()
    return r[0] if r else ""


def label_for(con, kind, snap):
    try:
        if kind == "invoice":
            i = snap["invoices"][0]; items = snap.get("invoice_items", [])
            total = sum(float(x.get("quantity") or 0) * float(x.get("unit_price") or 0) for x in items) - float(i.get("discount") or 0)
            return f"{'پیش‌فاکتور' if i.get('is_proforma') else 'فاکتور'} {i['invoice_no']} — {_cname(con, i['customer_id'])} — جمع {int(round(total)):,}"
        if kind == "customer":
            return snap["customers"][0]["name"]
        if kind == "payment":
            p = snap["payments"][0]
            return f"دریافت {int(round(float(p['amount']))):,} — {_cname(con, p['customer_id'])} — {p['method']} — {p['payment_date']}"
        if kind == "return":
            r0 = snap["returns"][0]; tot = sum(float(x.get("quantity") or 0) * float(x.get("unit_price") or 0) for x in snap.get("return_items", []))
            return f"مرجوعی {r0['return_no']} — {_cname(con, r0['customer_id'])} — جمع {int(round(tot)):,}"
        if kind == "cheque":
            c = snap["cheques"][0]
            return f"چک {c.get('cheque_no') or ''} — {_cname(con, c['customer_id'])} — {int(round(float(c['amount']))):,}"
    except Exception:
        pass
    return ""


def _log(con, user, action, kind, rid, label, snap, reason="", link_id=None):
    return con.execute(
        "INSERT INTO doc_history(at,user,action,kind,ref_id,label,reason,snapshot,link_id) VALUES(?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), user or "", action, kind, rid, label, reason or "",
         json.dumps(snap, ensure_ascii=False, default=str), link_id)).lastrowid


# ───────────── حذف و بازگردانی ─────────────
def archive_delete(con, user, kind, rid, reason=""):
    """قبل از حذف واقعی صدا بزنید (داخل همان تراکنش). اگر ثبت نشود، حذف هم نباید انجام شود."""
    snap = snapshot(con, kind, rid)
    if not snap:
        return None
    return _log(con, user, "delete", kind, rid, label_for(con, kind, snap), snap, reason)


def list_deleted(con):
    return _q(con, """SELECT h.id,h.at,h.user,h.kind,h.ref_id,h.label,h.reason,h.snapshot FROM doc_history h
                      WHERE h.action='delete' AND NOT EXISTS (SELECT 1 FROM doc_history r WHERE r.link_id=h.id AND r.action='restore')
                      ORDER BY h.id DESC""")


def list_history(con, limit=1000):
    return _q(con, "SELECT id,at,user,action,kind,ref_id,label,reason FROM doc_history ORDER BY id DESC LIMIT ?", (int(limit),))


def restore(con, user, hist_id):
    """سند حذف‌شده را با همان شناسه‌ها برمی‌گرداند. در خطا RestoreError؛ تراکنش را فراخوان باید برگرداند (db() خودش می‌کند)."""
    rows = _q(con, "SELECT * FROM doc_history WHERE id=?", (hist_id,))
    if not rows or rows[0]["action"] != "delete":
        raise RestoreError("این مورد یک رکورد حذف نیست.")
    h = rows[0]
    if con.execute("SELECT 1 FROM doc_history WHERE link_id=? AND action='restore'", (hist_id,)).fetchone():
        raise RestoreError("این مورد قبلاً بازگردانده شده است.")
    snap = json.loads(h["snapshot"])
    inv = (snap.get("invoices") or [None])[0]
    if inv is not None:
        if con.execute("SELECT 1 FROM invoices WHERE invoice_no=?", (inv["invoice_no"],)).fetchone():
            raise RestoreError(f"شماره {inv['invoice_no']} الان برای سند دیگری استفاده شده؛ بازگردانی ممکن نیست.")
    try:
        for tbl in _INSERT_ORDER:
            for row in snap.get(tbl, []):
                have = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
                cols = [c for c in row if c in have]
                if "id" in row and con.execute(f"SELECT 1 FROM {tbl} WHERE id=?", (row["id"],)).fetchone():
                    raise RestoreError("شناسه‌ی این سند الان در دیتابیس وجود دارد؛ بازگردانی ممکن نیست.")
                con.execute(f"INSERT INTO {tbl}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[c] for c in cols])
    except sqlite3.IntegrityError as e:
        raise RestoreError(f"بازگردانی ممکن نیست (مثلاً مشتری یا فاکتور مرتبط دیگر وجود ندارد): {e}")
    _log(con, user, "restore", h["kind"], h["ref_id"], h["label"], {}, link_id=hist_id)
    return h


# ───────────── ثبت و ویرایش فاکتور ─────────────
def log_invoice_change(con, user, action, iid, before=None, reason=""):
    after = snapshot(con, "invoice", iid)
    if not after:
        return None
    return _log(con, user, action, "invoice", iid, label_for(con, "invoice", after), {"before": before or {}, "after": after}, reason)


def invoice_to_proforma(con, user, iid, reason=""):
    """فاکتور ثبت‌شده را به پیش‌فاکتور برمی‌گرداند (از حساب و کاردکس خارج می‌شود). (شماره‌ی قبلی، شماره‌ی پیش‌فاکتور) را برمی‌گرداند.
    اگر دریافت، چک یا مرجوعی به فاکتور وصل باشد ValueError می‌دهد."""
    r = con.execute("SELECT invoice_no, COALESCE(is_proforma,0) FROM invoices WHERE id=?", (iid,)).fetchone()
    if not r:
        raise ValueError("فاکتور پیدا نشد.")
    if r[1]:
        raise ValueError("این مورد فاکتور نیست (پیش‌فاکتور است).")
    links = con.execute("SELECT (SELECT COUNT(*) FROM payments WHERE invoice_id=?)+(SELECT COUNT(*) FROM cheques WHERE invoice_id=?)"
                        "+(SELECT COUNT(*) FROM returns WHERE invoice_id=?)", (iid, iid, iid)).fetchone()[0]
    if links:
        raise ValueError("برای این فاکتور دریافت، چک یا مرجوعی ثبت شده است. ابتدا آن‌ها را ویرایش یا حذف کن؛ سپس فاکتور قابل برگشت به پیش‌فاکتور است.")
    old = r[0]; before = snapshot(con, "invoice", iid)
    row = con.execute("SELECT value FROM settings WHERE key='next_proforma'").fetchone()
    n = int(row[0]) if row and str(row[0]).isdigit() else 1
    while con.execute("SELECT 1 FROM invoices WHERE invoice_no=?", (f"P{n}",)).fetchone():
        n += 1
    new_no = f"P{n}"
    con.execute("UPDATE invoices SET is_proforma=1, invoice_no=?, kind='پیش‌فاکتور', paid_initial=0, due_date='' WHERE id=?", (new_no, iid))
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('next_proforma',?)", (str(n + 1),))
    log_invoice_change(con, user, "to_proforma", iid, before, f"{reason} (شماره‌ی قبلی فاکتور: {old})".strip())
    return old, new_no


# ───────────── محافظ چاپ ─────────────
_lock = threading.Lock()
_last = {}


@contextmanager
def print_guard(key):
    """هم‌زمانی را رد می‌کند: تا یک کار چاپ تمام نشده، کار دوم شروع نمی‌شود."""
    if not _lock.acquire(blocking=False):
        raise PrintBusy("یک کار چاپ هنوز در حال انجام است. چند لحظه صبر کن.")
    try:
        yield
        _last[key] = time.monotonic()
    finally:
        _lock.release()


def recently_printed(key, gap=60.0):
    t = _last.get(key)
    return t is not None and (time.monotonic() - t) < gap


def log_print(con, user, ok, iid, label, err=""):
    return _log(con, user, "print" if ok else "print_fail", "receipt", iid, label, {"error": err} if err else {})
