# -*- coding: utf-8 -*-
import os, sys, sqlite3, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import safety

SCHEMA = """
CREATE TABLE customers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT DEFAULT '', address TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE invoices(id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_no TEXT UNIQUE NOT NULL, customer_id INTEGER NOT NULL, invoice_date TEXT NOT NULL, kind TEXT NOT NULL,
  due_date TEXT DEFAULT '', description TEXT DEFAULT '', discount REAL DEFAULT 0, paid_initial REAL DEFAULT 0, notes TEXT DEFAULT '', created_at TEXT NOT NULL, is_proforma INTEGER DEFAULT 0,
  FOREIGN KEY(customer_id) REFERENCES customers(id));
CREATE TABLE invoice_items(id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, row_no INTEGER NOT NULL, product_name TEXT DEFAULT '', description TEXT DEFAULT '',
  quantity REAL DEFAULT 1, unit_price REAL DEFAULT 0, FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE);
CREATE TABLE payments(id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, invoice_id INTEGER, payment_date TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL,
  reference TEXT DEFAULT '', notes TEXT DEFAULT '', cheque_id INTEGER, FOREIGN KEY(customer_id) REFERENCES customers(id), FOREIGN KEY(invoice_id) REFERENCES invoices(id));
CREATE TABLE cheques(id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, invoice_id INTEGER, cheque_no TEXT DEFAULT '', bank TEXT DEFAULT '', issuer TEXT DEFAULT '',
  amount REAL NOT NULL, due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'x', notes TEXT DEFAULT '', FOREIGN KEY(customer_id) REFERENCES customers(id), FOREIGN KEY(invoice_id) REFERENCES invoices(id));
"""

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.p = os.path.join(self.d, "t.db")
        self.con = sqlite3.connect(self.p); self.con.row_factory = sqlite3.Row
        safety.apply_pragmas(self.con); self.con.executescript(SCHEMA); safety.ensure_tables(self.con)
        self.con.execute("INSERT INTO customers(name,created_at) VALUES('علی','2026-01-01')")
        self.con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at) VALUES('1001',1,'2026-10-18','نقدی','x')")
        self.con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,quantity,unit_price) VALUES(1,1,'لنت',2,500)")
        self.con.commit()
    def tearDown(self): self.con.close()

    def test_pragmas(self):
        self.assertEqual(self.con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.con.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_delete_and_restore(self):
        hid = safety.archive_delete(self.con, "مدیر", "invoice", 1, "اشتباه")
        self.con.execute("DELETE FROM invoice_items WHERE invoice_id=1"); self.con.execute("DELETE FROM invoices WHERE id=1"); self.con.commit()
        self.assertEqual(len(safety.list_deleted(self.con)), 1)
        safety.restore(self.con, "مدیر", hid); self.con.commit()
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM invoice_items").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT invoice_no FROM invoices WHERE id=1").fetchone()[0], "1001")
        self.assertEqual(len(safety.list_deleted(self.con)), 0)
        with self.assertRaises(safety.RestoreError): safety.restore(self.con, "x", hid)

    def test_restore_blocked_on_duplicate_no(self):
        hid = safety.archive_delete(self.con, "u", "invoice", 1)
        self.con.execute("DELETE FROM invoices WHERE id=1")
        self.con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at) VALUES('1001',1,'2026-10-19','نقدی','x')")
        self.con.commit()
        with self.assertRaises(safety.RestoreError): safety.restore(self.con, "u", hid)

    def test_rollback_is_atomic(self):
        safety.archive_delete(self.con, "u", "invoice", 1)
        self.con.execute("DELETE FROM invoices WHERE id=1"); self.con.rollback()
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM doc_history").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)

    def test_append_only(self):
        safety.archive_delete(self.con, "u", "invoice", 1); self.con.commit()
        with self.assertRaises(sqlite3.DatabaseError): self.con.execute("UPDATE doc_history SET user='x'")
        with self.assertRaises(sqlite3.DatabaseError): self.con.execute("DELETE FROM doc_history")

    def test_cheque_and_customer_and_edit_log(self):
        self.con.execute("INSERT INTO cheques(customer_id,cheque_no,amount,due_date) VALUES(1,'55',1000,'2026-12-01')")
        self.con.execute("INSERT INTO payments(customer_id,payment_date,amount,method,cheque_id) VALUES(1,'2026-12-01',1000,'چک',1)")
        hid = safety.archive_delete(self.con, "u", "cheque", 1)
        self.con.execute("DELETE FROM payments WHERE cheque_id=1"); self.con.execute("DELETE FROM cheques WHERE id=1")
        safety.restore(self.con, "u", hid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)
        before = safety.snapshot(self.con, "invoice", 1)
        self.assertTrue(safety.log_invoice_change(self.con, "u", "edit", 1, before))

    def test_print_guard(self):
        with safety.print_guard("a"):
            with self.assertRaises(safety.PrintBusy):
                with safety.print_guard("b"): pass
        self.assertTrue(safety.recently_printed("a")); self.assertFalse(safety.recently_printed("zzz"))

    def test_quick_check_and_stamp(self):
        self.con.commit(); self.assertTrue(safety.quick_check(self.p)[0]); self.assertTrue(safety.data_stamp(self.p))
        open(os.path.join(self.d, "bad.db"), "wb").write(b"not a database" * 100)
        self.assertFalse(safety.quick_check(os.path.join(self.d, "bad.db"))[0])



