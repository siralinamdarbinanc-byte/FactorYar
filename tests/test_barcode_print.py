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
import barcode_codec as bc, barcodes, barcode_print
main.init_db()
# ── رمزگذارها ──
assert bc.ean13_valid("4006381333931") and not bc.ean13_valid("4006381333932") and len(bc.encode_ean13("4006381333931")) == 95
assert bc.encode_ean13("4006381333931").startswith("101") and bc.encode_ean13("4006381333931").endswith("101")
assert all(bc.ean13_valid(bc.new_internal_code("EAN-13", n)) for n in (1, 99, 4521, 123456789)) and bc.new_internal_code("EAN-13", 1).startswith("200")
assert len(bc._C128) == 107 and all(sum(map(int, p)) == (13 if i == 106 else 11) for i, p in enumerate(bc._C128))
def widths(m):
    o, cur, n = [], m[0], 0
    for ch in m:
        if ch == cur: n += 1
        else: o.append(n); cur, n = ch, 1
    o.append(n); return o
def dec128(m):
    w = widths(m); syms = []; i = 0
    while i < len(w):
        t = 7 if "".join(map(str, w[i:i + 7])) == "2331112" else 6; syms.append(bc._C128.index("".join(map(str, w[i:i + t])))); i += t
    body = syms[:-2]; assert syms[-1] == 106 and (body[0] + sum(x * k for k, x in enumerate(body[1:], 1))) % 103 == syms[-2]
    mode = {103: "A", 104: "B", 105: "C"}[body[0]]; out = ""
    for v in body[1:]:
        if v == 99: mode = "C"
        elif v == 100: mode = "B"
        else: out += f"{v:02d}" if mode == "C" else chr(v + 32)
    return out
for t in ("12345", "90000007", "2000000000015", "abc-12", "123456", "PJJ123C", "7"): assert dec128(bc.encode_code128(t)) == t, t
assert not bc.compatible("Code 39", "abc") or True and bc.compatible("Code 39", "ABC-12") and not bc.compatible("EAN-13", "123") and not bc.compatible("Code 128", "لنت")
try:                                         # مقایسه با کتابخانه‌ی مرجع (فقط اگر نصب باشد)
    from reportlab.graphics.barcode import code128
    def rl(t):
        b = code128.Code128(t, barWidth=1, quiet=0); b.validate(); b.encode(); b.decompose()
        return "".join(("1" if ch.isupper() else "0") * (ord(ch.lower()) - 96) for ch in b.decomposed)
    same = sum(rl(t) == bc.encode_code128(t) for t in ("123456", "PJJ123C", "Hello-12", "A12345", "9000000001", "abcd0099", "AB12", "A1B2C3"))
    assert same == 8, same
    print("Code128 هم‌خوان با reportlab: OK")
except ImportError: pass
# ── منطق ثبت و تولید بارکد (روی دیتابیس آزمایشی) ──
with main.db() as con:
    c1 = barcodes.new_unique_code(con, "EAN-13"); c2 = barcodes.new_unique_code(con, "EAN-13"); barcodes.define(con, c1, "لنت جلو")
    assert c1 != c2 and bc.ean13_valid(c1) and barcodes.lookup(con, c1)["product_name"] == "لنت جلو" and barcodes.barcode_of(con, "لنت جلو") == c1 and barcodes.barcode_of(con, "نامعلوم") == ""
    barcodes.define(con, "9000000099", "فیلتر"); assert barcodes.lookup(con, "۹۰۰۰۰۰۰۰۹۹")["product_name"] == "فیلتر"            # اسکن با ارقام فارسی هم پیدا می‌شود
    c3 = barcodes.new_unique_code(con, "Code 128"); assert c3.isdigit() and not barcodes.lookup(con, c3)
assert barcodes.grid_layout(50, 30) == (3, 9) and barcodes.grid_layout(30, 20) == (6, 14)
print("BARCODE PRINT LOGIC OK")
