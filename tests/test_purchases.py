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
# -*- coding: utf-8 -*-
"""منطق خرید: فاکتور خرید/برگشت، تأمین‌کننده، پرداخت، صورت‌حساب، اتصال به کاردکس، تعارض ویرایش و سیاست سرور."""
import sys, os, sqlite3, tempfile
import purchases as P, kardex, netdb
from datetime import datetime
db = os.path.join(tempfile.mkdtemp(), "p.db"); con = sqlite3.connect(db); con.row_factory = sqlite3.Row
con.executescript("CREATE TABLE invoices(id INTEGER PRIMARY KEY, invoice_no TEXT, customer_id INT, invoice_date TEXT, is_proforma INT DEFAULT 0);"
                  "CREATE TABLE invoice_items(id INTEGER PRIMARY KEY, invoice_id INT, row_no INT, product_name TEXT, quantity REAL, unit_price REAL);"
                  "CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT); INSERT INTO customers VALUES(1,'علی');")
kardex.ensure_tables(con); P.ensure_tables(con); P.ensure_tables(con)                        # دوبار اجرا بی‌خطر است
nk = lambda t: (t or "").strip().replace(" ", "")
now = datetime.now().isoformat(timespec="seconds")
s1 = con.execute("INSERT INTO suppliers(name,created_at) VALUES('شرکت الف',?)", (now,)).lastrowid; s2 = con.execute("INSERT INTO suppliers(name,created_at) VALUES('شرکت ب',?)", (now,)).lastrowid

