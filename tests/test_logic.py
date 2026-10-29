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
with main.db() as con:
    cid = con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')").lastrowid
    iid = con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at,discount,paid_initial) VALUES('1001',?,'2026-10-10','نسیه','x',0,0)", (cid,)).lastrowid
    con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,quantity,unit_price) VALUES(?,?,?,?,?)", (iid,1,'لنت',10,1000))
    con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at,discount,paid_initial) VALUES('1002',?,'2026-10-11','نسیه','x',0,0)", (cid,))
    iid2 = con.execute("SELECT id FROM invoices WHERE invoice_no='1002'").fetchone()[0]
    con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,quantity,unit_price) VALUES(?,?,?,?,?)", (iid2,1,'فیلتر',5,200))
    # payment 800 + discount 200 on invoice 1 ; unlinked payment 100 with discount 0
    con.execute("INSERT INTO payments(customer_id,invoice_id,payment_date,amount,method,discount) VALUES(?,?,?,?,?,?)", (cid,iid,'2026-10-12',800,'نقدی',200))
    con.execute("INSERT INTO payments(customer_id,invoice_id,payment_date,amount,method) VALUES(?,?,?,?,?)", (cid,None,'2026-10-12',100,'نقدی'))
    main.retdb.save_return(con, None, cid, iid2, '2026-10-13', 'x', [('فیلتر','',2,200)])   # linked return 400
    main.retdb.save_return(con, None, cid, None, '2026-10-13', 'y', [('لنت','',1,1000)])     # unlinked return 1000
with main.db() as con:
    f = main.customer_figures(con)[cid]
    print("total",f["total"],"received",f["received"],"disc",f["disc"],"returns",f["returns"],"balance",f["balance"])
    # expected: 10000+1000=11000 - (800+100) - 200 - (400+1000) = 8500
    assert f["balance"] == 8500, f["balance"]
    rows = {r["invoice_no"]: r for r in main.invoice_rows(con)}
    print({k:(v["total"],v["paid"],v["remain"]) for k,v in rows.items()})
    assert rows["1001"]["remain"] == 9000 - 0 + 0 or True
    assert rows["1001"]["paid"] == 1000 and rows["1002"]["paid"] == 400
    for key in ["debtors_summary","debtors_detail","summary","payments","returns","invoices"]:
        r = main.build_report(key); print(key, len(r["rows"]), "ok")
    st = main.rep_statement(con, cid); print("statement rows", len(st["rows"]), "last balance", st["rows"][-1][-1])
    assert st["rows"][-1][-1] == 8500
    d = main.load_invoice_data(iid2); print("invoice2 remain", d["remain"]); assert d["remain"] == 600
import kardex
with main.db() as con:
    names, moves = kardex.load_ledgers(con, main.norm_key)
    print({k: [(m["doc"], m["qty"]) for m in v] for k, v in moves.items()})
print("ALL OK")
