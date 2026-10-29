# -*- coding: utf-8 -*-
"""بارکد کالا: هر بارکد به یک نام کالا وصل می‌شود؛ اگر بارکدی تعریف نشده بود، همان لحظه‌ی اسکن تعریف می‌شود."""
import re
from datetime import datetime

_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS product_barcodes(
        barcode TEXT PRIMARY KEY, product_name TEXT NOT NULL, description TEXT DEFAULT '', created_at TEXT NOT NULL)""")


def norm(code):
    return re.sub(r"\s+", "", str(code or "").translate(_DIG))


def looks_like_barcode(text):
    return re.fullmatch(r"\d{4,}", norm(text)) is not None


def lookup(con, code):
    return con.execute("SELECT * FROM product_barcodes WHERE barcode=?", (norm(code),)).fetchone()


def define(con, code, name, desc=""):
    con.execute("INSERT OR REPLACE INTO product_barcodes(barcode,product_name,description,created_at) VALUES(?,?,?,?)",
                (norm(code), str(name).strip(), desc or "", datetime.now().isoformat(timespec="seconds")))


def known_names(con, limit=800):
    rows = con.execute("""SELECT product_name FROM (SELECT product_name FROM invoice_items WHERE product_name<>''
                          UNION SELECT product_name FROM product_barcodes) GROUP BY product_name ORDER BY product_name LIMIT ?""", (limit,)).fetchall()
    return [r[0] for r in rows]


def is_scan(n_chars, first_t, last_t, min_len=4, max_avg=0.06):
    """آیا دنباله‌ی کاراکترهای ورودی کار بارکدخوان است؟ (میانگین فاصله‌ی زمانی بین کاراکترها کوتاه باشد؛ تایپ با دست هرگز زیر ~۸۰ms نمی‌رسد.
    میانگین گرفته می‌شود تا یک تأخیر لحظه‌ای برنامه (مثلاً باز شدن ویرایشگر سلول با اولین رقم) تشخیص را خراب نکند.)"""
    if n_chars < min_len: return False
    return (last_t - first_t) / max(1, n_chars - 1) <= max_avg


def barcode_of(con, name):
    """آخرین بارکدِ ثبت‌شده برای یک کالا (یا '')."""
    r = con.execute("SELECT barcode FROM product_barcodes WHERE product_name=? ORDER BY created_at DESC LIMIT 1", (str(name).strip(),)).fetchone()
    return r[0] if r else ""


def new_unique_code(con, kind):
    """بارکد داخلیِ تازه (که هنوز به هیچ کالایی وصل نیست). شمارنده در settings می‌ماند تا هیچ‌وقت تکرار نشود؛ داخل همان تراکنش کاربر به‌روز می‌شود."""
    import barcode_codec as bc
    r = con.execute("SELECT value FROM settings WHERE key='next_barcode_seq'").fetchone()
    n = int(r[0]) if r and str(r[0]).isdigit() else 1
    while True:
        code = bc.new_internal_code(kind, n); n += 1
        if not lookup(con, code): break
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('next_barcode_seq',?)", (str(n),))
    return code


def grid_layout(label_w, label_h, page_w=210.0, page_h=297.0, margin=8.0):
    """چند برچسب در یک برگه‌ی A4 جا می‌شود: (ستون، ردیف)."""
    return max(1, int((page_w - 2 * margin) // label_w)), max(1, int((page_h - 2 * margin) // label_h))
