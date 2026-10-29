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
main.init_db()
with main.db() as con:
    cid = con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')").lastrowid
    # ── بارکد ──
    assert barcodes.looks_like_barcode("۶۲۶۰۰۰۱۲۳۴") and not barcodes.looks_like_barcode("لنت") and not barcodes.looks_like_barcode("12")
    assert barcodes.lookup(con, "6260001234") is None
    barcodes.define(con, "۶۲۶۰۰۰۱۲۳۴", "لنت جلو", "پراید"); r = barcodes.lookup(con, "6260001234"); assert r["product_name"] == "لنت جلو"
    assert "لنت جلو" in barcodes.known_names(con)
# ── ثبت فاکتور با تخفیف ردیف + تخفیف اضافه ──
class It:
    def __init__(s, t): s.t = t
    def text(s): return s.t
    def data(s, *_): return None
class Tbl:
    def __init__(s, rows): s.rows = rows
    def rowCount(s): return len(s.rows)
    def item(s, r, c): return It(s.rows[r][c]) if c < len(s.rows[r]) else None
rows = [("لنت", "", "2", "1000", "", "100"), ("فیلتر", "", "1", "500", "", "0")]          # جمع 2500، تخفیف ردیف 100
main.need_date = lambda *a, **k: "2026-10-22"; main.warn = lambda *a, **k: print("WARN", a[1:3]); main.ask = lambda *a, **k: True
fake = NS(customer=NS(currentData=lambda: cid), inv_date=None, due=None, pf=False, kind=NS(currentText=lambda: "اعتباری"), items=Tbl(rows),
          discount=NS(value=lambda: 50.0), initial_paid=NS(value=lambda: 0.0), description=NS(text=lambda: ""), notes=NS(text=lambda: ""),
          no=NS(text=lambda: "2001"), invoice_id=None, mode="invoice", accept=lambda: None)
main.InvoiceDialog.save_validate(fake)
with main.db() as con:
    inv = con.execute("SELECT * FROM invoices WHERE invoice_no='2001'").fetchone(); its = con.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY row_no", (inv["id"],)).fetchall()
    print("saved discount", inv["discount"], [x["discount"] for x in its])
    assert inv["discount"] == 150 and [x["discount"] for x in its] == [100, 0]
    row = [r for r in main.invoice_rows(con, cid) if r["invoice_no"] == "2001"][0]; print("total", row["total"]); assert row["total"] == 2350     # 2500 - (100 + 50)
# ── بازخوانی: تخفیف اضافه = کل - تخفیف ردیف‌ها ──
added = []; state = {}
fake2 = NS(invoice_id=fake.invoice_id or None, mode="invoice", customer=NS(findData=lambda v: 0, setCurrentIndex=lambda i: None), description=NS(setText=lambda t: None),
           discount=NS(setValue=lambda v: state.__setitem__("extra", v)), initial_paid=NS(setValue=lambda v: None), notes=NS(setText=lambda t: None),
           no=NS(setText=lambda t: None), inv_date=NS(setIso=lambda v: None), kind=NS(setCurrentText=lambda t: None, currentText=lambda: "اعتباری"),
           due=NS(setIso=lambda v: None, setEnabled=lambda v: None), items=NS(setRowCount=lambda n: None), add_item_row=lambda v=None: added.append(v))
with main.db() as con: fake2.invoice_id = con.execute("SELECT id FROM invoices WHERE invoice_no='2001'").fetchone()[0]
main.InvoiceDialog.load_invoice(fake2, "2002")
print("loaded", added, state); assert state["extra"] == 50 and added[0][5] == "100"
# ── اسکن بارکد: شناخته‌شده خودکار اضافه می‌شود، تکراری تعداد را زیاد می‌کند، ناشناس تعریف می‌شود ──
class Cell:
    def __init__(s, t): s.t = t
    def text(s): return s.t
    def setText(s, t): s.t = t
class Grid:
    def __init__(s): s.rows = [{}]
    def rowCount(s): return len(s.rows)
    def item(s, r, c): return Cell(s.rows[r][c]) if c in s.rows[r] else None
    def blockSignals(s, b): pass
    def setItem(s, r, c, it): s.rows[r][c] = it.t
    def setCurrentCell(s, r, c): pass
    def scrollToItem(s, *a): pass
main.table_item = lambda t, *a, **k: Cell(str(t))
grid = Grid(); msgs = []
q = NS(items=grid, recalc=lambda: None, pm=NS(lookup=lambda n, c: {"price": 750.0, "description": "پراید"}), customer=NS(currentData=lambda: cid),
       add_item_row=lambda: grid.rows.append({}), _vmsg=msgs.append)
q._price_for = lambda name: (750.0, "پراید")
for n in ("_barcode_scanned", "_num"): setattr(q, n, getattr(main.InvoiceDialog, n).__get__(q))
q._define_barcode = lambda code: "ضربه‌گیر"                                   # کاربر نام را وارد می‌کند
q._barcode_scanned("۶۲۶۰۰۰۱۲۳۴")
print("known:", grid.rows); assert grid.rows[0][0] == "لنت جلو" and grid.rows[0][2] == "1" and grid.rows[0][3] == "750"
q._barcode_scanned("6260001234")
assert grid.rows[0][2] == "2" and len(grid.rows) == 1, grid.rows               # همان کالا: تعداد یکی زیاد می‌شود
q._barcode_scanned("9990001111")
print("new:", grid.rows); assert len(grid.rows) == 2 and grid.rows[1][0] == "ضربه‌گیر"
# ── نوع پرداخت: نقدی ← کل فاکتور؛ برگشت به اعتباری ← صفر؛ حالت بارگذاری دست نمی‌زند ──
class Spin:
    def __init__(s): s.v = 0.0; s.en = True
    def value(s): return s.v
    def setValue(s, v): s.v = float(v)
    def setEnabled(s, b): s.en = b
pay = NS(pf=False, initial_paid=Spin(), discount=NS(value=lambda: 0.0), _totals=lambda: (1000.0, 0.0), _loading=False, _prev_kind="اعتباری")
for n in ("_cash_kind_changed", "_kind_changed"): setattr(pay, n, getattr(main.InvoiceDialog, n).__get__(pay))
pay._kind_changed("نقدی"); assert pay.initial_paid.v == 1000 and not pay.initial_paid.en
pay._kind_changed("اعتباری"); assert pay.initial_paid.v == 0 and pay.initial_paid.en
pay.initial_paid.setValue(300); pay._kind_changed("چکی"); assert pay.initial_paid.v == 300       # بین انواع اعتباری مبلغ دستی حفظ می‌شود
pay._kind_changed("نقدی"); pay._loading = True; pay._kind_changed("اعتباری"); assert pay.initial_paid.v == 1000   # هنگام بارگذاری فاکتور ذخیره‌شده، مبلغ صفر نمی‌شود
print("INVOICE FORM LOGIC OK")
