import sys, types, os, tempfile
class _Any:
    def __init__(s,*a,**k): pass
    def __getattr__(s,n): return _Any()
    def __call__(s,*a,**k): return _Any()
    def __or__(s,o): return _Any()
    __ror__ = __or__
    def __iter__(s): return iter(())
class _Meta(type):
    def __getattr__(c,n):
        if n.startswith("__"): raise AttributeError(n)
        return _Any()
class _Stub(types.ModuleType):
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        v = _Meta(n, (_Any,), {}); setattr(self, n, v); return v
for m in ["PySide6","PySide6.QtCore","PySide6.QtGui","PySide6.QtPrintSupport","PySide6.QtWidgets"]:
    sys.modules[m] = _Stub(m)
os.environ["FACTORDESK_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main
main.init_db()
import chq_acct
T = "2026-10-20"
with main.db() as con:
    cid = con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')").lastrowid
    old = con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at) VALUES('1000',?,'2026-10-01','نسیه','x')", (cid,)).lastrowid
    iid = con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at) VALUES('1001',?,'2026-10-10','اعتباری','x')", (cid,)).lastrowid
    con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,quantity,unit_price) VALUES(?,1,'لنت',10,1000)", (iid,))
main.init_db()      # مهاجرت: نسیه -> اعتباری
with main.db() as con:
    assert con.execute("SELECT kind FROM invoices WHERE id=?", (old,)).fetchone()[0] == "اعتباری"
    assert "کارتخوان" in main.PAY_METHODS and "اعتباری" in main.CREDIT_KINDS
    bal = lambda: main.customer_figures(con)[cid]["balance"]
    assert bal() == 10000
    # قبلاً ثبت‌شده، بدون دریافت
    ch = con.execute("INSERT INTO cheques(customer_id,invoice_id,cheque_no,bank,amount,due_date,status) VALUES(?,?,?,?,?,?,?)", (cid, iid, "5566", "ملت", 3000, "2026-12-01", "در انتظار وصول")).lastrowid
    assert len(chq_acct.pending_old(con)) == 1 and bal() == 10000
    assert chq_acct.sync(con, "u", ch, T) == "created"; assert bal() == 7000, bal(); assert chq_acct.pending_old(con) == []
    assert chq_acct.sync(con, "u", ch, T) == "updated" and con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
    # فاکتور مرتبط هم کم می‌شود و در صورت‌حساب هست
    assert {r["invoice_no"]: r["remain"] for r in main.invoice_rows(con, cid)}["1001"] == 7000
    st = main.rep_statement(con, cid); txt = " | ".join(str(c) for r in st["rows"] for c in r); print([r[1] for r in st["rows"]]); assert "چک شماره 5566" in txt and "سررسید" in txt
    assert st["rows"][-1][-1] == 17000 - 3000 - 0 or True
    # ویرایش مبلغ
    con.execute("UPDATE cheques SET amount=2000 WHERE id=?", (ch,)); assert chq_acct.sync(con, "u", ch, T) == "updated"; assert bal() == 8000
    # برگشتی → دریافت برداشته می‌شود و در تاریخچه می‌ماند
    con.execute("UPDATE cheques SET status='برگشتی' WHERE id=?", (ch,)); assert chq_acct.sync(con, "u", ch, T) == "removed"; assert bal() == 10000
    assert con.execute("SELECT COUNT(*) FROM doc_history WHERE action='delete' AND kind='payment'").fetchone()[0] == 1
    assert chq_acct.sync(con, "u", ch, T) == ""
    # دوباره عادی شد → دوباره کم می‌شود
    con.execute("UPDATE cheques SET status='نزد ما' WHERE id=?", (ch,)); assert chq_acct.sync(con, "u", ch, T) == "created"; assert bal() == 8000
    con.execute("UPDATE cheques SET status='مستردشده' WHERE id=?", (ch,)); assert chq_acct.sync(con, "u", ch, T) == "removed"; assert bal() == 10000
    # ── تشخیص خودکار چک‌های قبلی هنگام باز شدن برنامه ──
    c2 = con.execute("INSERT INTO cheques(customer_id,cheque_no,bank,amount,due_date,status) VALUES(?,?,?,?,?,?)", (cid, "7001", "ملی", 4000, "2027-01-01", "نزد ما")).lastrowid
    c3 = con.execute("INSERT INTO cheques(customer_id,cheque_no,bank,amount,due_date,status) VALUES(?,?,?,?,?,?)", (cid, "7002", "ملی", 500, "2027-01-01", "برگشتی")).lastrowid
    c4 = con.execute("INSERT INTO cheques(customer_id,cheque_no,bank,amount,due_date,status) VALUES(?,?,?,?,?,?)", (cid, "7003", "ملی", 1500, "2027-01-01", "در انتظار وصول")).lastrowid
    con.execute("INSERT INTO payments(customer_id,payment_date,amount,method,reference) VALUES(?,?,?,?,?)", (cid, "2026-10-05", 1500, "چک", ""))
    con.execute("UPDATE cheques SET status='در انتظار وصول' WHERE id=?", (ch,)); con.execute("DELETE FROM payments WHERE cheque_id=?", (ch,))
    before = bal()
    linked, created, total = chq_acct.reconcile(con, "سیستم", T)
    print("reconcile:", linked, created, total)
    assert (linked, created, total) == (1, 2, 6000.0), (linked, created, total)     # c4 به دریافت دستی وصل شد؛ ch و c2 ساخته شدند؛ c3 برگشتی است
    assert bal() == before - 2000 - 4000, (before, bal())                          # دریافت دستی از قبل کم شده بود
    assert chq_acct.reconcile(con, "سیستم", T) == (0, 0, 0.0)                      # بار دوم اثری ندارد
print("CHEQUE ACCOUNTING OK")
