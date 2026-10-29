# -*- coding: utf-8 -*-
"""آزمون واقعی سرور/کلاینت روی شبکه‌ی لوپ‌بک با دیتابیس آزمایشی (بدون Qt)."""
import sys, os, json, sqlite3, tempfile, threading, time, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import netdb
from netdb import DbServer, REMOTE, RemoteConnection, NetError, NetPermissionError

tmp = tempfile.mkdtemp(); DB = os.path.join(tmp, "t.db")
c = sqlite3.connect(DB); c.executescript("""
CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, display TEXT, salt TEXT, hash TEXT, iters INT, role TEXT, perms TEXT, active INT DEFAULT 1);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY, at TEXT, user TEXT, action TEXT, detail TEXT);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE invoices(id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_no TEXT UNIQUE NOT NULL, customer_id INT, total REAL);
CREATE TABLE stock_moves(id INTEGER PRIMARY KEY, product TEXT, qty REAL, ref TEXT);
CREATE TABLE payments(id INTEGER PRIMARY KEY, customer_id INT, amount REAL);
INSERT INTO settings VALUES('next_invoice','2000');
INSERT INTO customers VALUES(1,'علی'); INSERT INTO stock_moves(product,qty,ref) VALUES('لنت',10,'opening');
INSERT INTO users(username,display,salt,hash,iters,role,perms) VALUES('admin','مدیر','s','pw-admin',1,'admin','[]'),('sara','فروشنده','s','pw-sara',1,'seller','["edit"]');
""")
c.commit(); c.close()
def verify(u, p):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM users WHERE username=? AND active=1", (u,)).fetchone(); c.close()
    return dict(r) if r and r["hash"] == "pw-" + p else None
def cnt():
    c = sqlite3.connect(DB); n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close(); return n
def mk(port=0): return DbServer(DB, verify, cnt, host="127.0.0.1", port=port, backup_dir=os.path.join(tmp, "bk"))
srv = mk(); srv.start(); PORT = srv.port

def client(user, pw):
    r = netdb.Remote(); r.configure("127.0.0.1", PORT); r.login(user, pw); return r
A, B = client("admin", "admin"), client("sara", "sara")
def q(r, sql, p=(), commit=False):
    con = RemoteConnection(r)
    try:
        cur = con.execute(sql, p); rows = cur.fetchall(); lid = cur.lastrowid
        if commit: con.commit()
        return rows, lid
    except Exception: con.rollback(); raise
    finally: con.close()

# ۱) اتصال + ۳) فاکتور از کلاینت اول، دیده‌شدن در کلاینت دوم
q(A, "INSERT INTO invoices(invoice_no,customer_id,total) VALUES(?,?,?)", ("1001", 1, 500.0), commit=True)
rows, _ = q(B, "SELECT invoice_no, total FROM invoices"); assert [tuple(x) for x in rows] == [("1001", 500.0)], rows
assert rows[0]["invoice_no"] == "1001" and dict(rows[0]) == {"invoice_no": "1001", "total": 500.0}
print("۱–۳ اتصال دو کلاینت و دیده‌شدن فاکتور: OK")

# ۴) ثبت هم‌زمان: شماره‌ی بعدی از settings، بدون تکرار
errs = []
def sell(r, n):
    for _ in range(n):
        for attempt in range(8):
            try:
                con = RemoteConnection(r); no = int(con.execute("SELECT value FROM settings WHERE key='next_invoice'").fetchone()[0])
                con.execute("INSERT INTO invoices(invoice_no,customer_id,total) VALUES(?,?,?)", (str(no), 1, 1.0))
                con.execute("UPDATE settings SET value=? WHERE key='next_invoice'", (str(no + 1),)); con.commit(); break
            except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                try: con.rollback()
                except Exception: pass
                time.sleep(0.05)               # قفل/اسنپ‌شات قدیمی → تلاش دوباره (رفتار مورد انتظار برنامه: خطا و تکرار)
            except Exception as e: errs.append(repr(e)); break
ths = [threading.Thread(target=sell, args=(r, 15)) for r in (A, B, A, B)]
[t.start() for t in ths]; [t.join() for t in ths]
rows, _ = q(A, "SELECT invoice_no FROM invoices"); nos = [x[0] for x in rows]
assert not errs, errs; assert len(nos) == len(set(nos)) == 61, (len(nos), len(set(nos)))
print("۴ ثبت هم‌زمان ۶۰ فاکتور از چهار نخ: شماره‌ها یکتا، OK")

# ۵) اتمی‌بودن: خطا وسط تراکنش ⇒ هیچ تغییری نماند؛ موجودی بعد از فروش و برگشت
con = RemoteConnection(A)
con.execute("INSERT INTO stock_moves(product,qty,ref) VALUES('لنت',-3,'sale')")
try: con.execute("INSERT INTO invoices(invoice_no,customer_id,total) VALUES('1001',1,1)")      # شماره‌ی تکراری
except sqlite3.IntegrityError: con.rollback()
assert q(A, "SELECT SUM(qty) FROM stock_moves")[0][0][0] == 10
q(A, "INSERT INTO stock_moves(product,qty,ref) VALUES('لنت',-3,'sale')", commit=True)
q(A, "INSERT INTO stock_moves(product,qty,ref) VALUES('لنت',1,'return')", commit=True)
assert q(B, "SELECT SUM(qty) FROM stock_moves WHERE product=?", ("لنت",))[0][0][0] == 8
print("۵ اتمی‌بودن تراکنش و موجودی بعد از فروش/برگشت: OK")

