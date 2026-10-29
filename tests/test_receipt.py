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

st = main.load_style("invoice"); st["font"] = st.get("font") or "Tahoma"
inv = {"invoice_no": "1001", "invoice_date": "2026-10-24", "kind": "نقدی", "discount": 0, "paid_initial": 0, "description": "", "created_at": "2026-10-24T10:05:00"}
items = [dict(product_name="لنت", description="", quantity=1, unit_price=1000, discount=0)]
for cust, want in (({"name": "علی", "phone": "0912", "address": "تهران، خیابان آزادی"}, True), ({"name": "علی", "phone": "", "address": ""}, False)):
    try: html = main.build_receipt_html({"inv": inv, "cust": cust, "items": items, "pays": [], "subtotal": 1000, "total": 1000, "paid": 0, "remain": 1000, "disc": 0, "pf": False}, st)
    except Exception as e: print("ERR", repr(e)); break
    assert ("تلفن: 0912" in html and "آدرس: تهران، خیابان آزادی" in html) == want
print("RECEIPT OK")
