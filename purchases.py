# -*- coding: utf-8 -*-
"""منطق حسابداری خرید (بدون Qt): تأمین‌کنندگان، فاکتور خرید، برگشت از خرید، پرداخت به تأمین‌کننده، صورت‌حساب و گزارش.
علامت‌ها: مانده‌ی تأمین‌کننده = آنچه ما به او بدهکاریم (مثبت = بدهی ما).
  فاکتور خرید: + مبلغ نهایی ؛ پرداخت اولیه‌ی همان فاکتور: − ؛ برگشت از خرید: − مبلغ نهایی ؛ وجه نقد دریافتی بابت برگشت: + ؛ هر پرداخت: −."""
import hashlib
from datetime import datetime

DOC_LABEL = {"purchase": "فاکتور خرید", "return": "برگشت از خرید"}
KINDS = ("نقدی", "اعتباری", "چکی")
PAY_METHODS = ["نقدی", "کارتخوان", "کارت‌به‌کارت", "حواله", "واریز بانکی", "چک", "سایر"]


class Conflict(Exception):
    """سند بعد از باز شدن توسط کاربر دیگری تغییر کرده است."""


def ensure_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS suppliers(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT DEFAULT '', address TEXT DEFAULT '',
      notes TEXT DEFAULT '', created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS purchases(
      id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT NOT NULL DEFAULT 'purchase', doc_no TEXT NOT NULL, supplier_id INTEGER NOT NULL,
      doc_date TEXT NOT NULL, ref_no TEXT DEFAULT '', kind TEXT DEFAULT 'نقدی', due_date TEXT DEFAULT '', description TEXT DEFAULT '',
      discount REAL DEFAULT 0, paid_initial REAL DEFAULT 0, notes TEXT DEFAULT '', created_at TEXT NOT NULL,
      UNIQUE(doc_type, doc_no), FOREIGN KEY(supplier_id) REFERENCES suppliers(id)
    );
    CREATE TABLE IF NOT EXISTS purchase_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_id INTEGER NOT NULL, row_no INTEGER NOT NULL, product_name TEXT DEFAULT '',
      description TEXT DEFAULT '', quantity REAL DEFAULT 1, unit_price REAL DEFAULT 0,
      FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS supplier_payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id INTEGER NOT NULL, purchase_id INTEGER, pay_date TEXT NOT NULL, amount REAL NOT NULL,
      method TEXT NOT NULL, reference TEXT DEFAULT '', notes TEXT DEFAULT '', due_date TEXT DEFAULT '', cleared INTEGER DEFAULT 1, created_at TEXT DEFAULT '',
      FOREIGN KEY(supplier_id) REFERENCES suppliers(id)
    );
    CREATE INDEX IF NOT EXISTS idx_pur_supplier ON purchases(supplier_id, doc_date);
    CREATE INDEX IF NOT EXISTS idx_puritems ON purchase_items(purchase_id, row_no);
    CREATE INDEX IF NOT EXISTS idx_suppay ON supplier_payments(supplier_id, pay_date);
    """)
    if "created_at" not in [r[1] for r in con.execute("PRAGMA table_info(supplier_payments)")]:
        con.execute("ALTER TABLE supplier_payments ADD COLUMN created_at TEXT DEFAULT ''")        # نسخه‌های قبلی: ستون ساعت ثبت


# ───────────── محاسبه ─────────────
def items_total(items):
    """items: [(نام، شرح، تعداد، قیمت واحد)]"""
    return sum(float(i[2]) * float(i[3]) for i in items)

def doc_total(items, discount=0.0):
    return max(0.0, items_total(items) - float(discount or 0))

def next_no(con, doc_type):
    r = con.execute("SELECT COALESCE(MAX(CAST(doc_no AS INTEGER)),0)+1 FROM purchases WHERE doc_type=?", (doc_type,)).fetchone()
    return str(int(r[0]))

def doc_fp(con, pid):
    a = [tuple(r) for r in con.execute("SELECT * FROM purchases WHERE id=?", (pid,))]
    b = [tuple(r) for r in con.execute("SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY row_no", (pid,))]
    return hashlib.md5(repr((a, b)).encode("utf-8")).hexdigest()


def save_doc(con, pid, doc_type, supplier_id, date_iso, ref_no, kind, due_date, description, discount, paid_initial, notes, items, expected_fp=None):
    """ذخیره‌ی سند (جدید یا ویرایش) داخل تراکنش کاربر. شماره‌ی جدید داخل همین تراکنش گرفته می‌شود (هم‌زمانیِ شبکه تکراری نمی‌سازد).
    expected_fp: اثر انگشتی که هنگام باز کردن سند گرفته شد؛ اگر عوض شده باشد Conflict. -> (id، شماره)"""
    now = datetime.now().isoformat(timespec="seconds")
    if pid is None:
        no = next_no(con, doc_type)
        pid = con.execute("""INSERT INTO purchases(doc_type,doc_no,supplier_id,doc_date,ref_no,kind,due_date,description,discount,paid_initial,notes,created_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (doc_type, no, supplier_id, date_iso, ref_no or "", kind, due_date or "", description or "",
                                                                float(discount or 0), float(paid_initial or 0), notes or "", now)).lastrowid
    else:
        if expected_fp and doc_fp(con, pid) != expected_fp: raise Conflict("این سند بعد از باز کردنش توسط کاربر دیگری تغییر کرده است.")
        con.execute("""UPDATE purchases SET supplier_id=?,doc_date=?,ref_no=?,kind=?,due_date=?,description=?,discount=?,paid_initial=?,notes=? WHERE id=?""",
                    (supplier_id, date_iso, ref_no or "", kind, due_date or "", description or "", float(discount or 0), float(paid_initial or 0), notes or "", pid))
        con.execute("DELETE FROM purchase_items WHERE purchase_id=?", (pid,))
        no = con.execute("SELECT doc_no FROM purchases WHERE id=?", (pid,)).fetchone()[0]
    for n, (name, desc, qty, price) in enumerate(items, 1):
        con.execute("INSERT INTO purchase_items(purchase_id,row_no,product_name,description,quantity,unit_price) VALUES(?,?,?,?,?,?)", (pid, n, name, desc, qty, price))
    return pid, no


