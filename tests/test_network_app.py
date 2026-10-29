# -*- coding: utf-8 -*-
"""پنل پیامک: پرداخت وجه / چک / مرجوعی، قالب‌های آماده، و جست‌وجوی کلمه‌ای (با Qt شبیه‌سازی‌شده)."""
import sys, os, types, tempfile, re
class _Any:
    def __init__(s,*a,**k): pass
    def __getattr__(s,n): return _Any()
    def __call__(s,*a,**k): return _Any()
    def __or__(s,o): return _Any()
    __ror__ = __or__
    def __getitem__(s,k): return _Any()
    def __ge__(s,o): return False
    __gt__ = __lt__ = __le__ = __ge__
    def __bool__(s): return False
    def __int__(s): return 0
    def __float__(s): return 0.0
    def __sub__(s,o): return 0.0
    __rsub__ = __add__ = __radd__ = __mul__ = __rmul__ = __truediv__ = __sub__
    def __index__(s): return 0
    def __len__(s): return 0
    def __iter__(s): return iter(())
class _Meta(type):
    def __getattr__(c,n):
        if n.startswith("__"): raise AttributeError(n)
        return _Any()
class _Stub(types.ModuleType):
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        v = _Meta(n, (_Any,), {}); setattr(self, n, v); return v
for m in ["PySide6","PySide6.QtCore","PySide6.QtGui","PySide6.QtPrintSupport","PySide6.QtWidgets"]: sys.modules[m] = _Stub(m)
os.environ["FACTORDESK_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main, sms_panel as sp
from types import SimpleNamespace as NS
import barcodes
from types import SimpleNamespace as NS

import json, sqlite3, time
import auth, netdb
from netdb import DbServer, REMOTE
main.init_db(); H = main._auth_H()
auth.create_user(H, "admin", "مدیر", "admin1234", "admin", [])
auth.create_user(H, "sara", "فروشنده", "sara1234", "seller", ["edit"])
with main.db() as con: cid = con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')").lastrowid
from contextlib import contextmanager
@contextmanager
def sdb():                                                      # اتصال مستقیمِ «سمت سرور» (در تولید، سرور و کلاینت دو برنامه‌ی جدا هستند)
    c = sqlite3.connect(main.DB_PATH, timeout=15); c.row_factory = sqlite3.Row
    try: yield c; c.commit()
    except Exception: c.rollback(); raise
    finally: c.close()
Hs = NS(db=sdb)
srv = DbServer(main.DB_PATH, lambda u, p: auth.verify_login(Hs, u, p), lambda: auth.count_users(Hs), host="127.0.0.1", port=0, backup_dir=str(main.BACKUP_DIR))
srv.start(); direct = main.db                                  # direct = اتصال مستقیم فقط برای «بازرسی» نتیجه روی سرور
main.NET_MODE = "client"; REMOTE.configure("127.0.0.1", srv.port)
auth.verify_login_orig = auth.verify_login
import net_ui                                                   # (Qt شبیه‌سازی‌شده) فقط تابع‌های منطقی استفاده می‌شوند
u = REMOTE.login("sara", "sara1234"); assert u["role"] == "seller" and "hash" not in u and "salt" not in u
sess = auth.Session(dict(u, perms=json.dumps(u["perms"]))); assert sess.can("edit") and not sess.can("delete") and not sess.is_admin()
print("ورود از طریق سرور (بدون هش/نمک در پاسخ): OK")

# پایه‌ی خود برنامه (db()/get_pref/set_pref) از مسیر شبکه
main.set_pref("default_kind", "نقدی"); assert main.get_pref("default_kind", "") == "نقدی"
with sqlite3.connect(main.DB_PATH) as c: assert c.execute("SELECT value FROM settings WHERE key='pref_default_kind'").fetchone()[0] == "نقدی"
with main.db() as con: r = con.execute("SELECT name, id FROM customers WHERE id=?", (cid,)).fetchone(); assert r["name"] == "علی" and r[1] == cid and dict(r)["id"] == cid
print("db()/get_pref/set_pref و دسترسی ستونی روی RemoteConnection: OK")

# ثبت فاکتور واقعی برنامه (save_validate) از کلاینت + شماره‌ی تکراری هم‌زمان ⇒ شماره‌ی آزاد بعدی
class It:
    def __init__(s, t): s.t = t
    def text(s): return s.t
    def data(s, *_): return None
class Tbl:
    def __init__(s, rows): s.rows = rows
    def rowCount(s): return len(s.rows)
    def item(s, r, c): return It(s.rows[r][c]) if c < len(s.rows[r]) else None
main.need_date = lambda *a, **k: "2026-10-24"; main.warn = lambda *a, **k: print("WARN", a[1:3]); main.ask = lambda *a, **k: True
notes = []; main.info = lambda *a, **k: notes.append(a[2])
main.AUTH = auth.Session(None)
def mkdlg(no):
    d = NS(customer=NS(currentData=lambda: cid), inv_date=None, due=None, pf=False, kind=NS(currentText=lambda: "اعتباری"), items=Tbl([("لنت", "", "2", "1000", "", "0")]),
           discount=NS(value=lambda: 0.0), initial_paid=NS(value=lambda: 0.0), description=NS(text=lambda: ""), notes=NS(text=lambda: ""),
           invoice_id=None, mode="invoice", accept=lambda: None, _loading=False)
    d.no = NS(text=lambda: no, setText=lambda t: None); return d
d1 = mkdlg("3001"); main.InvoiceDialog.save_validate(d1); assert d1.final_no == "3001", getattr(d1, "final_no", None)
d2 = mkdlg("3001"); main.InvoiceDialog.save_validate(d2); assert d2.final_no != "3001" and notes, (d2.final_no, notes)     # دومی خودکار شماره‌ی آزاد گرفت و کاربر مطلع شد
with sqlite3.connect(main.DB_PATH) as c:
    nos = [x[0] for x in c.execute("SELECT invoice_no FROM invoices")]; items = c.execute("SELECT COUNT(*) FROM invoice_items").fetchone()[0]
assert sorted(nos) == sorted(set(nos)) and len(nos) == 2 and items == 2, (nos, items)
print("ثبت فاکتور واقعی از کلاینت + شماره‌ی تکراری هم‌زمان:", nos, "OK")

# ویرایش هم‌زمان یک سند: نفر دوم بعد از تغییر نفر اول نمی‌تواند بی‌خبر رویش بنویسد
iid = d1.saved_id
with main.db() as con: fp = main._doc_fp(con, iid)
with sqlite3.connect(main.DB_PATH) as c: c.execute("UPDATE invoices SET notes='تغییر نفر اول' WHERE id=?", (iid,))
e = mkdlg("3001"); e.invoice_id = iid; e._fp = fp; e.mode = "edit"; warns = []; main.warn = lambda *a, **k: warns.append(a[1]); main.InvoiceDialog.save_validate(e)
assert warns == ["ویرایش هم‌زمان"], warns
print("جلوگیری از بازنویسی خاموش سند: OK")

# مجوز سمت سرور با کدهای خود برنامه
try: auth.delete_user(H, 1); raise SystemExit("فروشنده نباید بتواند کاربر حذف کند")
except sqlite3.DatabaseError: pass
try:
    with main.db() as con: con.execute("DELETE FROM invoices WHERE id=?", (iid,))
    raise SystemExit("فروشنده بدون مجوز حذف، نباید فاکتور حذف کند")
except sqlite3.DatabaseError: pass
REMOTE.login("admin", "admin1234"); assert len(auth.list_users(H)) == 2
with main.db() as con: con.execute("DELETE FROM invoice_items WHERE invoice_id=?", (iid,)); con.execute("DELETE FROM invoices WHERE id=?", (iid,))
print("مجوزهای سمت سرور با کد واقعی برنامه: OK")

# ── خرید از مسیر شبکه: مجوز «خرید» سمت سرور + شماره‌گذاری هم‌زمان بدون تکرار ──
import purchases as P, threading
REMOTE.login("sara", "sara1234")
try:
    with main.db() as con: con.execute("INSERT INTO suppliers(name,created_at) VALUES('ممنوع','x')")
    raise SystemExit("فروشنده بدون مجوز خرید نباید بتواند تأمین‌کننده بسازد")
except sqlite3.DatabaseError: pass
REMOTE.login("admin", "admin1234")
with main.db() as con: sid = con.execute("INSERT INTO suppliers(name,created_at) VALUES('الف','x')").lastrowid
errs = []
def buy(n):
    for k in range(n):
        for _ in range(12):
            try:
                with main.db() as con: P.save_doc(con, None, "purchase", sid, "2026-10-24", "", "نقدی", "", "", 0, 0, "", [("لنت", "", 1, 100.0)])
                break
            except (sqlite3.OperationalError, sqlite3.IntegrityError): time.sleep(0.05)
            except Exception as e: errs.append(repr(e)); break
ts = [threading.Thread(target=buy, args=(10,)) for _ in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
with sqlite3.connect(main.DB_PATH) as c: nos = [r[0] for r in c.execute("SELECT doc_no FROM purchases WHERE doc_type='purchase'")]
assert not errs, errs; assert len(nos) == len(set(nos)) == 30, (len(nos), len(set(nos)))
print("خرید از مسیر شبکه: مجوز سمت سرور و ۳۰ ثبت هم‌زمان با شماره‌های یکتا: OK")
srv.stop(); print("APP OVER NETWORK OK")
