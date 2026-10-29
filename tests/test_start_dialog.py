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
main.init_db()
with main.db() as con: cid = con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')").lastrowid
StartInv = main.StartDocDialog(None, False); StartPf = main.StartDocDialog(None, True)          # ساخته می‌شوند (مسیر کد پنجره)
d = main.StartDocDialog.__new__(main.StartDocDialog); d.pf = False; d.customer = NS(currentData=lambda: cid); d.kind = NS(currentText=lambda: "اعتباری")
assert main.StartDocDialog.result_values(d) == (cid, "اعتباری"); d.pf = True; assert main.StartDocDialog.result_values(d) == (cid, None)
warns = []; main.warn = lambda *a, **k: warns.append(a[1]); d.customer = NS(currentData=lambda: None); d.pf = False
try: main.StartDocDialog.accept(d)
except Exception: pass
assert warns == ["مشتری"]                                                                      # بدون مشتری اجازه‌ی ادامه نمی‌دهد
P = NS(_voice_customer=None, refresh_all=lambda: None)
main.InvoiceDialog(P, start=(cid, "اعتباری")); main.InvoiceDialog(P, mode="proforma", start=(cid, None)); main.InvoiceDialog(P)   # با/بدون start
print("START DIALOG OK")