def load_doc(con, pid):
    h = con.execute("SELECT p.*, s.name AS supplier_name FROM purchases p JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?", (pid,)).fetchone()
    its = con.execute("SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY row_no", (pid,)).fetchall()
    return h, its


def delete_doc(con, pid):
    """پرداخت‌های ثبت‌شده‌ی مرتبط حذف نمی‌شوند (پول واقعاً پرداخت شده)؛ فقط از سند جدا می‌شوند و در حساب تأمین‌کننده می‌مانند."""
    con.execute("UPDATE supplier_payments SET purchase_id=NULL WHERE purchase_id=?", (pid,))
    con.execute("DELETE FROM purchase_items WHERE purchase_id=?", (pid,)); con.execute("DELETE FROM purchases WHERE id=?", (pid,))


_DOC_SQL = """SELECT p.id, p.created_at, p.doc_type, p.doc_no, p.doc_date, p.ref_no, p.kind, p.due_date, p.description, p.discount, p.paid_initial, p.supplier_id, s.name AS supplier_name,
       COALESCE((SELECT SUM(quantity*unit_price) FROM purchase_items WHERE purchase_id=p.id),0) AS gross,
       COALESCE((SELECT SUM(amount) FROM supplier_payments WHERE purchase_id=p.id),0) AS linked_paid
  FROM purchases p JOIN suppliers s ON s.id=p.supplier_id"""

def doc_rows(con, supplier_id=None, doc_type=None, text="", date_from=None, date_to=None):
    """فهرست سندها با مبلغ نهایی، پرداخت‌شده و مانده‌ی هر سند."""
    where, args = [], []
    if supplier_id: where.append("p.supplier_id=?"); args.append(supplier_id)
    if doc_type: where.append("p.doc_type=?"); args.append(doc_type)
    if date_from: where.append("p.doc_date>=?"); args.append(date_from)
    if date_to: where.append("p.doc_date<=?"); args.append(date_to)
    if text:
        t = f"%{text.strip()}%"
        where.append("(p.doc_no LIKE ? OR p.ref_no LIKE ? OR s.name LIKE ? OR p.description LIKE ? OR EXISTS(SELECT 1 FROM purchase_items i WHERE i.purchase_id=p.id AND i.product_name LIKE ?))")
        args += [t, t, t, t, t]
    sql = _DOC_SQL + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY p.doc_date DESC, p.id DESC"
    out = []
    for r in con.execute(sql, args):
        d = dict(r); d["total"] = max(0.0, d["gross"] - (d["discount"] or 0)); d["paid"] = (d["paid_initial"] or 0) + d["linked_paid"]
        d["remain"] = d["total"] - d["paid"] if d["doc_type"] == "purchase" else 0.0
        out.append(d)
    return out


def supplier_figures(con):
    """{id: {name, phone, address, purchases, returns, paid, refunds, balance}}  balance>0 یعنی به تأمین‌کننده بدهکاریم."""
    fig = {}
    for s in con.execute("SELECT id,name,phone,address FROM suppliers"):
        fig[s["id"]] = dict(name=s["name"], phone=s["phone"], address=s["address"], purchases=0.0, returns=0.0, paid=0.0, refunds=0.0, balance=0.0, docs=0)
    for d in doc_rows(con):
        f = fig.get(d["supplier_id"])
        if f is None: continue
        f["docs"] += 1
        if d["doc_type"] == "purchase": f["purchases"] += d["total"]; f["paid"] += d["paid_initial"] or 0
        else: f["returns"] += d["total"]; f["refunds"] += d["paid_initial"] or 0
    for r in con.execute("SELECT supplier_id, SUM(amount) a FROM supplier_payments GROUP BY supplier_id"):
        if r["supplier_id"] in fig: fig[r["supplier_id"]]["paid"] += r["a"] or 0
    for f in fig.values(): f["balance"] = f["purchases"] - f["returns"] - f["paid"] + f["refunds"]
    return fig


