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
main.init_db()
with main.db() as con:
    cid = con.execute("INSERT INTO customers(name,phone,created_at) VALUES('علی رضایی','09121234567','x')").lastrowid
    iid = con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at,due_date) VALUES('1001',?,'2026-10-10','نسیه','x','2026-11-10')", (cid,)).lastrowid
    con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,quantity,unit_price) VALUES(?,1,'لنت',10,1000)", (iid,))
    pid = con.execute("INSERT INTO payments(customer_id,invoice_id,payment_date,amount,method,reference,discount) VALUES(?,?,?,?,?,?,?)", (cid,iid,'2026-10-12',800,'نقدی','777',200)).lastrowid
    chid = con.execute("INSERT INTO cheques(customer_id,invoice_id,cheque_no,bank,issuer,amount,due_date,status) VALUES(?,?,?,?,?,?,?,?)", (cid,iid,'5566','ملت','علی',3000,'2026-12-01','در انتظار وصول')).lastrowid
    rid, _ = main.retdb.save_return(con, None, cid, iid, '2026-10-13', '', [('لنت','',2,1000)])
    # قالب‌های آماده برای سه نوع جدید ساخته شده‌اند
    kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM sms_templates")}
    print("template kinds:", sorted(kinds)); assert {"payment","cheque","return"} <= kinds
    lk = [r[0] for r in con.execute("SELECT kind FROM sms_templates WHERE mode='lookup'")]; assert {"payment","cheque","return"} <= set(lk)

from types import SimpleNamespace
H = SimpleNamespace(db=main.db, customer_figures=main.customer_figures, invoice_rows=main.invoice_rows, money=main.money, strip_bidi=main.strip_bidi,
                    iso_to_j=main.iso_to_j, today_j=main.today_j, today_iso=main.today_iso, get_pref=main.get_pref, set_pref=main.set_pref,
                    load_style=main.load_style, match=main.match_words)
panel = sp.SmsPanel.__new__(sp.SmsPanel); panel.H = H
class Period:
    def currentData(s): return 0
panel.period = Period()
with H.db() as con:
    figs = H.customer_figures(con)
    last = {}
    open_by = {}
    for kind, q, n in ((sp.KIND_PAYMENT, "", 1), (sp.KIND_CHEQUE, "", 1), (sp.KIND_RETURN, "", 1),
                       (sp.KIND_PAYMENT, "رضایی علی", 1), (sp.KIND_CHEQUE, "رضایی ملت", 1), (sp.KIND_RETURN, "رضایی 1001", 1), (sp.KIND_PAYMENT, "نامربوط", 0)):
        rows, heads = panel._doc_rows(kind, figs, open_by, {}, last, q)
        print(kind, repr(q), len(rows), heads[:4]); assert len(rows) == n, (kind, q, len(rows))
        if rows: assert len(rows[0]["cells"]) + 2 == len(heads), (kind, len(rows[0]["cells"]), len(heads))
        if rows: assert rows[0]["phone"] == "09121234567"
    # همه‌ی قالب‌های پیش‌فرض با متغیرهای نمونه کامل پر می‌شوند (بدون {متغیر} باقی‌مانده)
    ctx = panel._sample_ctx() if False else sp.SmsPanel._sample_ctx(panel)
    for k, title, body, _ in sp.DEFAULT_TEMPLATES + sp.DEFAULT_TEMPLATES_V2:
        out = sp.render(body, ctx); assert not re.search(r"\{[a-z_]+\}", out), (title, out)
    for k, title, body, mp in sp.LOOKUP_TEMPLATES + sp.LOOKUP_TEMPLATES_V2:
        toks = sp.build_tokens(mp, ctx); out = sp.render_tokens(body, toks); assert "%token" not in out, (title, out)
    # متن واقعی پرداخت با تخفیف
    rows, _ = panel._doc_rows(sp.KIND_PAYMENT, figs, open_by, {}, last, "")
    print(sp.render(sp.DEFAULT_TEMPLATES_V2[0][2], rows[0]["ctx"]))
    rows, _ = panel._doc_rows(sp.KIND_RETURN, figs, open_by, {}, last, "")
    print(sp.render(sp.DEFAULT_TEMPLATES_V2[5][2], rows[0]["ctx"]))
print("SMS KINDS OK")

# ── سازنده‌ی فرم‌ها بدون خطا اجرا می‌شود (چیدمان جدید تاریخ/سررسید) ──
import collections
main.invoice_rows = lambda con, cid=None: []
main.customer_figures = lambda con: collections.defaultdict(lambda: dict(balance=0.0, total=0.0, received=0.0, disc=0.0, returns=0.0))
calls = [("InvoiceDialog", lambda: main.InvoiceDialog(None)), ("InvoiceDialog proforma", lambda: main.InvoiceDialog(None, mode="proforma")),
         ("PaymentDialog", lambda: main.PaymentDialog(None)), ("ChequeDialog", lambda: main.ChequeDialog(None)), ("ReturnDialog", lambda: main.ReturnDialog(None))]
for name, fn in calls:
    try: fn(); print(name, "ok")
    except Exception as e: print("FAIL", name, type(e).__name__, e); raise
print("DIALOGS OK")