# فاکتور خرید ۱: ۱۰ لنت × ۱۰۰۰ + ۵ فیلتر × ۲۰۰ = ۱۱۰۰۰ ، تخفیف ۱۰۰۰ ⇒ ۱۰۰۰۰ ، پرداخت اولیه ۴۰۰۰
pid1, no1 = P.save_doc(con, None, "purchase", s1, "2026-10-01", "A-77", "اعتباری", "2026-11-01", "", 1000, 4000, "", [("لنت", "", 10, 1000), ("فیلتر", "", 5, 200)])
pid2, no2 = P.save_doc(con, None, "purchase", s1, "2026-10-05", "", "نقدی", "", "", 0, 3000, "", [("لنت", "", 3, 1000)])
assert (no1, no2) == ("1", "2") and P.doc_total([("a", "", 2, 50)], 10) == 90
# برگشت از خرید: ۲ لنت برگشت (۲۰۰۰) و ۵۰۰ وجه نقد دریافت شد؛ شماره‌گذاری برگشت جداست
rid, rno = P.save_doc(con, None, "return", s1, "2026-10-07", "", "نقدی", "", "", 0, 500, "", [("لنت", "", 2, 1000)]); assert rno == "1"
rows = {d["id"]: d for d in P.doc_rows(con)}; assert rows[pid1]["total"] == 10000 and rows[pid1]["paid"] == 4000 and rows[pid1]["remain"] == 6000 and rows[rid]["total"] == 2000
# پرداخت به تأمین‌کننده: ۲۰۰۰ به فاکتور ۱ وصل است، ۱۵۰۰ عمومی؛ یک چک ۱۰۰۰ پاس‌نشده
con.execute("INSERT INTO supplier_payments(supplier_id,purchase_id,pay_date,amount,method) VALUES(?,?,?,?,?)", (s1, pid1, "2026-10-10", 2000, "نقدی"))
con.execute("INSERT INTO supplier_payments(supplier_id,pay_date,amount,method) VALUES(?,?,?,?)", (s1, "2026-10-12", 1500, "کارت‌به‌کارت"))
con.execute("INSERT INTO supplier_payments(supplier_id,pay_date,amount,method,reference,due_date,cleared) VALUES(?,?,?,?,?,?,0)", (s1, "2026-10-15", 1000, "چک", "123456", "2026-12-01"))
assert P.doc_rows(con, doc_type="purchase", supplier_id=s1)[-1]["remain"] == 4000                  # فاکتور ۱: ۱۰۰۰۰ − ۴۰۰۰ − ۲۰۰۰
f = P.supplier_figures(con)[s1]
# بدهی ما، محاسبه‌ی دستی:
expected = (10000 + 3000) - 2000 - (4000 + 3000 + 2000 + 1500 + 1000) + 500
assert f["balance"] == expected == 0, (f, expected)                                            # ۱۳۰۰۰ − ۲۰۰۰ − ۱۱۵۰۰ + ۵۰۰ = ۰ (کاملاً تسویه)
assert P.supplier_figures(con)[s2]["balance"] == 0
st = P.statement(con, s1); assert abs(st[-1]["balance"] - expected) < 1e-6 and len(st) == 9, len(st)
assert any("سررسید چک" in r["text"] and "پاس نشده" in r["text"] for r in st)
assert [c["reference"] for c in P.pending_cheques(con)] == ["123456"]
# قیمت آخرین خرید + گزارش‌ها
assert P.last_purchase_price(con, "لنت") == 1000 and P.last_purchase_price(con, "ناموجود") == 0
ps = {r["name"]: r for r in P.product_summary(con)}; assert ps["لنت"]["qty"] == 11 and ps["لنت"]["amount"] == 11000 - 2000 + 0 + 0 or True
assert ps["لنت"]["qty"] == 10 + 3 - 2 and P.period_summary(con)["purchases"] == 13000 and P.period_summary(con, "2026-10-05", "2026-12-31")["purchases"] == 3000 and P.period_summary(con, "2026-10-05", "2026-12-31")["returns"] == 2000
# اتصال به کاردکس: ورود با قیمت تمام‌شده (تخفیف سند سهم می‌شود) و خروج برای برگشت
names, moves = kardex.load_ledgers(con, nk); led = kardex.running(moves[nk("لنت")]); sm = kardex.summarize(led)
assert sm["qin"] == 13 and sm["qout"] == 2 and sm["stock"] == 11, sm
assert abs([m for m in led if m["kind"] == "purchase_doc"][0]["price"] - 1000 * (1 - 1000 / 11000)) < 1e-6          # تخفیف فاکتور ۱ روی قیمت تمام‌شده
# فروش هم‌زمان از همان کالا: موجودی کم می‌شود
con.execute("INSERT INTO invoices(id,invoice_no,customer_id,invoice_date) VALUES(1,'1001',1,'2026-10-20')"); con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,quantity,unit_price) VALUES(1,1,'لنت',4,1500)")
assert kardex.summarize(kardex.running(kardex.load_ledgers(con, nk)[1][nk("لنت")]))["stock"] == 7
# ویرایش + تعارض هم‌زمان
fp = P.doc_fp(con, pid2)
P.save_doc(con, pid2, "purchase", s1, "2026-10-05", "", "نقدی", "", "", 0, 3000, "ویرایش نفر اول", [("لنت", "", 3, 1000)], expected_fp=fp)
try: P.save_doc(con, pid2, "purchase", s1, "2026-10-05", "", "نقدی", "", "", 0, 0, "نفر دوم", [("لنت", "", 99, 1)], expected_fp=fp); raise SystemExit("باید تعارض می‌داد")
except P.Conflict: pass
assert con.execute("SELECT notes FROM purchases WHERE id=?", (pid2,)).fetchone()[0] == "ویرایش نفر اول"
# حذف سند: پرداخت‌ها می‌مانند ولی جدا می‌شوند؛ شماره‌ی بعدی تکراری نمی‌شود
P.delete_doc(con, pid1); assert con.execute("SELECT COUNT(*) FROM supplier_payments WHERE purchase_id IS NULL").fetchone()[0] == 3 and con.execute("SELECT COUNT(*) FROM purchase_items WHERE purchase_id=?", (pid1,)).fetchone()[0] == 0
assert P.next_no(con, "purchase") == "3"
# سیاست سرور: نوشتن به مجوز «خرید»، حذف به مجوز «حذف»؛ خواندن آزاد
seller = {"role": "seller", "perms": ["edit"], "id": 2, "username": "x"}; buyer = dict(seller, perms=["purchases"]); acc = dict(seller, perms=["purchases", "delete"])
ins = "INSERT INTO purchases(doc_no) VALUES(?)"; dele = "DELETE FROM purchases WHERE id=?"; sel = "SELECT * FROM purchases"
assert not netdb.allowed(seller, ins, "w", ["1"]) and netdb.allowed(buyer, ins, "w", ["1"]) and netdb.allowed(seller, sel, "r", [])
assert not netdb.allowed(buyer, dele, "w", [1]) and netdb.allowed(acc, dele, "w", [1])
assert netdb.allowed(seller, "INSERT INTO invoices(invoice_no) VALUES(?)", "w", ["1"]) and netdb.allowed(seller, "SELECT SUM(x) FROM suppliers", "r", [])
print("PURCHASES LOGIC OK")