def statement(con, sid):
    """صورت‌حساب تأمین‌کننده به ترتیب زمان: [{date, text, dec (کاهش بدهی ما), inc (افزایش بدهی ما), balance, kind, ref_id}]"""
    ev = []
    for d in doc_rows(con, supplier_id=sid):
        lab = DOC_LABEL[d["doc_type"]]; ref = f" — فاکتور تأمین‌کننده {d['ref_no']}" if d["ref_no"] else ""
        if d["doc_type"] == "purchase":
            ev.append((d["doc_date"], 0, d["id"], f"{lab} {d['doc_no']}{ref}", 0.0, d["total"], "purchase", d["id"], d["created_at"]))
            if d["paid_initial"]: ev.append((d["doc_date"], 1, d["id"], f"پرداخت اولیه‌ی {lab} {d['doc_no']}", d["paid_initial"], 0.0, "initial", d["id"], d["created_at"]))
        else:
            ev.append((d["doc_date"], 0, d["id"], f"{lab} {d['doc_no']}{ref}", d["total"], 0.0, "return", d["id"], d["created_at"]))
            if d["paid_initial"]: ev.append((d["doc_date"], 1, d["id"], f"وجه دریافتی بابت {lab} {d['doc_no']}", 0.0, d["paid_initial"], "refund", d["id"], d["created_at"]))
    for p in con.execute("SELECT * FROM supplier_payments WHERE supplier_id=?", (sid,)):
        extra = f" — {p['reference']}" if p["reference"] else ""
        extra += f" (سررسید چک {p['due_date']}{'' if p['cleared'] else '، پاس نشده'})" if p["method"] == "چک" and p["due_date"] else ""
        ev.append((p["pay_date"], 2, p["id"], f"پرداخت ({p['method']}){extra}", p["amount"], 0.0, "payment", p["id"], p["created_at"] if "created_at" in p.keys() else ""))
    bal, out = 0.0, []
    for date_, _rk, _id, text, dec, inc, kind, ref_id, ts in sorted(ev, key=lambda e: (e[0], e[1], e[2])):
        bal += inc - dec; out.append(dict(date=date_, text=text, dec=dec, inc=inc, balance=bal, kind=kind, ref_id=ref_id, time=(ts or "")[11:16]))
    return out


def last_purchase_price(con, product_name, supplier_id=None):
    """آخرین قیمت خرید این کالا (ترجیحاً از همین تأمین‌کننده)، وگرنه 0."""
    name = (product_name or "").strip()
    if not name: return 0.0
    r = con.execute("""SELECT i.unit_price FROM purchase_items i JOIN purchases p ON p.id=i.purchase_id WHERE p.doc_type='purchase' AND i.product_name=? AND i.unit_price>0
                       ORDER BY (p.supplier_id=?) DESC, p.doc_date DESC, p.id DESC LIMIT 1""", (name, supplier_id or 0)).fetchone()
    return float(r[0]) if r else 0.0


def product_summary(con, date_from=None, date_to=None):
    """گزارش خرید به تفکیک کالا: تعداد خالص (خرید − برگشت)، مبلغ و میانگین قیمت خرید."""
    where, args = [], []
    if date_from: where.append("p.doc_date>=?"); args.append(date_from)
    if date_to: where.append("p.doc_date<=?"); args.append(date_to)
    sql = """SELECT i.product_name AS name,
                    SUM(CASE WHEN p.doc_type='purchase' THEN i.quantity ELSE -i.quantity END) AS qty,
                    SUM(CASE WHEN p.doc_type='purchase' THEN i.quantity*i.unit_price ELSE -i.quantity*i.unit_price END) AS amount
             FROM purchase_items i JOIN purchases p ON p.id=i.purchase_id""" + (" WHERE " + " AND ".join(where) if where else "") + " GROUP BY i.product_name ORDER BY amount DESC"
    out = []
    for r in con.execute(sql, args):
        out.append(dict(name=r["name"], qty=r["qty"] or 0, amount=r["amount"] or 0, avg=(r["amount"] / r["qty"]) if r["qty"] else 0))
    return out


def period_summary(con, date_from=None, date_to=None):
    rows = doc_rows(con, date_from=date_from, date_to=date_to)
    buy = sum(d["total"] for d in rows if d["doc_type"] == "purchase"); ret = sum(d["total"] for d in rows if d["doc_type"] == "return")
    where, args = "", []
    if date_from or date_to:
        parts = []
        if date_from: parts.append("pay_date>=?"); args.append(date_from)
        if date_to: parts.append("pay_date<=?"); args.append(date_to)
        where = " WHERE " + " AND ".join(parts)
    paid = con.execute("SELECT COALESCE(SUM(amount),0) FROM supplier_payments" + where, args).fetchone()[0]
    return dict(purchases=buy, returns=ret, net=buy - ret, payments=paid)


def pending_cheques(con):
    """چک‌های پرداختیِ پاس‌نشده، به ترتیب سررسید."""
    return con.execute("""SELECT p.*, s.name AS supplier_name FROM supplier_payments p JOIN suppliers s ON s.id=p.supplier_id
                          WHERE p.method='چک' AND p.cleared=0 ORDER BY p.due_date, p.id""").fetchall()
