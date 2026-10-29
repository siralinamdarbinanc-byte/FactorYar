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
import sqlite3
main.init_db(); main.AUTH = main.auth.Session(None)
# ── ساعت ثبت: هر سندی که ساعت نداشت، با تریگر خودکار می‌گیرد ──
with main.db() as con:
    cid = con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')").lastrowid
    con.execute("INSERT INTO payments(customer_id,payment_date,amount,method) VALUES(?,?,?,?)", (cid, "2026-10-24", 100, "نقدی"))
    con.execute("INSERT INTO cheques(customer_id,cheque_no,amount,due_date) VALUES(?,?,?,?)", (cid, "1", 50, "2026-12-01"))
    con.execute("INSERT INTO suppliers(name,created_at) VALUES('الف','x')")
    con.execute("INSERT INTO supplier_payments(supplier_id,pay_date,amount,method) VALUES(1,'2026-10-24',10,'نقدی')")
    ts = [con.execute(f"SELECT created_at FROM {t}").fetchone()[0] for t in ("payments", "cheques", "supplier_payments")]
assert all(len(t) == 19 and t[10] == "T" for t in ts), ts
assert main.show_time("2026-10-24T14:35:09") == "14:35" and main.show_time("") == "" and main.show_time(None) == ""
main.ensure_time_columns(sqlite3.connect(main.DB_PATH)); main.init_db()                      # دوباره اجرا بی‌خطر است
# ── کالای بدون قیمت: فاکتور نهایی ممنوع؛ سند جدید → پیشنهاد پیش‌فاکتور؛ ویرایش فاکتور ثبت‌شده → فقط خطا ──
class It:
    def __init__(s, t): s.t = t
    def text(s): return s.t
    def data(s, *_): return None
class Tbl:
    def __init__(s, rows): s.rows = rows
    def rowCount(s): return len(s.rows)
    def item(s, r, c): return It(s.rows[r][c]) if c < len(s.rows[r]) else None
warns, asks = [], []
main.need_date = lambda *a, **k: "2026-10-24"; main.warn = lambda *a, **k: warns.append(a[1]); main.info = lambda *a, **k: None
def mk(rows, invoice_id=None, mode="invoice"):
    d = NS(customer=NS(currentData=lambda: cid), inv_date=None, due=None, pf=(mode == "proforma"), kind=NS(currentText=lambda: "اعتباری"), items=Tbl(rows), discount=NS(value=lambda: 0.0),
           initial_paid=NS(value=lambda: 0.0), description=NS(text=lambda: ""), notes=NS(text=lambda: ""), invoice_id=invoice_id, mode=mode, accept=lambda: None, _loading=False)
    d.no = NS(text=lambda: d._no, setText=lambda t: setattr(d, "_no", t)); d._no = "5001"; d.setWindowTitle = lambda t: None
    d._to_proforma = main.InvoiceDialog._to_proforma.__get__(d); return d
zero_rows = [("لنت", "", "2", "1000", "", "0"), ("فیلتر", "", "1", "0", "", "0")]
main.ask = lambda *a, **k: (asks.append(a[1]), False)[1]
d = mk(zero_rows); main.InvoiceDialog.save_validate(d); assert asks == ["کالای بدون قیمت"] and not hasattr(d, "final_no")           # انصراف: چیزی ثبت نمی‌شود
with main.db() as con: assert con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
main.ask = lambda *a, **k: True
d = mk(zero_rows); main.InvoiceDialog.save_validate(d)
assert d.final_no.startswith("P") and d.pf
with main.db() as con: r = con.execute("SELECT kind,is_proforma,paid_initial,due_date,created_at FROM invoices").fetchone(); assert tuple(r)[:4] == ("پیش‌فاکتور", 1, 0, "") and len(r[4]) == 19
d2 = mk([("لنت", "", "2", "1000", "", "0")]); main.InvoiceDialog.save_validate(d2); assert not d2.final_no.startswith("P")                    # با قیمت: فاکتور عادی
with main.db() as con: iid = con.execute("SELECT id FROM invoices WHERE invoice_no=?", (d2.final_no,)).fetchone()[0]
n0 = len(warns); d3 = mk(zero_rows, invoice_id=iid); main.InvoiceDialog.save_validate(d3); assert warns[n0:] == ["قیمت صفر"] and not hasattr(d3, "final_no")  # ویرایش فاکتور نهایی
d4 = mk(zero_rows, mode="proforma"); d4._no = "P9"; main.InvoiceDialog.save_validate(d4); assert d4.final_no.startswith("P")                                      # پیش‌فاکتور بدون قیمت آزاد است
print("TIME / ZERO-PRICE OK")
