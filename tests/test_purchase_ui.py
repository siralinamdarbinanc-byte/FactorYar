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
import purchases as P, purchase_ui as U
main.init_db(); main.AUTH = main.auth.Session(None)
with main.db() as con:
    s1 = con.execute("INSERT INTO suppliers(name,phone,created_at) VALUES('الف','0912','x')").lastrowid
    pid, no = P.save_doc(con, None, "purchase", s1, "2026-10-01", "A1", "اعتباری", "", "", 0, 100, "", [("لنت", "", 2, 1000.0)])
warns, audits = [], []
ctx = NS(db=main.db, JalaliDateEdit=main.JalaliDateEdit, need_date=lambda *a, **k: "2026-10-24", make_searchable_combo=main.make_searchable_combo, bbox=main.bbox, money=main.money,
         iso_to_j=main.iso_to_j, parse_num=main.parse_num, fmt_qty=main.fmt_qty, warn=lambda *a, **k: warns.append(a[1]), info=main.info, ask=lambda *a, **k: True, ask_reason=lambda *a, **k: "تست",
         table_item=main.table_item, money_spin=main.money_spin, with_words=main.with_words, today_iso=main.today_iso, audit=lambda a, d="": audits.append(a), can=lambda p: True,
         BarcodeSniffer=main._BarcodeSniffer, export_table=lambda *a: None, show_preview=lambda *a, **k: None, norm_key=main.norm_key, now_iso=lambda: "2026-10-24T10:00:00")
U.bind(ctx)
# ساخت همه‌ی پنجره‌ها و تب‌ها با داده‌ی واقعی (مسیر کد بارگذاری/نمایش)
pp = U.PurchasesPanel(); sp = U.SuppliersPanel(); pp.refresh(); sp.refresh()
assert len(pp.rows) == 1 and pp.rows[0]["total"] == 2000 and sp.ids == [s1]
U.PurchaseDialog(None, pid); U.PurchaseDialog(None, None, "return", s1); U.SupplierDialog(None, s1); U.SupplierPaymentDialog(None, s1); U.StatementDialog(None, s1); U.ChequesPayableDialog(None)
U.print_doc(None, pid)
# مسیر ذخیره‌ی فاکتور خرید با خود کد برنامه (با ویجت‌های جایگزین)
d = U.PurchaseDialog.__new__(U.PurchaseDialog)
d.__dict__.update(dict(sup=NS(currentData=lambda: s1, currentText=lambda: "الف"), date=None, ref=NS(text=lambda: "B2"), desc=NS(text=lambda: ""), notes=NS(text=lambda: ""),
    discount=NS(value=lambda: 100.0), paid=NS(value=lambda: 0.0), kind=NS(currentText=lambda: "اعتباری"), due=NS(iso=lambda: "2026-12-01"), ret=False, doc_type="purchase", pid=None, _fp=None,
    saved_id=None, print_after=False, _finish_accept=lambda: None, _rows=lambda: [("لنت", "", 2.0, 1000.0), ("فیلتر", "", 1.0, 300.0)]))
d._net = lambda: P.doc_total(d._rows(), 100.0); U.PurchaseDialog.save(d, True)
with main.db() as con: h, its = P.load_doc(con, d.saved_id)
assert h["doc_no"] == "2" and h["ref_no"] == "B2" and len(its) == 2 and h["kind"] == "اعتباری" and h["due_date"] == "2026-12-01" and d.print_after and "ثبت فاکتور خرید" in audits, (h and h["doc_no"], audits)
d.pid = d.saved_id; d._fp = "stale"; n0 = len(warns); U.PurchaseDialog.save(d, False)               # تعارض ویرایش هم‌زمان
assert warns[n0:] == ["ویرایش هم‌زمان"]
d.pid = None; d._fp = None; d.discount = NS(value=lambda: 99999.0); n0 = len(warns); U.PurchaseDialog.save(d, False); assert warns[n0:] == ["تخفیف"]    # تخفیف بیش از جمع
# کاردکس: خرید در موجودی انبار دیده می‌شود
import kardex
names, moves = kardex.load_ledgers(sqlite_con := main.connect_db(), main.norm_key); assert kardex.summarize(kardex.running(moves[main.norm_key("لنت")]))["stock"] == 4; sqlite_con.close()
print("PURCHASE UI OK")