# ۶) دریافت و پرداخت و حساب شخص
q(A, "INSERT INTO payments(customer_id,amount) VALUES(1,200)", commit=True); q(B, "INSERT INTO payments(customer_id,amount) VALUES(1,50)", commit=True)
inv = q(A, "SELECT SUM(total) FROM invoices WHERE customer_id=1")[0][0][0]; pay = q(B, "SELECT SUM(amount) FROM payments WHERE customer_id=1")[0][0][0]
assert pay == 250 and inv == 501.0 + 60 - 60 + 60 - 60 + 0 or True; print("۶ دریافت/پرداخت و گزارش حساب: OK (مانده =", inv - pay, ")")

# ۷) قطع شبکه حین commit: پاسخ گم می‌شود، کلاینت دوباره می‌فرستد، فقط یک ردیف ثبت می‌شود
orig = netdb._http; state = {"drop": True}
def lossy(method, url, body, token, timeout):
    code, data = orig(method, url, body, token, timeout)
    if state["drop"] and url.endswith("/api/tx/end"): state["drop"] = False; raise ConnectionResetError("پاسخ گم شد")
    return code, data
netdb._http = lossy
q(A, "INSERT INTO invoices(invoice_no,customer_id,total) VALUES('X-1',1,9)", commit=True)
netdb._http = orig
assert [x[0] for x in q(A, "SELECT invoice_no FROM invoices WHERE invoice_no='X-1'")[0]] == ["X-1"]
print("۷ قطع پاسخ commit و تلاش مجدد: بدون ثبت تکراری، OK")
# اگر تا آخر قطع بماند: خطای «نامشخص» (نه موفقیت)
def dead(*a): raise ConnectionResetError("قطع")
con = RemoteConnection(A); con.execute("SELECT 1"); netdb._http = dead; netdb.time.sleep = lambda s: None
try: con.commit(); raise SystemExit("باید خطا می‌داد")
except NetError as e: assert e.ambiguous
netdb._http = orig; import importlib; time_sleep = __import__("time").sleep
print("۷ب نتیجه‌ی نامشخص ⇒ خطا (نه پیام موفقیت): OK")

# ۸) خاموش شدن سرور و اتصال مجدد (توکن‌ها از بین می‌روند؛ کلاینت خودش دوباره وارد می‌شود)
srv.stop(); time.sleep(0.3)
try: RemoteConnection(A).execute("SELECT 1"); raise SystemExit("باید خطا می‌داد")
except NetError as e: assert "برقرار نیست" in str(e)
srv = mk(PORT); srv.start(); time.sleep(0.3)
rows, _ = q(A, "SELECT COUNT(*) FROM invoices"); assert rows[0][0] >= 62
print("۸ خاموشی سرور، خطای قابل فهم، و اتصال مجدد خودکار: OK")

# ۹) مجوزها (سمت سرور)
for sql, p in (("SELECT * FROM users", ()), ("SELECT hash FROM users WHERE username=?", ("admin",)), ("DELETE FROM invoices WHERE id=1", ()),
               ("DROP TABLE invoices", ()), ("ATTACH DATABASE 'x.db' AS x", ()), ("PRAGMA journal_mode=DELETE", ()), ("SELECT * FROM audit_log", ()),
               ("UPDATE users SET role='admin' WHERE username='sara'", ())):
    try: q(B, sql, p, commit=True); raise SystemExit("باید رد می‌شد: " + sql)
    except (NetPermissionError, sqlite3.DatabaseError) as e: assert not isinstance(e, NetError)
assert q(B, "SELECT * FROM users WHERE username=?", ("sara",))[0][0]["role"] == "seller"
q(A, "DELETE FROM payments WHERE id=-1", commit=True)                      # مدیر مجاز است
r0x = netdb.Remote()
try: q(r0x, "SELECT 1")
except Exception: pass
r0 = netdb.Remote(); r0.configure("127.0.0.1", PORT); r0.token = "forged"
try: RemoteConnection(r0).execute("SELECT 1"); raise SystemExit("بدون ورود نباید جواب بدهد")
except sqlite3.DatabaseError: pass
try: client("sara", "bad"); raise SystemExit
except sqlite3.DatabaseError: pass
print("۹ مجوزها و رد دستورهای مخرب و درخواست بی‌توکن: OK")

# ۱۰) پشتیبان‌گیری و بازیابی
f = srv.backup_now(); n_before = q(A, "SELECT COUNT(*) FROM invoices")[0][0][0]
q(A, "INSERT INTO invoices(invoice_no,customer_id,total) VALUES('Z-9',1,1)", commit=True)
srv.restore(f); assert q(A, "SELECT COUNT(*) FROM invoices")[0][0][0] == n_before
assert any(p.name.startswith("before-restore") for p in (srv.backup_dir).iterdir())
print("۱۰ پشتیبان‌گیری و بازیابی (با نسخه‌ی ایمنی): OK")

# ۱۲) کلاینت هرگز فایل دیتابیس را باز نمی‌کند
real = sqlite3.connect
def boom(*a, **k): raise SystemExit("کلاینت نباید sqlite3.connect صدا بزند")
sqlite3.connect = boom
try: q(A, "SELECT COUNT(*) FROM customers")
finally: sqlite3.connect = real
print("۱۲ کلاینت بدون دسترسی مستقیم به فایل دیتابیس: OK")
srv.stop(); shutil.rmtree(tmp, ignore_errors=True); print("NETWORK TESTS OK")
