# -*- coding: utf-8 -*-
"""مرجوعی کالا (فاکتور یار): ثبت و بازیابی سند مرجوعی. بدون وابستگی به Qt تا مستقل تست شود.
مرجوعی از مانده‌ی بدهی مشتری کم می‌شود؛ اگر به فاکتور وصل باشد از مانده‌ی همان فاکتور هم کم می‌شود،
و در کاردکس به‌صورت ورود کالا (بدون اثر روی میانگین قیمت خرید) دیده می‌شود."""
from datetime import datetime


def ensure_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS returns(
      id INTEGER PRIMARY KEY AUTOINCREMENT, return_no TEXT NOT NULL DEFAULT '', customer_id INTEGER NOT NULL,
      invoice_id INTEGER, return_date TEXT NOT NULL, notes TEXT DEFAULT '', created_at TEXT NOT NULL,
      FOREIGN KEY(customer_id) REFERENCES customers(id), FOREIGN KEY(invoice_id) REFERENCES invoices(id)
    );
    CREATE TABLE IF NOT EXISTS return_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER NOT NULL, row_no INTEGER NOT NULL,
      product_name TEXT DEFAULT '', description TEXT DEFAULT '', quantity REAL DEFAULT 1, unit_price REAL DEFAULT 0,
      FOREIGN KEY(return_id) REFERENCES returns(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_ret_customer ON returns(customer_id);
    CREATE INDEX IF NOT EXISTS idx_ret_invoice ON returns(invoice_id);
    CREATE INDEX IF NOT EXISTS idx_retitems ON return_items(return_id, row_no);
    """)


def next_no(con):
    r = con.execute("SELECT COALESCE(MAX(CAST(return_no AS INTEGER)),0)+1 FROM returns").fetchone()
    return str(int(r[0]))


def items_total(items):
    return sum(float(i[2]) * float(i[3]) for i in items)


def save_return(con, rid, customer_id, invoice_id, date_iso, notes, items):
    """items: [(نام کالا، شرح، تعداد، قیمت واحد)]. شناسه و شماره‌ی مرجوعی را برمی‌گرداند."""
    if rid is None:
        no = next_no(con)
        rid = con.execute("INSERT INTO returns(return_no,customer_id,invoice_id,return_date,notes,created_at) VALUES(?,?,?,?,?,?)",
                          (no, customer_id, invoice_id, date_iso, notes or "", datetime.now().isoformat(timespec="seconds"))).lastrowid
    else:
        con.execute("UPDATE returns SET customer_id=?,invoice_id=?,return_date=?,notes=? WHERE id=?",
                    (customer_id, invoice_id, date_iso, notes or "", rid))
        con.execute("DELETE FROM return_items WHERE return_id=?", (rid,))
        no = con.execute("SELECT return_no FROM returns WHERE id=?", (rid,)).fetchone()[0]
    for n, (name, desc, qty, price) in enumerate(items, 1):
        con.execute("INSERT INTO return_items(return_id,row_no,product_name,description,quantity,unit_price) VALUES(?,?,?,?,?,?)",
                    (rid, n, name, desc, qty, price))
    return rid, no


def load_return(con, rid):
    h = con.execute("SELECT * FROM returns WHERE id=?", (rid,)).fetchone()
    items = con.execute("SELECT * FROM return_items WHERE return_id=? ORDER BY row_no", (rid,)).fetchall()
    return h, items
