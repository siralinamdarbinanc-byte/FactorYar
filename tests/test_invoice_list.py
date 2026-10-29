# -*- coding: utf-8 -*-
"""فهرست فاکتورها: پیش‌فرض فقط امروز؛ روزهای دیگر با انتخاب تاریخ؛ جست‌وجو در همه‌ی روزها. (با Qt شبیه‌سازی‌شده)"""
import sys, os, types, tempfile
from datetime import date, timedelta
class _Any:
    def __init__(s,*a,**k): pass
    def __getattr__(s,n): return _Any()
    def __call__(s,*a,**k): return _Any()
    def __or__(s,o): return _Any()
    __ror__ = __or__
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
import main

class Combo:
    def __init__(s, first): s.items=[(first,0)]; s.idx=0
    def addItem(s,t,d=None): s.items.append((t,d))
    def clear(s): s.items=[]; s.idx=-1
    def currentData(s): return s.items[s.idx][1] if 0<=s.idx<len(s.items) else None
    def findData(s,v):
        for i,(t,d) in enumerate(s.items):
            if d==v: return i
        return -1
    def setCurrentIndex(s,i): s.idx=i
    def blockSignals(s,b): pass
class Edit:
    def __init__(s,t=""): s.t=t
    def text(s): return s.t
class Table:
    def __init__(s): s.rows=[]
    def setRowCount(s,n): s.rows=[]
    def rowCount(s): return len(s.rows)
    def insertRow(s,r): s.rows.append({})
    def setItem(s,r,c,it): s.rows[r][c]=getattr(it,"t",None)
    def item(s,r,c): return _Any()
class Lbl:
    def setText(s,t): s.t=t
class Win: pass

main.init_db()
main.table_item = lambda text, align=None: types.SimpleNamespace(t=str(text), setForeground=lambda *a: None, setFont=lambda *a: None)
today = date.today(); y = today - timedelta(days=1); old = today - timedelta(days=40)
def mk(no, d, created):
    with main.db() as con:
        cid = con.execute("SELECT id FROM customers WHERE name='علی'").fetchone()
        cid = cid[0] if cid else con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')").lastrowid
        i = con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at) VALUES(?,?,?,?,?)", (no, cid, d.isoformat(), "نقدی", created)).lastrowid
        con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,quantity,unit_price) VALUES(?,1,'x',1,10)", (i,))
now = today.isoformat() + "T10:00:00"
mk("T1", today, now); mk("Y1", y, y.isoformat()+"T10:00:00"); mk("Y2", y, now); mk("O1", old, old.isoformat()+"T10:00:00")

def nos(w):
    w.inv_table.rows = []; main.FactorDesk.refresh_invoices(w); return sorted(r[1] if False else r.get(1) for r in w.inv_table.rows)
def fresh(q=""):
    w = Win(); w.inv_search = Edit(q); w.inv_year = Combo("all"); w.inv_month = Combo("all"); w.inv_day = Combo("all")
    for i in range(1,13): w.inv_month.addItem(str(i), i)
    for i in range(1,32): w.inv_day.addItem(str(i), i)
    ty, tm, td = [int(x) for x in main.iso_to_j(main.today_iso()).split("/")]
    w._inv_year_init = ty; w.inv_month.setCurrentIndex(tm); w.inv_day.setCurrentIndex(td)
    w.inv_table = Table(); w.overdue_lbl = Lbl(); return w

w = fresh(); got = nos(w); print("today view:", got); assert got == ["T1", "Y2"], got        # امروز + فاکتورِ ثبت‌شده‌ی امروز با تاریخ دیروز
# انتخاب دیروز
yj = [int(x) for x in main.iso_to_j(y.isoformat()).split("/")]
w.inv_year.setCurrentIndex(w.inv_year.findData(yj[0])); w.inv_month.setCurrentIndex(yj[1]); w.inv_day.setCurrentIndex(yj[2])
got = nos(w); print("yesterday view:", got); assert got == ["Y1", "Y2"], got
# همه
for c in (w.inv_year, w.inv_month, w.inv_day): c.setCurrentIndex(0)
got = nos(w); print("all:", got); assert got == ["O1", "T1", "Y1", "Y2"], got
# جست‌وجو در همه‌ی روزها در حالی که فیلتر روی امروز است
w2 = fresh("O1"); got = nos(w2); print("search:", got); assert got == ["O1"], got
print("INVOICE LIST OK")

# ── مانده‌ی کل مشتری در فرم فاکتور ──
class Lbl2:
    def setText(s,t): s.t=t
    def setStyleSheet(s,t): s.css=t
class Cust:
    def __init__(s,cid): s.cid=cid
    def currentData(s): return s.cid
with main.db() as con: cid = con.execute("SELECT id FROM customers WHERE name='علی'").fetchone()[0]
d = Win(); d.customer = Cust(cid); d.cust_bal = Lbl2(); d.invoice_id = None; d.mode = "invoice"
main.InvoiceDialog._show_balance(d); print(d.cust_bal.t); assert "بدهکار" in d.cust_bal.t and "بدون احتساب" in d.cust_bal.t
with main.db() as con: iid = con.execute("SELECT id FROM invoices WHERE invoice_no='T1'").fetchone()[0]
d.invoice_id = iid; main.InvoiceDialog._show_balance(d); print(d.cust_bal.t); assert "شامل همین فاکتور" in d.cust_bal.t
d.customer = Cust(None); main.InvoiceDialog._show_balance(d); assert d.cust_bal.t == ""
print("BALANCE LABEL OK")