class TReturns(unittest.TestCase):
    def test_returns_roundtrip(self):
        import returns
        d = tempfile.mkdtemp(); con = sqlite3.connect(os.path.join(d, "r.db")); con.row_factory = sqlite3.Row
        safety.apply_pragmas(con); con.executescript(SCHEMA); safety.ensure_tables(con); returns.ensure_tables(con)
        con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')"); con.commit()
        rid, no = returns.save_return(con, None, 1, None, "2026-10-19", "خراب", [("لنت", "", 2, 500), ("فیلتر", "", 1, 100)])
        self.assertEqual(no, "1"); self.assertEqual(returns.items_total([("a", "", 2, 500)]), 1000)
        rid2, no2 = returns.save_return(con, None, 1, None, "2026-10-19", "", [("x", "", 1, 1)]); self.assertEqual(no2, "2")
        returns.save_return(con, rid, 1, None, "2026-10-19", "ویرایش", [("لنت", "", 1, 500)])
        self.assertEqual(len(returns.load_return(con, rid)[1]), 1)
        hid = safety.archive_delete(con, "u", "return", rid, "تست")
        con.execute("DELETE FROM return_items WHERE return_id=?", (rid,)); con.execute("DELETE FROM returns WHERE id=?", (rid,)); con.commit()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM returns").fetchone()[0], 1)
        safety.restore(con, "u", hid); con.commit()
        self.assertEqual(len(returns.load_return(con, rid)[1]), 1); con.close()

class TRevert(unittest.TestCase):
    def test_invoice_to_proforma(self):
        import returns
        d = tempfile.mkdtemp(); con = sqlite3.connect(os.path.join(d, "v.db")); con.row_factory = sqlite3.Row
        safety.apply_pragmas(con); con.executescript(SCHEMA); safety.ensure_tables(con); returns.ensure_tables(con)
        con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT DEFAULT '')"); con.execute("INSERT INTO settings VALUES('next_proforma','7')")
        con.execute("INSERT INTO customers(name,created_at) VALUES('علی','x')")
        con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at,paid_initial) VALUES('1001',1,'2026-10-19','نقدی','x',1000)")
        con.execute("INSERT INTO invoices(invoice_no,customer_id,invoice_date,kind,created_at) VALUES('1002',1,'2026-10-19','نسیه','x')")
        con.execute("INSERT INTO payments(customer_id,invoice_id,payment_date,amount,method) VALUES(1,2,'2026-10-19',5,'نقدی')"); con.commit()
        with self.assertRaises(ValueError): safety.invoice_to_proforma(con, "u", 2, "x")      # دریافت وصل است
        old, new = safety.invoice_to_proforma(con, "u", 1, "اشتباه"); con.commit()
        self.assertEqual((old, new), ("1001", "P7"))
        r = con.execute("SELECT invoice_no,is_proforma,kind,paid_initial FROM invoices WHERE id=1").fetchone()
        self.assertEqual((r[0], r[1], r[2], r[3]), ("P7", 1, "پیش‌فاکتور", 0))
        self.assertEqual(con.execute("SELECT value FROM settings WHERE key='next_proforma'").fetchone()[0], "8")
        h = con.execute("SELECT action,reason FROM doc_history ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(h[0], "to_proforma"); self.assertIn("1001", h[1])
        with self.assertRaises(ValueError): safety.invoice_to_proforma(con, "u", 1, "again")  # حالا پیش‌فاکتور است
        con.close()

if __name__ == "__main__": unittest.main()
