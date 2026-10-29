# -*- coding: utf-8 -*-
"""
FactorDesk v2 - Persian offline invoice, customer, payment and cheque manager.
UI: PySide6 | DB: SQLite | Dates: stored as ISO Gregorian, shown/entered as Jalali (Shamsi).
Printing / PDF use Qt rich-text (native Persian shaping, no extra libraries).
"""
import sys, os, re, sqlite3, shutil, json, base64, html as _html, ctypes, struct, math, functools, inspect, time as _time
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, QMarginsF, QByteArray, QBuffer, QIODevice, QTimer, QPropertyAnimation, QSizeF, QUrl, QStringListModel, QMimeData, QObject, Signal, QEvent
from PySide6.QtGui import QFont, QColor, QTextDocument, QTextOption, QImage, QPageSize, QPageLayout, QPainter, QFontDatabase, QDesktopServices, QGuiApplication, QShortcut, QKeySequence
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog, QPrinterInfo
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QTabWidget, QLabel, QPushButton, QLineEdit, QTextEdit, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QMessageBox, QDialogButtonBox, QSpinBox,
    QDoubleSpinBox, QFileDialog, QGroupBox, QHeaderView, QAbstractItemView, QStatusBar,
    QInputDialog, QColorDialog, QFontComboBox, QTextBrowser, QScrollArea, QToolButton,
    QStyledItemDelegate, QGraphicsOpacityEffect, QCompleter, QToolBar, QMenu,
)

import sms_panel, kardex, secure, auth, maintenance, safety, recycle_ui
import returns as retdb
import chq_acct, barcodes, netdb, barcode_print, purchases
from ui_fit import QDialog, fit_window, keep_on_screen   # QDialog سازگار با اندازه‌ی صفحه (جایگزین QDialog خود Qt)

APP_NAME = "فاکتور یار"
APP_DIR = Path(os.environ.get("FACTORDESK_DATA") or (Path.home() / "FactorDeskData"))
DB_PATH = APP_DIR / "factordesk.db"
NET_MODE = os.environ.get("FACTORDESK_MODE", "")      # "client" ← از client_app.py؛ خالی = برنامه‌ی تک‌کاربره یا سرور (دسترسی مستقیم به فایل)
BACKUP_DIR = APP_DIR / "backups"
VOICE_ASSISTANT = False   # True = bring back the smart voice assistant (AI, always-on listening, guided entry). Plain "تایپ با صدا" stays.
APP_DIR.mkdir(parents=True, exist_ok=True)

CHQ_STATUSES = ["نزد ما", "در انتظار وصول", "وصول‌شده", "برگشتی", "مستردشده"]
CHQ_COLLECTED = CHQ_STATUSES[2]
CHQ_CLOSED = (CHQ_STATUSES[2], CHQ_STATUSES[4])
CREDIT_KINDS = ("اعتباری", "نسیه", "چکی", "ترکیبی")     # «نسیه» برای پشتیبان‌های قدیمی
PAY_METHODS = ["نقدی", "کارتخوان", "کارت‌به‌کارت", "حواله", "واریز بانکی", "چک", "سایر"]

# ───────────────────────── Jalali (Shamsi) calendar ─────────────────────────
J_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
            "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
J_WEEK = ["ش", "ی", "د", "س", "چ", "پ", "ج"]
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def norm_digits(s):
    return (s or "").translate(_DIG)

_FOLD = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "ؤ": "و", "أ": "ا", "إ": "ا", "ٱ": "ا", "\u200c": " ", "\u200f": " ", "\u200e": " "})
_DIAC = re.compile("[\u064b-\u065f\u0670\u0640]")

def fold_text(s):
    """یکسان‌سازی متن برای جست‌وجو: ی/ک عربی، ارقام فارسی، نیم‌فاصله، اعراب، حروف بزرگ و کوچک."""
    t = _DIAC.sub("", norm_digits(str(s if s is not None else ""))).translate(_FOLD).lower()
    return re.sub(r"\s+", " ", t).strip()

def match_words(query, *fields):
    """جست‌وجوی کلمه‌ای: هر کلمه‌ی عبارت جست‌وجو باید جایی از فیلدها باشد، به هر ترتیب و در هر موقعیتی.
    مثلاً «رضایی علی» یا «علی ۰۹۱۲» یا «علیرضایی» هم «علی رضایی» را پیدا می‌کند."""
    words = fold_text(query).split()
    if not words: return True
    hay = fold_text(" ".join(str(f if f is not None else "") for f in fields)); compact = hay.replace(" ", "")
    return all(w in hay or w in compact for w in words)

def parse_num(s):
    t = norm_digits(str(s)).replace(",", "").replace("٬", "").replace("،", "").strip()
    return float(t) if t else 0.0

def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053)); days %= 12053
    jy += 4 * (days // 1461); days %= 1461
    if days > 365:
        jy += (days - 1) // 365; days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31; jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30; jd = 1 + (days - 186) % 30
    return jy, jm, jd

def j2g(jy, jm, jd):
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097); days %= 146097
    if days > 36524:
        days -= 1; gy += 100 * (days // 36524); days %= 36524
        if days >= 365: days += 1
    gy += 4 * (days // 1461); days %= 1461
    if days > 365:
        gy += (days - 1) // 365; days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    ml = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm < 13 and gd > ml[gm]:
        gd -= ml[gm]; gm += 1
    return gy, gm, gd

def j_month_len(jy, jm):
    if jm <= 6: return 31
    if jm <= 11: return 30
    return 30 if g2j(*j2g(jy, 12, 30)) == (jy, 12, 30) else 29

def iso_to_j(iso):
    """'2026-03-21' -> '1405/01/01' (empty stays empty)."""
    if not iso: return ""
    try:
        y, m, d = [int(x) for x in str(iso)[:10].split("-")]
        jy, jm, jd = g2j(y, m, d)
        return f"{jy:04d}/{jm:02d}/{jd:02d}"
    except Exception:
        return str(iso)

def j_to_iso(text):
    """'1405/1/1' (Persian or Latin digits) -> ISO Gregorian, or None if invalid."""
    parts = [p for p in re.split(r"[/\-.\s]+", norm_digits(text).strip()) if p]
    if len(parts) != 3: return None
    try:
        jy, jm, jd = [int(p) for p in parts]
        if jy < 100: jy += 1400
        if not (1 <= jm <= 12 and 1 <= jd <= j_month_len(jy, jm)): return None
        return date(*j2g(jy, jm, jd)).isoformat()
    except Exception:
        return None

def today_iso(): return date.today().isoformat()
def today_j(): return iso_to_j(today_iso())
def show_date(iso): return iso_to_j(iso)
def show_time(ts):
    """ساعت ثبت (HH:MM) از زمان کامل ثبت سند؛ برای سندهای قدیمی که ساعت نداشتند خالی است."""
    ts = str(ts or ""); return ts[11:16] if len(ts) >= 16 else ""
def _inv_time(inv):
    try: return show_time(inv["created_at"])
    except Exception: return ""
def ensure_time_columns(con):
    """ثبت خودکار زمان ایجاد برای جدول‌هایی که ستون زمان نداشتند (دریافت‌ها، چک‌ها، پرداخت به تأمین‌کننده): ستون + تریگر؛ هیچ کد ثبتی عوض نمی‌شود."""
    for t in ("payments", "cheques", "supplier_payments"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        if not cols: continue
        if "created_at" not in cols: con.execute(f"ALTER TABLE {t} ADD COLUMN created_at TEXT DEFAULT ''")
        con.execute(f"CREATE TRIGGER IF NOT EXISTS trg_{t}_time AFTER INSERT ON {t} WHEN NEW.created_at IS NULL OR NEW.created_at='' "
                    f"BEGIN UPDATE {t} SET created_at=strftime('%Y-%m-%dT%H:%M:%S','now','localtime') WHERE id=NEW.id; END")

def money(value):
    try:
        n = int(round(float(value)))
    except Exception:
        return "0"
    s = f"{abs(n):,}"
    return f"‪-{s}‬" if n < 0 else s

def fmt_qty(q):
    try:
        q = float(q)
        return str(int(q)) if q == int(q) else f"{q:.3f}".rstrip("0").rstrip(".")
    except Exception:
        return str(q)

def esc(s):
    return _html.escape(str(s if s is not None else "")).replace("\n", "<br>")

# ───────────────────────────── Database ─────────────────────────────
def connect_db():
    if NET_MODE == "client": return netdb.RemoteConnection()      # کلاینت هرگز فایل دیتابیس را باز نمی‌کند
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    safety.apply_pragmas(con)      # WAL + synchronous=FULL + busy_timeout: ذخیره‌ی لحظه‌ای و مقاوم در برابر هنگ/قطع برق
    return con

@contextmanager
def db():
    con = connect_db()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback(); raise
    finally:
        con.close()

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS customers(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT DEFAULT '',
          address TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS invoices(
          id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_no TEXT UNIQUE NOT NULL,
          customer_id INTEGER NOT NULL, invoice_date TEXT NOT NULL, kind TEXT NOT NULL,
          due_date TEXT DEFAULT '', description TEXT DEFAULT '', discount REAL DEFAULT 0,
          paid_initial REAL DEFAULT 0, notes TEXT DEFAULT '', created_at TEXT NOT NULL,
          FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS invoice_items(
          id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL, product_name TEXT DEFAULT '', description TEXT DEFAULT '',
          quantity REAL DEFAULT 1, unit_price REAL DEFAULT 0,
          FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS payments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
          invoice_id INTEGER, payment_date TEXT NOT NULL, amount REAL NOT NULL,
          method TEXT NOT NULL, reference TEXT DEFAULT '', notes TEXT DEFAULT '',
          cheque_id INTEGER,
          FOREIGN KEY(customer_id) REFERENCES customers(id),
          FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        );
        CREATE TABLE IF NOT EXISTS cheques(
          id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
          invoice_id INTEGER, cheque_no TEXT DEFAULT '', bank TEXT DEFAULT '',
          issuer TEXT DEFAULT '', amount REAL NOT NULL, due_date TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'نزد ما', notes TEXT DEFAULT '',
          FOREIGN KEY(customer_id) REFERENCES customers(id),
          FOREIGN KEY(invoice_id) REFERENCES invoices(id)
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS product_memory(
          id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          description TEXT DEFAULT '', last_price REAL DEFAULT 0, last_qty REAL DEFAULT 1,
          uses INTEGER DEFAULT 0, last_used TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS product_customer_price(
          customer_id INTEGER NOT NULL, key TEXT NOT NULL, price REAL NOT NULL, last_used TEXT DEFAULT '',
          PRIMARY KEY(customer_id, key)
        );
        CREATE TABLE IF NOT EXISTS voice_alias(
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, spoken_key TEXT NOT NULL, spoken TEXT NOT NULL,
          target TEXT NOT NULL, uses INTEGER DEFAULT 1, UNIQUE(kind, spoken_key)
        );
        CREATE TABLE IF NOT EXISTS voice_macro(
          id INTEGER PRIMARY KEY AUTOINCREMENT, trigger_key TEXT UNIQUE NOT NULL, trigger TEXT NOT NULL, customer TEXT DEFAULT '',
          kind TEXT DEFAULT '', items TEXT DEFAULT '', discount REAL DEFAULT 0, proforma INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_items_invoice ON invoice_items(invoice_id, row_no);
        CREATE INDEX IF NOT EXISTS idx_inv_customer ON invoices(customer_id);
        CREATE INDEX IF NOT EXISTS idx_inv_date ON invoices(invoice_date);
        CREATE INDEX IF NOT EXISTS idx_pay_invoice ON payments(invoice_id);
        CREATE INDEX IF NOT EXISTS idx_pay_customer ON payments(customer_id);
        CREATE INDEX IF NOT EXISTS idx_chq_customer ON cheques(customer_id);
        """)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(payments)")]
        if "cheque_id" not in cols:
            con.execute("ALTER TABLE payments ADD COLUMN cheque_id INTEGER")
        icols = [r["name"] for r in con.execute("PRAGMA table_info(invoices)")]
        if "is_proforma" not in icols:
            con.execute("ALTER TABLE invoices ADD COLUMN is_proforma INTEGER DEFAULT 0")
        sms_panel.ensure_tables(con); kardex.ensure_tables(con); auth.ensure_tables(con); safety.ensure_tables(con); retdb.ensure_tables(con); barcodes.ensure_tables(con); purchases.ensure_tables(con); ensure_time_columns(con)
        if "discount" not in [r["name"] for r in con.execute("PRAGMA table_info(invoice_items)")]:
            con.execute("ALTER TABLE invoice_items ADD COLUMN discount REAL DEFAULT 0")
        con.execute("UPDATE invoices SET kind='اعتباری' WHERE kind='نسیه'")
        con.execute("UPDATE settings SET value='اعتباری' WHERE key='pref_default_kind' AND value='نسیه'")
        if "discount" not in [r["name"] for r in con.execute("PRAGMA table_info(payments)")]:
            con.execute("ALTER TABLE payments ADD COLUMN discount REAL DEFAULT 0")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('next_proforma','1')")
        if not con.execute("SELECT value FROM settings WHERE key='next_invoice'").fetchone():
            con.execute("INSERT INTO settings(key,value) VALUES('next_invoice','1001')")
        # one-time: a ready "cash customer" used as the default for new invoices
        if not con.execute("SELECT 1 FROM settings WHERE key='pref_cash_customer_done'").fetchone():
            row = con.execute("SELECT id FROM customers WHERE name=?", ("مشتری نقدی",)).fetchone()
            cid = row["id"] if row else con.execute("INSERT INTO customers(name,created_at) VALUES(?,?)",
                                                    ("مشتری نقدی", datetime.now().isoformat(timespec="seconds"))).lastrowid
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('pref_default_customer',?)", (str(cid),))
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('pref_default_kind','نقدی')")
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pref_cash_customer_done','1')")
        # one-time: learn products from invoices that already exist
        if not con.execute("SELECT 1 FROM settings WHERE key='pref_memory_backfilled'").fetchone():
            rebuild_product_memory(con)
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pref_memory_backfilled','1')")
        maintenance.ensure_indexes(con)

# ── small preferences (stored in the settings table) ──
SECRET_PREFS = {"sms_api_key", "google_key"}   # روی ویندوز با DPAPI رمز ذخیره می‌شوند (secure.py)

def get_pref(key, default=""):
    try:
        with db() as con:
            r = con.execute("SELECT value FROM settings WHERE key=?", ("pref_" + key,)).fetchone()
        if not r: return default
        return secure.unprotect(r["value"]) if key in SECRET_PREFS else r["value"]
    except Exception:
        return default

def set_pref(key, value):
    value = secure.protect(str(value)) if key in SECRET_PREFS else str(value)
    with db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", ("pref_" + key, value))

def migrate_secrets():
    """کلیدهای قدیمی که هنوز متن ساده‌اند را یک بار رمز می‌کند."""
    if not secure.available(): return
    for k in SECRET_PREFS:
        with db() as con: r = con.execute("SELECT value FROM settings WHERE key=?", ("pref_" + k,)).fetchone()
        if r and r["value"] and not r["value"].startswith(secure.PREFIX): set_pref(k, r["value"])

# ── backups ──
def _sqlite_copy(src_path, dst_path):
    """Consistent copy of the database using SQLite's own backup API."""
    src = sqlite3.connect(str(src_path)); dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()

def auto_backup(force=False, ignore_pref=False):
    """Backup into <data>/backups. Skipped when the database has not changed since the last backup.
    Keeps only the newest N files (setting). Returns the new file path or None."""
    if not force and not ignore_pref and get_pref("backup_on_exit", "1") != "1": return None
    if not DB_PATH.exists(): return None
    stamp = safety.data_stamp(DB_PATH)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    marker = BACKUP_DIR / ".last_stamp"
    if not force and marker.exists() and marker.read_text(encoding="utf-8").strip() == stamp: return None
    dest = BACKUP_DIR / f"factordesk-{datetime.now():%Y%m%d-%H%M%S}.db"
    _sqlite_copy(DB_PATH, dest)
    try: keep = max(3, int(float(get_pref("backup_keep", "30") or 30)))
    except Exception: keep = 30
    for old in sorted(BACKUP_DIR.glob("factordesk-*.db"))[:-keep]:
        try: old.unlink()
        except Exception: pass
    marker.write_text(stamp, encoding="utf-8")
    cloud = get_pref("cloud_dir", "").strip()
    if cloud:                      # کپی در پوشه‌ی همگام‌شده با فضای ابری؛ خطا نباید پشتیبان محلی را خراب کند
        try:
            maintenance.copy_to_cloud(dest, cloud, keep); set_pref("cloud_status", f"{iso_to_j(today_iso())} {datetime.now():%H:%M} — موفق")
        except Exception as e:
            set_pref("cloud_status", f"{iso_to_j(today_iso())} {datetime.now():%H:%M} — خطا: {e}")
    return dest

# ── product memory: learns every invoice row and suggests while typing ──
_NORM_TBL = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا", "ؤ": "و",
                           "\u200c": " ", "\u200f": None, "\u200e": None, "\u202a": None, "\u202c": None})
def norm_key(text):
    """Normalise Persian/Arabic letter variants, ZWNJ, digits and spacing so 'روغن  موتور' == 'روغن‌موتور'."""
    t = norm_digits(str(text or "")).translate(_NORM_TBL)
    t = re.sub("[\u064B-\u065F\u0670]", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()

def learn_item(con, customer_id, name, desc, qty, price, when, bump=True):
    key = norm_key(name)
    if not key: return
    con.execute("""INSERT INTO product_memory(key,name,description,last_price,last_qty,uses,last_used) VALUES(?,?,?,?,?,1,?)
        ON CONFLICT(key) DO UPDATE SET name=excluded.name,
          description=CASE WHEN excluded.description<>'' THEN excluded.description ELSE product_memory.description END,
          last_price=CASE WHEN excluded.last_price>0 THEN excluded.last_price ELSE product_memory.last_price END,
          last_qty=excluded.last_qty, uses=product_memory.uses+?, last_used=MAX(product_memory.last_used, excluded.last_used)""",
        (key, str(name).strip(), str(desc or "").strip(), float(price or 0), float(qty or 1), str(when or ""), 1 if bump else 0))
    if customer_id and float(price or 0) > 0:
        con.execute("INSERT OR REPLACE INTO product_customer_price(customer_id,key,price,last_used) VALUES(?,?,?,?)",
                    (customer_id, key, float(price), str(when or "")))

def rebuild_product_memory(con):
    con.execute("DELETE FROM product_memory"); con.execute("DELETE FROM product_customer_price")
    rows = con.execute("""SELECT i.customer_id cid, i.invoice_date d, it.product_name n, it.description ds, it.quantity q, it.unit_price p
        FROM invoice_items it JOIN invoices i ON i.id=it.invoice_id ORDER BY i.invoice_date, i.id, it.row_no""").fetchall()
    for r in rows: learn_item(con, r["cid"], r["n"], r["ds"], r["q"], r["p"], r["d"], True)

class ProductMemory:
    """In-memory view of what was typed before. Ranking = how often + how recently + sold to this customer;
    typo-tolerant fallback via difflib. Fully offline, no model files."""
    def __init__(self):
        self.rows = []; self.by_key = {}; self.cust = {}; self.reload()
    def reload(self):
        try:
            with db() as con:
                self.rows = [dict(r) for r in con.execute("SELECT * FROM product_memory")]
                cp = con.execute("SELECT customer_id,key,price FROM product_customer_price").fetchall()
        except Exception:
            self.rows, cp = [], []
        self.by_key = {r["key"]: r for r in self.rows}
        self.cust = {(r["customer_id"], r["key"]): r["price"] for r in cp}
    def suggest(self, text, customer_id=None, limit=8):
        q = norm_key(text)
        if not q: return []
        toks = q.split(); today = date.today(); scored = []
        for r in self.rows:
            k = r["key"]
            if not all(t in k for t in toks): continue
            sc = math.log1p(r["uses"])
            if k.startswith(q): sc += 3
            elif any(w.startswith(toks[0]) for w in k.split()): sc += 1.5
            if customer_id is not None and (customer_id, k) in self.cust: sc += 2
            try: sc += 1.0 / (1 + max(0, (today - date.fromisoformat(str(r["last_used"])[:10])).days) / 30.0)
            except Exception: pass
            scored.append((sc, r["name"]))
        scored.sort(key=lambda x: -x[0])
        names = [n for _, n in scored[:limit]]
        if not names and len(q) >= 3:
            import difflib
            names = [self.by_key[k]["name"] for k in difflib.get_close_matches(q, list(self.by_key), n=limit, cutoff=0.6)]
        return names
    def lookup(self, name, customer_id=None):
        r = self.by_key.get(norm_key(name))
        if not r: return None
        price = self.cust.get((customer_id, r["key"]), r["last_price"])
        return dict(description=r["description"] or "", price=float(price or 0), qty=r["last_qty"])

# ── appearance settings: global part + per-paper profiles (A4 / A5) ──
SHOW_KEYS = [("show_row_no", "ستون ردیف"), ("show_desc", "ستون شرح کالا"), ("show_logo", "لوگو"),
             ("show_shop_address", "آدرس فروشگاه"), ("show_customer_phone", "تلفن مشتری"),
             ("show_customer_address", "آدرس مشتری"), ("show_due", "تاریخ سررسید"),
             ("show_payments", "فهرست دریافت‌های فاکتور"), ("show_balance", "پرداخت‌شده و مانده فاکتور"),
             ("show_prev_balance", "مانده کل حساب مشتری"), ("show_notes", "توضیحات فاکتور"),
             ("show_signatures", "محل امضا"), ("zebra", "ردیف‌های راه‌راه")]

GLOBAL_DEFAULTS = {
    "title": "فاکتور فروش", "proforma_title": "پیش‌فاکتور",
    "proforma_note": "قیمت‌ها تا زمان صدور فاکتور نهایی قابل تغییر است.",
    "shop_name": "", "shop_phone": "", "shop_address": "", "logo_path": "",
    "accent": "#286B4A", "font": "Tahoma", "layout": "classic", "unit": "تومان", "footer": "",
    "receipt_width": 80, "receipt_font_size": 9, "receipt_margin": 0, "receipt_margin_top": 0, "receipt_margin_bottom": 2, "receipt_margin_side": 0, "receipt_direct": True, "receipt_show_logo": False, "receipt_printer": "",
    "seller_label": "امضای فروشنده", "buyer_label": "امضای خریدار",
    "paper": "A4", "report_paper": "A4",
}

INVOICE_TEMPLATES = [
    ("classic", "کلاسیک — جدول خط‌دار"),
    ("modern", "مدرن — ساده با خط‌های کم‌رنگ"),
    ("compact", "فشرده — ردیف‌های بیشتر در هر صفحه (کم‌مصرف)"),
    ("formal", "رسمی سیاه‌وسفید — مناسب پرینتر لیزری"),
    ("ledger", "دفتری — شبکه‌ی پررنگ و واضح"),
]

def _invoice_theme(layout, acc):
    t = dict(grid=True, bcol="#4d5d53", head_bg=acc, head_fg="#ffffff", zebra="#f4f7f5", tot_bg="#e4eee7",
             info_bg="#f2f6f3", title_color=acc, band=acc, pad_add=0, gap=0, title_scale=1.0)
    if layout == "modern": t.update(grid=False, bcol="#d5ddd7")
    elif layout == "compact": t.update(bcol="#555555", pad_add=-2, gap=2, title_scale=0.7, band=None)
    elif layout == "formal": t.update(bcol="#000000", head_bg="#d9d9d9", head_fg="#000000", zebra="#f2f2f2", tot_bg="#e6e6e6",
                                      info_bg="#ffffff", title_color="#000000", band="#000000")
    elif layout == "ledger": t.update(bcol="#000000", pad_add=-1, tot_bg="#d9e6dd", info_bg="#ffffff")
    return t

def _shows(**over):
    d = {k: True for k, _ in SHOW_KEYS}; d["show_prev_balance"] = False; d.update(over); return d

PAPER_DEFAULTS = {
    "A4": dict(font_size=10, table_font_size=10, title_size=18, cell_pad=4, margin=8, logo_h=70, totals_width=42,
               col_row=6, col_qty=8, col_price=16, col_amount=18, orientation="portrait",
               rep_font_size=9, rep_pad=4, rep_orient="auto", **_shows()),
    "A5": dict(font_size=8, table_font_size=8, title_size=13, cell_pad=2, margin=6, logo_h=38, totals_width=50,
               col_row=6, col_qty=8, col_price=16, col_amount=18, orientation="portrait",
               rep_font_size=7, rep_pad=2, rep_orient="auto", **_shows(show_payments=False)),
}

def default_raw_style():
    return {"g": dict(GLOBAL_DEFAULTS), "paper": {p: dict(v) for p, v in PAPER_DEFAULTS.items()}}

def load_raw_style():
    raw = default_raw_style()
    try:
        with db() as con:
            row = con.execute("SELECT value FROM settings WHERE key='invoice_style'").fetchone()
        if row and row["value"]:
            data = json.loads(row["value"])
            if "g" in data:
                raw["g"].update({k: v for k, v in data["g"].items() if k in GLOBAL_DEFAULTS})
                for p in raw["paper"]:
                    raw["paper"][p].update({k: v for k, v in data.get("paper", {}).get(p, {}).items() if k in raw["paper"][p]})
            else:  # older flat format
                raw["g"].update({k: v for k, v in data.items() if k in GLOBAL_DEFAULTS})
    except Exception:
        pass
    for k in ("paper", "report_paper"):
        if raw["g"][k] not in raw["paper"]: raw["g"][k] = "A4"
    return raw

def save_raw_style(raw):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('invoice_style',?)", (json.dumps(raw, ensure_ascii=False),))

def flatten_style(raw, kind="invoice", paper=None):
    g = raw["g"]; name = paper or (g["paper"] if kind == "invoice" else g["report_paper"]); p = raw["paper"][name]
    out = {**g, **p}; out["paper"] = name
    if kind == "report":
        out["font_size"] = p["rep_font_size"]; out["table_font_size"] = p["rep_font_size"]
        out["cell_pad"] = p["rep_pad"]; out["orientation"] = p["rep_orient"]
    return out

def load_style(kind="invoice"):
    return flatten_style(load_raw_style(), kind)

# ── figures ──
def invoice_rows(con, customer_id=None, d1="", d2="", proforma=0):
    q = """SELECT i.*, c.name cname, c.phone cphone,
        COALESCE((SELECT SUM(quantity*unit_price) FROM invoice_items WHERE invoice_id=i.id),0) sub,
        COALESCE((SELECT SUM(amount+COALESCE(discount,0)) FROM payments WHERE invoice_id=i.id),0)
          + COALESCE((SELECT SUM(ri.quantity*ri.unit_price) FROM return_items ri JOIN returns r ON r.id=ri.return_id WHERE r.invoice_id=i.id),0) linked
        FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE 1=1"""
    q += " AND COALESCE(i.is_proforma,0)=?"; p = [proforma]
    if customer_id: q += " AND i.customer_id=?"; p.append(customer_id)
    if d1: q += " AND i.invoice_date>=?"; p.append(d1)
    if d2: q += " AND i.invoice_date<=?"; p.append(d2)
    q += " ORDER BY i.invoice_date, i.id"
    out = []
    for r in con.execute(q, p):
        d = dict(r)
        d["total"] = max(0.0, float(r["sub"] or 0) - float(r["discount"] or 0))
        d["paid"] = float(r["paid_initial"] or 0) + float(r["linked"] or 0)
        d["remain"] = d["total"] - d["paid"]
        out.append(d)
    return out

def invoice_total(invoice_id, con):
    r = con.execute("""SELECT i.discount, COALESCE((SELECT SUM(quantity*unit_price) FROM invoice_items WHERE invoice_id=i.id),0) s
                       FROM invoices i WHERE i.id=?""", (invoice_id,)).fetchone()
    return max(0.0, float(r["s"]) - float(r["discount"] or 0)) if r else 0.0

def customer_figures(con):
    figs = {}
    for c in con.execute("SELECT id,name,phone,address FROM customers ORDER BY name"):
        figs[c["id"]] = dict(id=c["id"], name=c["name"], phone=c["phone"] or "", address=c["address"] or "",
                             n=0, total=0.0, initial=0.0, pays=0.0, disc=0.0, returns=0.0, received=0.0, balance=0.0,
                             overdue=0.0, open_chq=0.0, oldest="")
    today = today_iso()
    for inv in invoice_rows(con):
        f = figs[inv["customer_id"]]
        f["n"] += 1; f["total"] += inv["total"]; f["initial"] += float(inv["paid_initial"] or 0)
        if inv["remain"] > 0.5:
            if not f["oldest"] or inv["invoice_date"] < f["oldest"]: f["oldest"] = inv["invoice_date"]
            if inv["due_date"] and inv["due_date"] < today: f["overdue"] += inv["remain"]
    for r in con.execute("SELECT customer_id, SUM(amount) s, SUM(COALESCE(discount,0)) d FROM payments GROUP BY customer_id"):
        figs[r["customer_id"]]["pays"] = float(r["s"] or 0); figs[r["customer_id"]]["disc"] = float(r["d"] or 0)
    for r in con.execute("SELECT r.customer_id, SUM(ri.quantity*ri.unit_price) s FROM returns r JOIN return_items ri ON ri.return_id=r.id GROUP BY r.customer_id"):
        figs[r["customer_id"]]["returns"] = float(r["s"] or 0)
    ph = ",".join("?" * len(CHQ_CLOSED))
    for r in con.execute(f"SELECT customer_id, SUM(amount) s FROM cheques WHERE status NOT IN ({ph}) GROUP BY customer_id", CHQ_CLOSED):
        figs[r["customer_id"]]["open_chq"] = float(r["s"] or 0)
    for f in figs.values():
        f["received"] = f["initial"] + f["pays"]
        f["balance"] = f["total"] - f["received"] - f["disc"] - f["returns"]
    return figs

# ───────────────────────────── Reports ─────────────────────────────
def _rep(title, headers, rows, money_cols=(), qty_cols=(), totals=None, bold=(), cids=None, subtitle="", nosort=False):
    return dict(title=title, subtitle=subtitle, headers=headers, rows=rows, money_cols=set(money_cols),
                qty_cols=set(qty_cols), totals=totals or [], bold=set(bold), cids=cids, nosort=nosort,
                landscape=len(headers) >= 8)

def _range_text(d1, d2):
    if d1 and d2: return f"از تاریخ {iso_to_j(d1)} تا {iso_to_j(d2)}"
    if d1: return f"از تاریخ {iso_to_j(d1)}"
    if d2: return f"تا تاریخ {iso_to_j(d2)}"
    return ""

def rep_debtors_summary(con):
    figs = [f for f in customer_figures(con).values() if f["balance"] > 0.5]
    figs.sort(key=lambda f: -f["balance"])
    rows = [[n, f["name"], f["phone"], f["n"], f["total"], f["received"], f["balance"], f["overdue"],
             f["open_chq"], iso_to_j(f["oldest"])] for n, f in enumerate(figs, 1)]
    totals = [("تعداد بدهکاران", str(len(figs))), ("جمع کل مطالبات", sum(f["balance"] for f in figs)),
              ("جمع سررسید گذشته", sum(f["overdue"] for f in figs)),
              ("جمع چک‌های در جریان این مشتریان", sum(f["open_chq"] for f in figs))]
    return _rep("گزارش بدهکاران — کلی",
                ["ردیف", "مشتری", "تلفن", "تعداد فاکتور", "جمع فاکتورها", "دریافتی‌ها", "مانده بدهی",
                 "سررسید گذشته", "چک‌های در جریان", "قدیمی‌ترین فاکتور باز"],
                rows, money_cols={4, 5, 6, 7, 8}, totals=totals, cids=[f["id"] for f in figs],
                subtitle=f"وضعیت تا امروز {today_j()}")

def rep_debtors_detail(con):
    figs = customer_figures(con)
    debtors = sorted([f for f in figs.values() if f["balance"] > 0.5], key=lambda f: -f["balance"])
    invs = invoice_rows(con)
    unlinked = {r["customer_id"]: float(r["s"] or 0) for r in con.execute(
        "SELECT customer_id, SUM(amount+COALESCE(discount,0)) s FROM payments WHERE invoice_id IS NULL GROUP BY customer_id")}
    unl_ret = {r[0]: float(r[1] or 0) for r in con.execute(
        "SELECT r.customer_id, SUM(ri.quantity*ri.unit_price) FROM returns r JOIN return_items ri ON ri.return_id=r.id WHERE r.invoice_id IS NULL GROUP BY r.customer_id")}
    today = today_iso()
    rows, bold, cids = [], [], []
    for f in debtors:
        tot = paid = 0.0
        for inv in [i for i in invs if i["customer_id"] == f["id"] and abs(i["remain"]) > 0.5]:
            late = inv["remain"] > 0.5 and inv["due_date"] and inv["due_date"] < today
            rows.append([f["name"], inv["invoice_no"], iso_to_j(inv["invoice_date"]), iso_to_j(inv["due_date"]),
                         inv["total"], inv["paid"], inv["remain"], "سررسید گذشته" if late else "باز"])
            cids.append(f["id"]); tot += inv["total"]; paid += inv["paid"]
        u = unlinked.get(f["id"], 0.0)
        if u:
            rows.append([f["name"], "دریافت‌های بدون اتصال به فاکتور", "", "", None, u, -u, ""])
            cids.append(f["id"]); paid += u
        ur = unl_ret.get(f["id"], 0.0)
        if ur:
            rows.append([f["name"], "مرجوعی بدون اتصال به فاکتور", "", "", None, ur, -ur, ""])
            cids.append(f["id"]); paid += ur
        rows.append([f"جمع «{f['name']}»", "", "", "", tot, paid, f["balance"], ""])
        bold.append(len(rows) - 1); cids.append(f["id"])
    return _rep("گزارش بدهکاران — جزئی (به تفکیک فاکتور)",
                ["مشتری", "شماره فاکتور", "تاریخ", "سررسید", "مبلغ فاکتور", "پرداخت‌شده", "مانده", "وضعیت"],
                rows, money_cols={4, 5, 6}, bold=bold, cids=cids,
                totals=[("تعداد بدهکاران", str(len(debtors))), ("جمع کل مطالبات", sum(f["balance"] for f in debtors))],
                subtitle=f"وضعیت تا امروز {today_j()}")

def rep_statement(con, cid, d1="", d2=""):
    c = con.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    ev = []  # (date, order, text, debit, credit)
    for inv in invoice_rows(con, cid):
        ev.append((inv["invoice_date"], 0, f"فاکتور شماره {inv['invoice_no']} ({inv['kind']})", inv["total"], 0.0))
        if float(inv["paid_initial"] or 0) > 0:
            ev.append((inv["invoice_date"], 1, f"پرداخت اولیه فاکتور {inv['invoice_no']}", 0.0, float(inv["paid_initial"])))
    for p in con.execute("""SELECT p.*, i.invoice_no, ch.due_date chq_due, ch.bank chq_bank FROM payments p LEFT JOIN invoices i ON i.id=p.invoice_id
                            LEFT JOIN cheques ch ON ch.id=p.cheque_id WHERE p.customer_id=?""", (cid,)):
        t = f"دریافت ({p['method']})" + (f" بابت فاکتور {p['invoice_no']}" if p["invoice_no"] else "")
        extra = " ".join(x for x in [p["reference"], p["notes"]] if x)
        if p["chq_due"]:
            t = f"چک شماره {p['reference'] or '—'}" + (f" بانک {p['chq_bank']}" if p["chq_bank"] else "") + f" (سررسید {iso_to_j(p['chq_due'])})" + (f" بابت فاکتور {p['invoice_no']}" if p["invoice_no"] else "")
            extra = ""
        if float(p["amount"]) > 0: ev.append((p["payment_date"], 2, t + (f" — {extra}" if extra else ""), 0.0, float(p["amount"])))
        if float(p["discount"] or 0) > 0:
            ev.append((p["payment_date"], 2, "تخفیف دریافت" + (f" بابت فاکتور {p['invoice_no']}" if p["invoice_no"] else ""), 0.0, float(p["discount"])))
    for r in con.execute("""SELECT r.return_no, r.return_date, i.invoice_no, COALESCE(SUM(ri.quantity*ri.unit_price),0) t FROM returns r
                            LEFT JOIN invoices i ON i.id=r.invoice_id LEFT JOIN return_items ri ON ri.return_id=r.id
                            WHERE r.customer_id=? GROUP BY r.id""", (cid,)):
        ev.append((r["return_date"], 2, f"مرجوعی شماره {r['return_no']}" + (f" بابت فاکتور {r['invoice_no']}" if r["invoice_no"] else ""), 0.0, float(r["t"])))
    ev.sort(key=lambda e: (e[0], e[1]))
    bal, opening, rows = 0.0, None, []
    for dt, _, text, deb, cre in ev:
        if d1 and dt < d1:
            bal += deb - cre; continue
        if d2 and dt > d2: continue
        if opening is None and d1:
            opening = bal
            rows.append(["", "مانده از قبل", None, None, bal])
        bal += deb - cre
        rows.append([iso_to_j(dt), text, deb or None, cre or None, bal])
    if d1 and opening is None:
        rows.insert(0, ["", "مانده از قبل", None, None, bal])
    open_chq = con.execute(f"SELECT COALESCE(SUM(amount),0) FROM cheques WHERE customer_id=? AND status NOT IN ({','.join('?'*len(CHQ_CLOSED))})",
                           (cid, *CHQ_CLOSED)).fetchone()[0]
    f = customer_figures(con)[cid]
    sub = " | ".join(x for x in [f"تلفن: {c['phone']}" if c["phone"] else "", f"آدرس: {c['address']}" if c["address"] else "",
                                 _range_text(d1, d2)] if x)
    return _rep(f"صورت‌حساب مشتری: {c['name']}", ["تاریخ", "شرح", "بدهکار (فاکتور)", "بستانکار (دریافت / تخفیف / مرجوعی)", "مانده"],
                rows, money_cols={2, 3, 4}, subtitle=sub,
                totals=[("جمع فاکتورها", f["total"]), ("جمع دریافتی‌ها", f["received"]),
                        ("جمع تخفیف دریافت‌ها", f["disc"]), ("جمع مرجوعی‌ها", f["returns"]),
                        ("مانده حساب", f["balance"]), ("چک‌های در جریان (کسر نشده)", float(open_chq))])

def rep_summary(con):
    figs = customer_figures(con)
    total = sum(f["total"] for f in figs.values()); rec = sum(f["received"] for f in figs.values())
    ph = ",".join("?" * len(CHQ_CLOSED))
    chq = con.execute(f"SELECT COALESCE(SUM(amount),0) FROM cheques WHERE status NOT IN ({ph})", CHQ_CLOSED).fetchone()[0]
    late = con.execute(f"SELECT COUNT(*) FROM cheques WHERE due_date<? AND status NOT IN ({ph})", (today_iso(), *CHQ_CLOSED)).fetchone()[0]
    ninv = con.execute("SELECT COUNT(*) FROM invoices WHERE COALESCE(is_proforma,0)=0").fetchone()[0]
    rows = [["تعداد مشتریان", str(len(figs))], ["تعداد فاکتورها", str(ninv)],
            ["مجموع فاکتورها پس از تخفیف", money(total)], ["پرداخت‌ها و دریافت‌های ثبت‌شده", money(rec)],
            ["تخفیف دریافت‌ها", money(sum(f["disc"] for f in figs.values()))], ["مرجوعی‌ها", money(sum(f["returns"] for f in figs.values()))],
            ["مانده مطالبات", money(total - rec - sum(f["disc"] for f in figs.values()) - sum(f["returns"] for f in figs.values()))],
            ["تعداد مشتریان بدهکار", str(sum(1 for f in figs.values() if f["balance"] > 0.5))],
            ["جمع چک‌های در جریان", money(chq)], ["تعداد چک‌های سررسید گذشته و باز", str(late)]]
    bold = []
    top = sorted([f for f in figs.values() if f["balance"] > 0.5], key=lambda f: -f["balance"])[:10]
    if top:
        rows.append(["۱۰ بدهکار اول", ""]); bold.append(len(rows) - 1)
        rows += [[f["name"], money(f["balance"])] for f in top]
    return _rep("گزارش کلی وضعیت", ["شرح", "مقدار (تومان)"], rows, bold=bold, subtitle=f"تاریخ گزارش: {today_j()}", nosort=True)

def rep_invoices(con, cid, d1, d2):
    invs = invoice_rows(con, cid, d1, d2)
    rows = [[i["invoice_no"], iso_to_j(i["invoice_date"]), i["cname"], i["kind"], i["total"], i["paid"], i["remain"],
             iso_to_j(i["due_date"])] for i in invs]
    return _rep("گزارش فاکتورها — کلی", ["شماره", "تاریخ", "مشتری", "نوع", "مبلغ کل", "پرداخت‌شده", "مانده", "سررسید"],
                rows, money_cols={4, 5, 6}, subtitle=_range_text(d1, d2),
                totals=[("تعداد فاکتور", str(len(invs))), ("جمع فاکتورها", sum(i["total"] for i in invs)),
                        ("جمع پرداخت‌شده", sum(i["paid"] for i in invs)), ("جمع مانده", sum(i["remain"] for i in invs))])

def rep_invoices_detail(con, cid, d1, d2):
    rows, bold = [], []
    invs = invoice_rows(con, cid, d1, d2)
    for inv in invs:
        for it in con.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY row_no", (inv["id"],)):
            rows.append([inv["invoice_no"], iso_to_j(inv["invoice_date"]), inv["cname"], it["row_no"], it["product_name"],
                         it["description"], it["quantity"], it["unit_price"], it["quantity"] * it["unit_price"]])
        rows.append([inv["invoice_no"], "", "جمع این فاکتور (پس از تخفیف)", "", "", "", None,
                     float(inv["discount"] or 0) or None, inv["total"]])
        bold.append(len(rows) - 1)
    return _rep("گزارش فاکتورها — جزئی (با اقلام)",
                ["شماره", "تاریخ", "مشتری", "ردیف", "نام کالا", "شرح", "تعداد", "قیمت واحد / تخفیف", "مبلغ"],
                rows, money_cols={7, 8}, qty_cols={6}, bold=bold, subtitle=_range_text(d1, d2),
                totals=[("تعداد فاکتور", str(len(invs))), ("جمع کل فاکتورها", sum(i["total"] for i in invs))])

def rep_payments(con, cid, d1, d2):
    q = """SELECT p.*, c.name cname, i.invoice_no FROM payments p JOIN customers c ON c.id=p.customer_id
           LEFT JOIN invoices i ON i.id=p.invoice_id WHERE 1=1"""
    p = []
    if cid: q += " AND p.customer_id=?"; p.append(cid)
    if d1: q += " AND p.payment_date>=?"; p.append(d1)
    if d2: q += " AND p.payment_date<=?"; p.append(d2)
    items = con.execute(q + " ORDER BY p.payment_date, p.id", p).fetchall()
    rows = [[iso_to_j(x["payment_date"]), x["cname"], x["invoice_no"] or "", x["amount"], x["method"], x["reference"], x["notes"], float(x["discount"] or 0) or None] for x in items]
    return _rep("گزارش دریافت‌ها", ["تاریخ", "مشتری", "فاکتور", "مبلغ", "روش", "شماره پیگیری", "توضیحات", "تخفیف"], rows,
                money_cols={3, 7}, subtitle=_range_text(d1, d2),
                totals=[("تعداد", str(len(items))), ("جمع دریافتی‌ها", sum(float(x["amount"]) for x in items)),
                        ("جمع تخفیف‌ها", sum(float(x["discount"] or 0) for x in items))])

def rep_returns(con, cid, d1, d2):
    q = """SELECT r.*, c.name cname, i.invoice_no,
           COALESCE((SELECT SUM(quantity*unit_price) FROM return_items WHERE return_id=r.id),0) total
           FROM returns r JOIN customers c ON c.id=r.customer_id LEFT JOIN invoices i ON i.id=r.invoice_id WHERE 1=1"""
    p = []
    if cid: q += " AND r.customer_id=?"; p.append(cid)
    if d1: q += " AND r.return_date>=?"; p.append(d1)
    if d2: q += " AND r.return_date<=?"; p.append(d2)
    items = con.execute(q + " ORDER BY r.return_date, r.id", p).fetchall()
    rows = [[x["return_no"], iso_to_j(x["return_date"]), x["cname"], x["invoice_no"] or "", x["total"], x["notes"]] for x in items]
    return _rep("گزارش مرجوعی‌ها", ["شماره", "تاریخ", "مشتری", "فاکتور", "جمع مرجوعی", "توضیحات"], rows,
                money_cols={4}, subtitle=_range_text(d1, d2),
                totals=[("تعداد", str(len(items))), ("جمع مرجوعی‌ها", sum(float(x["total"]) for x in items))])

def rep_cheques(con, cid, d1, d2):
    q = """SELECT ch.*, c.name cname, i.invoice_no FROM cheques ch JOIN customers c ON c.id=ch.customer_id
           LEFT JOIN invoices i ON i.id=ch.invoice_id WHERE 1=1"""
    p = []
    if cid: q += " AND ch.customer_id=?"; p.append(cid)
    if d1: q += " AND ch.due_date>=?"; p.append(d1)
    if d2: q += " AND ch.due_date<=?"; p.append(d2)
    items = con.execute(q + " ORDER BY ch.due_date, ch.id", p).fetchall()
    rows = [[iso_to_j(x["due_date"]), x["cname"], x["invoice_no"] or "", x["cheque_no"], x["bank"], x["issuer"], x["amount"], x["status"]] for x in items]
    totals = [("تعداد", str(len(items)))]
    for s in CHQ_STATUSES:
        t = sum(float(x["amount"]) for x in items if x["status"] == s)
        if t: totals.append((f"جمع «{s}»", t))
    return _rep("گزارش چک‌ها (بر اساس سررسید)", ["سررسید", "مشتری", "فاکتور", "شماره چک", "بانک", "صادرکننده", "مبلغ", "وضعیت"],
                rows, money_cols={6}, subtitle=_range_text(d1, d2), totals=totals)

REPORTS = [
    ("debtors_summary", "بدهکاران — کلی", dict(cust=False, dates=False)),
    ("debtors_detail", "بدهکاران — جزئی (به تفکیک فاکتور)", dict(cust=False, dates=False)),
    ("statement", "صورت‌حساب مشتری (ریز گردش حساب)", dict(cust=True, dates=True)),
    ("summary", "گزارش کلی وضعیت", dict(cust=False, dates=False)),
    ("invoices", "فاکتورها — کلی", dict(cust=True, dates=True)),
    ("invoices_detail", "فاکتورها — جزئی (با اقلام)", dict(cust=True, dates=True)),
    ("payments", "دریافت‌ها", dict(cust=True, dates=True)),
    ("returns", "مرجوعی‌ها", dict(cust=True, dates=True)),
    ("cheques", "چک‌ها", dict(cust=True, dates=True)),
]

def build_report(key, cid=None, d1="", d2=""):
    with db() as con:
        if key in ("debtors_summary", "debtors_detail"):
            rp = rep_debtors_summary(con) if key == "debtors_summary" else rep_debtors_detail(con); rp["debtor"] = True; return rp
        if key == "statement": return rep_statement(con, cid, d1, d2)
        if key == "summary": return rep_summary(con)
        if key == "invoices": return rep_invoices(con, cid, d1, d2)
        if key == "invoices_detail": return rep_invoices_detail(con, cid, d1, d2)
        if key == "payments": return rep_payments(con, cid, d1, d2)
        if key == "returns": return rep_returns(con, cid, d1, d2)
        if key == "cheques": return rep_cheques(con, cid, d1, d2)
    raise ValueError(key)

def cell_text(rep, c, v):
    if v is None or v == "": return ""
    if c in rep["money_cols"]: return money(v)
    if c in rep["qty_cols"]: return fmt_qty(v)
    return str(v)

def total_text(v):
    return money(v) if isinstance(v, (int, float)) else str(v)

# ───────────────────── printing / PDF (Qt rich text) ─────────────────────
def make_document(html, st):
    doc = QTextDocument()
    doc.setDefaultFont(QFont(st["font"], int(st["font_size"])))
    doc.setDocumentMargin(0)
    opt = QTextOption(); opt.setTextDirection(Qt.RightToLeft); doc.setDefaultTextOption(opt)
    doc.setHtml(html)
    return doc

def make_printer(st, landscape=False, pdf_path=None, receipt=False, receipt_height_mm=None):
    pr = QPrinter(QPrinter.HighResolution)
    if receipt:
        width = float(st.get("receipt_width", 80))
        height = float(receipt_height_mm or st.get("receipt_height", 120))
        height = max(25.0, min(height, 2000.0))
        pr.setPageSize(QPageSize(QSizeF(width, height), QPageSize.Millimeter))
        pr.setPageOrientation(QPageLayout.Portrait)
        side = float(st.get("receipt_margin_side", st.get("receipt_margin", 0)))
        top = float(st.get("receipt_margin_top", st.get("receipt_margin", 0)))
        bottom = float(st.get("receipt_margin_bottom", st.get("receipt_margin", 0)))
        pr.setPageMargins(QMarginsF(side, top, side, bottom), QPageLayout.Millimeter)
    else:
        pr.setPageSize(QPageSize(QPageSize.A5 if st.get("paper") == "A5" else QPageSize.A4))
        o = st.get("orientation", "portrait")
        land = True if o == "landscape" else (landscape if o == "auto" else False)
        pr.setPageOrientation(QPageLayout.Landscape if land else QPageLayout.Portrait)
        m = float(st.get("margin", 10))
        pr.setPageMargins(QMarginsF(m, m, m, m), QPageLayout.Millimeter)
    if pdf_path:
        pr.setOutputFormat(QPrinter.PdfFormat); pr.setOutputFileName(pdf_path)
    return pr

def _doc_dpi():
    """Resolution of QTextDocument layout units. QTextDocument.print_() assumes the same value
    (primary-screen logical DPI, normally 96), so using it keeps preview, PDF and raw printing identical."""
    try:
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            v = float(scr.logicalDotsPerInchX())
            if v > 0:
                return v
    except Exception:
        pass
    return 96.0

def _receipt_font_name(st):
    """Return a real installed font for receipt rendering (avoids Qt's missing-font warnings)."""
    requested = str(st.get("font") or "Tahoma")
    families = set(QFontDatabase.families())
    if requested in families:
        return requested
    for fallback in ("Tahoma", "Arial", "Noto Sans Arabic", "DejaVu Sans"):
        if fallback in families:
            return fallback
    return QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()

def make_receipt_document(d, st):
    """Build the receipt QTextDocument from the *global* style only.
    The receipt never needs the per-paper keys (font_size, table_font_size ...), so it can be called
    with raw['g'] or with the flattened style without KeyError."""
    safe = dict(st)
    safe["font"] = _receipt_font_name(safe)
    try:
        fs = int(safe.get("receipt_font_size", 9))
    except (TypeError, ValueError):
        fs = 9
    safe["receipt_font_size"] = max(5, fs)
    safe.setdefault("footer", "")
    doc = QTextDocument()
    doc.setDefaultFont(QFont(safe["font"], safe["receipt_font_size"]))
    doc.setDocumentMargin(0)
    opt = QTextOption(); opt.setTextDirection(Qt.RightToLeft); doc.setDefaultTextOption(opt)
    doc.setHtml(build_receipt_html(d, safe))
    return doc, safe

def _receipt_printable_mm(st):
    width_mm = float(st.get("receipt_width", 80))
    side_mm = max(0.0, float(st.get("receipt_margin_side", st.get("receipt_margin", 0))))
    return width_mm, side_mm, max(25.0, width_mm - 2 * side_mm)

def receipt_printer_for_document(doc, st):
    """Custom-height thermal page for preview / driver printing.
    Layout units are 1/dpi inch (see _doc_dpi), so the height is converted with the same dpi;
    the page is made exactly as tall as the content, so Qt never has to start a second page."""
    dpi = _doc_dpi()
    _w, _s, printable_mm = _receipt_printable_mm(st)
    doc.setTextWidth(printable_mm / 25.4 * dpi)
    h_units = max(1.0, doc.documentLayout().documentSize().height())
    top_mm = max(0.0, float(st.get("receipt_margin_top", 0)))
    bottom_mm = max(0.0, float(st.get("receipt_margin_bottom", 0)))
    height_mm = h_units / dpi * 25.4 + top_mm + bottom_mm + 3.0
    return make_printer(st, receipt=True, receipt_height_mm=height_mm)

def _receipt_image(doc, st):
    """Render the complete receipt to ONE bitmap at the print-head resolution (203 dpi).
    The printer then only receives pixels: no printer fonts, no page layout, no pagination."""
    width_mm, side_mm, printable_mm = _receipt_printable_mm(st)
    top_mm = max(0.0, float(st.get("receipt_margin_top", 0)))
    bottom_mm = max(0.0, float(st.get("receipt_margin_bottom", 2)))

    # Common thermal heads are 384 dots (58mm) and 576 dots (80mm) at 203dpi.
    max_dots = 384 if width_mm <= 58 else 576
    dpi = 203.0
    width_px = min(max_dots, max(8, int(round(printable_mm * dpi / 25.4))))

    src_dpi = _doc_dpi()                 # document units -> dots
    scale = dpi / src_dpi
    doc.setTextWidth(width_px / scale)   # text fills exactly the dots we send
    h_units = max(1.0, doc.documentLayout().documentSize().height())
    height_px = max(16, int(math.ceil(h_units * scale + (top_mm + bottom_mm) * dpi / 25.4)))
    img = QImage(width_px, height_px, QImage.Format_Grayscale8)
    img.fill(255)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.scale(scale, scale)
    painter.translate(0, top_mm / 25.4 * src_dpi)
    doc.drawContents(painter)
    painter.end()
    return img

RASTER_BAND_ROWS = 128   # rows per GS v 0 command (small bands are accepted by every ESC/POS firmware)

def _pack_raster_bands(gray, bytes_per_line, w, h, threshold=190, band_rows=RASTER_BAND_ROWS):
    """gray: Grayscale8 pixel bytes. Returns the ESC/POS body: several short GS v 0 commands
    back to back. A single huge GS v 0 makes many printers drop the tail of the data; the leftover
    bytes are then read as commands (random cut / feed / garbage) -> the receipt comes out in pieces."""
    row_bytes = (w + 7) // 8
    pad = b'0' * (row_bytes * 8 - w)
    table = bytes((49 if v < threshold else 48) for v in range(256))   # '1' = black dot
    out = bytearray()
    y = 0
    while y < h:
        n = min(band_rows, h - y)
        out += b'\x1dv0\x00' + bytes((row_bytes & 0xFF, (row_bytes >> 8) & 0xFF, n & 0xFF, (n >> 8) & 0xFF))
        for r in range(y, y + n):
            row = bytes(gray[r * bytes_per_line: r * bytes_per_line + w])
            bits = row.translate(table) + pad
            out += int(bits, 2).to_bytes(row_bytes, 'big')
        y += n
    return bytes(out)

def _escpos_raster_bytes(img, feed_lines=3):
    """ESC/POS payload: reset, left-align, whole invoice as raster bands, feed, ONE cut."""
    img = img.convertToFormat(QImage.Format_Grayscale8)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    try:
        gray = bytes(img.constBits())[: bpl * h]
        if len(gray) < bpl * h:
            raise ValueError("short image buffer")
    except Exception:   # very old bindings: slow but safe pixel access
        buf = bytearray(bpl * h)
        for y in range(h):
            for x in range(w):
                buf[y * bpl + x] = img.pixel(x, y) & 0xFF
        gray = bytes(buf)
    body = _pack_raster_bands(gray, bpl, w, h)
    return b'\x1b@' + b'\x1ba\x00' + body + b'\x1bd' + bytes((max(0, min(feed_lines, 255)),)) + b'\x1dV\x00'

def _raw_print_windows(data, printer_name=None):
    """Send exactly one RAW print job (StartDoc / one page / WritePrinter in chunks / EndDoc)."""
    if os.name != 'nt':
        raise RuntimeError('چاپ مستقیم رول در این نسخه فقط روی Windows فعال است.')

    class DOC_INFO_1(ctypes.Structure):
        # Order matters: document name, OUTPUT FILE, DATATYPE
        _fields_ = [("pDocName", ctypes.c_wchar_p), ("pOutputFile", ctypes.c_wchar_p), ("pDatatype", ctypes.c_wchar_p)]

    winspool = ctypes.WinDLL('winspool.drv')
    OpenPrinter = winspool.OpenPrinterW; OpenPrinter.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]; OpenPrinter.restype = ctypes.c_bool
    StartDocPrinter = winspool.StartDocPrinterW; StartDocPrinter.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]; StartDocPrinter.restype = ctypes.c_uint
    StartPagePrinter = winspool.StartPagePrinter; StartPagePrinter.argtypes = [ctypes.c_void_p]; StartPagePrinter.restype = ctypes.c_bool
    WritePrinter = winspool.WritePrinter; WritePrinter.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]; WritePrinter.restype = ctypes.c_bool
    EndPagePrinter = winspool.EndPagePrinter; EndPagePrinter.argtypes = [ctypes.c_void_p]; EndPagePrinter.restype = ctypes.c_bool
    EndDocPrinter = winspool.EndDocPrinter; EndDocPrinter.argtypes = [ctypes.c_void_p]; EndDocPrinter.restype = ctypes.c_bool
    ClosePrinter = winspool.ClosePrinter; ClosePrinter.argtypes = [ctypes.c_void_p]; ClosePrinter.restype = ctypes.c_bool
    if not printer_name:
        printer_name = QPrinterInfo.defaultPrinter().printerName()
    if not printer_name:
        raise RuntimeError('هیچ چاپگر پیش‌فرضی در ویندوز انتخاب نشده است.')
    h = ctypes.c_void_p()
    if not OpenPrinter(printer_name, ctypes.byref(h), None):
        raise RuntimeError(f'باز کردن چاپگر ممکن نشد: {printer_name}')
    try:
        info = DOC_INFO_1("FactorDesk Receipt", None, "RAW")
        if not StartDocPrinter(h, 1, ctypes.byref(info)):
            raise RuntimeError('شروع کار چاپ مستقیم ممکن نشد.')
        try:
            if not StartPagePrinter(h):
                raise RuntimeError('شروع صفحه چاپ ممکن نشد.')
            try:
                view = memoryview(data); pos = 0; CH = 16384
                while pos < len(view):
                    chunk = bytes(view[pos:pos + CH]); written = ctypes.c_uint(0)
                    buf = ctypes.create_string_buffer(chunk, len(chunk))
                    if not WritePrinter(h, buf, len(chunk), ctypes.byref(written)) or written.value != len(chunk):
                        raise RuntimeError('ارسال اطلاعات فیش به چاپگر کامل نشد.')
                    pos += len(chunk)
            finally:
                EndPagePrinter(h)
        finally:
            EndDocPrinter(h)
    finally:
        ClosePrinter(h)

def print_receipt_direct(parent, d, st, printer_name=None):
    """Continuous-roll thermal printing: one bitmap, sent in raster bands, exactly one cut command."""
    doc, safe_st = make_receipt_document(d, st)
    img = _receipt_image(doc, safe_st)
    _raw_print_windows(_escpos_raster_bytes(img), printer_name)

_VIRTUAL_PRINTER_WORDS = ("pdf", "xps", "onenote", "fax", "document writer", "image writer")

def choose_receipt_printer(parent, st):
    """Printer for raw receipt printing: the one saved in settings, otherwise ask (default preselected)."""
    names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
    if not names:
        raise RuntimeError("هیچ چاپگری روی ویندوز نصب نشده است.")
    saved = str(st.get("receipt_printer") or "")
    if saved in names:
        return saved
    default = QPrinterInfo.defaultPrinter().printerName()
    cur = names.index(default) if default in names else 0
    name, ok = QInputDialog.getItem(parent, "انتخاب چاپگر فیش", "پرینتر فیش (حرارتی) را انتخاب کن:", names, cur, False)
    if not ok:
        return None
    if any(w in name.lower() for w in _VIRTUAL_PRINTER_WORDS):
        QMessageBox.warning(parent, "چاپگر نامناسب", "این چاپگر مجازی است (PDF/XPS/Fax) و فیش روی کاغذ چاپ نمی‌شود. پرینتر حرارتی را انتخاب کن.")
        return None
    return name

def show_preview(parent, doc, printer, title="پیش‌نمایش چاپ", extra=None):
    """extra: list of (text, callback) added to the preview toolbar (e.g. receipt printer, share)."""
    dlg = QPrintPreviewDialog(printer, parent)
    dlg.setWindowTitle(title); dlg.resize(1000, 800)
    dlg.setWindowFlag(Qt.WindowMaximizeButtonHint, True); fit_window(dlg); QTimer.singleShot(60, lambda: keep_on_screen(dlg))
    dlg.paintRequested.connect(lambda p: doc.print_(p))
    if extra:
        tb = dlg.findChild(QToolBar)
        if tb is not None:
            tb.addSeparator()
            for text, fn in extra:
                act = tb.addAction(text); act.triggered.connect(lambda checked=False, f=fn: f())
    dlg.exec()

def save_pdf(parent, doc, st, default_name, landscape=False):
    path, _ = QFileDialog.getSaveFileName(parent, "ذخیره PDF", default_name, "PDF (*.pdf)")
    if not path: return
    try:
        doc.print_(make_printer(st, landscape, pdf_path=path))
        QMessageBox.information(parent, "انجام شد", "فایل PDF ذخیره شد.")
    except Exception as e:
        QMessageBox.warning(parent, "خطا در PDF", str(e))

def logo_tag(st, height=70):
    p = st.get("logo_path")
    if not st.get("show_logo", True) or not p or not os.path.exists(p): return ""
    img = QImage(p)
    if img.isNull(): return ""
    img = img.scaledToHeight(height * 2, Qt.SmoothTransformation)
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly); img.save(buf, "PNG"); buf.close()
    b64 = base64.b64encode(bytes(ba)).decode()
    return f'<img src="data:image/png;base64,{b64}" height="{height}">'

def _shop_header(st):
    parts = []
    if st["shop_name"]: parts.append(f'<b style="font-size:{int(st["font_size"])+4}pt">{esc(st["shop_name"])}</b>')
    if st["shop_phone"]: parts.append(f'تلفن: {esc(st["shop_phone"])}')
    if st["shop_address"] and st.get("show_shop_address", True): parts.append(esc(st["shop_address"]))
    return "<br>".join(parts)

def build_report_html(rep, st):
    acc = st["accent"]; fs = int(st["font_size"]); pad = int(st["cell_pad"]); ts = f"font-size:{fs}pt;"
    h = ['<html><body dir="rtl">']
    shop = _shop_header(st); logo = logo_tag(st, int(st["logo_h"] * 0.7))
    if shop or logo:
        h.append(f'<table width="100%" cellspacing="0" cellpadding="2"><tr><td align="right" valign="middle">{logo}</td>'
                 f'<td align="right" valign="middle">{shop}</td></tr></table>')
    h.append(f'<p align="center"><span style="font-size:{fs+5}pt;color:{acc}"><b>{esc(rep["title"])}</b></span>')
    if rep.get("subtitle"): h.append(f'<br>{esc(rep["subtitle"])}')
    h.append(f'<br><span style="font-size:{max(6, fs-2)}pt">تاریخ چاپ: {today_j()}</span></p>')
    h.append(f'<table dir="rtl" width="100%" border="1" cellspacing="0" cellpadding="{pad}" style="border-color:#aab8ad">')
    h.append("<thead><tr>" + "".join(f'<th bgcolor="{acc}" align="center" style="{ts}"><font color="#ffffff">{esc(x)}</font></th>' for x in rep["headers"]) + "</tr></thead>")
    for r, row in enumerate(rep["rows"]):
        bold = r in rep["bold"]
        bg = "#e4eee7" if bold else ("#f4f7f5" if r % 2 else "#ffffff")
        cells = []
        for c, v in enumerate(row):
            t = esc(cell_text(rep, c, v)) or "&nbsp;"
            if bold: t = f"<b>{t}</b>"
            cells.append(f'<td bgcolor="{bg}" align="right" style="{ts}">{t}</td>')
        h.append("<tr>" + "".join(cells) + "</tr>")
    h.append("</table>")
    if rep["totals"]:
        h.append(f'<br><table dir="rtl" width="50%" border="1" cellspacing="0" cellpadding="{pad}" align="left" style="border-color:#aab8ad">')
        for lbl, v in rep["totals"]:
            h.append(f'<tr><td bgcolor="#e4eee7" style="{ts}"><b>{esc(lbl)}</b></td><td align="right" style="{ts}">{esc(total_text(v))}</td></tr>')
        h.append("</table>")
    h.append("</body></html>")
    return "".join(h)

def build_receipt_html(d, st):
    """RTL thermal receipt (58/80mm) drawn as a clear grid that uses the full paper width.
    Column order is right-to-left: row no. | product name | quantity | amount."""
    fs = int(st.get("receipt_font_size", 9)); inv, cust, items = d["inv"], d["cust"], d["items"]
    unit = esc(st.get("unit", "تومان"))
    small = max(6, fs - 2)
    narrow = float(st.get("receipt_width", 80)) <= 58
    w_row, w_qty, w_amt = (9, 13, 35) if narrow else (8, 12, 30)
    w_name = 100 - w_row - w_qty - w_amt
    tb = 'dir="rtl" width="100%" border="1" cellspacing="0" cellpadding="2" style="border-color:#000000;border-collapse:collapse;"'
    sp = '<table width="100%" cellspacing="0" cellpadding="0"><tr><td height="3"></td></tr></table>'

    rows = []
    for i, it in enumerate(items, 1):
        name = esc(it.get("product_name", ""))
        desc = esc(it.get("description", ""))
        name_html = name + (f'<br><span style="font-size:{small}pt;color:#444444">{desc}</span>' if desc else "")
        qty = fmt_qty(it["quantity"])
        amount = money(float(it["quantity"]) * float(it["unit_price"]))
        if inv.get("is_proforma") and float(it["unit_price"]) == 0: amount = ""
        rows.append(
            f'<tr><td align="center" valign="middle"><b>{i}</b></td>'
            f'<td align="right" valign="middle">{name_html}</td>'
            f'<td align="center" valign="middle">{qty}</td>'
            f'<td align="right" valign="middle">{amount}</td></tr>')
    head = "".join(f'<th bgcolor="#d9d9d9" width="{w}%" align="center">{t}</th>'
                   for w, t in ((w_row, "ردیف"), (w_name, "نام کالا"), (w_qty, "تعداد"), (w_amt, "مبلغ")))

    shop = esc(st.get("shop_name", "")) or esc(APP_NAME)
    phone = esc(st.get("shop_phone", ""))
    logo = logo_tag(st, 45) if st.get("receipt_show_logo") else ""
    title = "پیش‌فاکتور" if inv.get("is_proforma") else st.get("title", "فاکتور فروش")
    top = (f'{logo}{"<br>" if logo else ""}<b style="font-size:{fs + 2}pt">{shop}</b>'
           f'{("<br>" + phone) if phone else ""}<br><b>{esc(title)}</b>')

    tot = [("جمع کالاها", money(d["subtotal"]), False)]
    if float(inv.get("discount") or 0) > 0:
        tot.append(("تخفیف", money(inv.get("discount", 0)), False))
    tot += [("مبلغ نهایی", money(d["total"]), True), ("پرداخت‌شده", money(d["paid"]), False), ("مانده", money(d["remain"]), True)]
    tot_html = ""
    for lbl, val, bold in tot:
        bg = ' bgcolor="#d9d9d9"' if bold else ""
        l_txt = f"<b>{lbl}</b>" if bold else lbl
        v_txt = f"<b>{val} {unit}</b>" if bold else f"{val} {unit}"
        tot_html += f'<tr><td width="42%" align="right"{bg}>{l_txt}</td><td width="58%" align="left"{bg}>{v_txt}</td></tr>'
    footer = f'<p align="center">{esc(st["footer"])}</p>' if st.get("footer") else ""
    return (
        f'<html><body dir="rtl" style="font-family:\'{esc(st["font"])}\';font-size:{fs}pt;margin:0;padding:0;">'
        f'<table {tb}><tr><td align="center">{top}</td></tr></table>{sp}'
        f'<table {tb}><tr><td width="50%" align="right">شماره: <b>{esc(inv.get("invoice_no", ""))}</b></td>'
        f'<td width="50%" align="right">تاریخ: {esc(iso_to_j(inv.get("invoice_date", "")))}{(" — ساعت " + _inv_time(inv)) if _inv_time(inv) else ""}</td></tr>'
        f'<tr><td colspan="2" align="right">مشتری: <b>{esc(cust.get("name", ""))}</b></td></tr>'
        + (f'<tr><td colspan="2" align="right">تلفن: {esc(cust.get("phone", ""))}</td></tr>' if st.get("show_customer_phone", True) and cust.get("phone") else "")
        + (f'<tr><td colspan="2" align="right">آدرس: {esc(cust.get("address", ""))}</td></tr>' if st.get("show_customer_address", True) and cust.get("address") else "")
        + f'</table>{sp}'
        f'<table {tb}><thead><tr>{head}</tr></thead>{"".join(rows)}</table>{sp}'
        f'<table {tb}>{tot_html}</table>'
        f'{footer}<p align="center">با تشکر</p></body></html>'
    )

def load_invoice_data(iid):
    with db() as con:
        inv = con.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
        cust = con.execute("SELECT * FROM customers WHERE id=?", (inv["customer_id"],)).fetchone()
        items = [dict(x) for x in con.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY row_no", (iid,))]
        pays = [dict(x) for x in con.execute("SELECT * FROM payments WHERE invoice_id=? ORDER BY payment_date,id", (iid,))]
        rets = float(con.execute("SELECT COALESCE(SUM(ri.quantity*ri.unit_price),0) FROM return_items ri JOIN returns r ON r.id=ri.return_id WHERE r.invoice_id=?", (iid,)).fetchone()[0] or 0)
        bal = customer_figures(con)[inv["customer_id"]]["balance"]
    inv = dict(inv); cust = dict(cust)
    sub = sum(float(i["quantity"]) * float(i["unit_price"]) for i in items)
    total = max(0.0, sub - float(inv["discount"] or 0))
    linked = sum(float(p["amount"]) + float(p.get("discount") or 0) for p in pays) + rets
    paid = float(inv["paid_initial"] or 0) + linked
    return dict(inv=inv, cust=cust, items=items, payments=pays, subtotal=sub, total=total,
                linked=linked, paid=paid, remain=total - paid, balance=bal)

def sample_invoice_data(n=3, proforma=False):
    d = date.today()
    names = [("لنت ترمز جلو", "مدل نمونه", 850000), ("فیلتر روغن", "", 190000), ("روغن موتور ۴ لیتری", "5W-30", 640000),
             ("شمع موتور", "ست ۴ تایی", 420000), ("تسمه تایم", "", 1250000)]
    items = [dict(row_no=i + 1, product_name=names[i % 5][0], description=names[i % 5][1], quantity=1 + i % 3,
                  unit_price=0 if (proforma and i % 4 == 3) else names[i % 5][2]) for i in range(n)]
    sub = sum(i["quantity"] * i["unit_price"] for i in items)
    return dict(inv=dict(invoice_no="1001", invoice_date=d.isoformat(), kind="اعتباری", due_date=(d + timedelta(30)).isoformat(),
                         description="فروش قطعات", discount=50000, paid_initial=500000, notes="یادداشت نمونه",
                         is_proforma=1 if proforma else 0),
                cust=dict(name="مشتری نمونه", phone="09120000000", address="تهران، خیابان نمونه"),
                items=items, payments=[dict(payment_date=d.isoformat(), amount=300000, method="کارت‌به‌کارت", notes="")],
                subtotal=sub, total=max(0, sub - 50000), linked=300000, paid=800000, remain=sub - 850000, balance=1500000)

def build_invoice_html(d, st):
    acc = st["accent"]; fs = int(st["font_size"]); tfs = int(st["table_font_size"]); pad = int(st["cell_pad"])
    unit = esc(st["unit"]); inv, cust = d["inv"], d["cust"]
    pf = bool(inv.get("is_proforma")); th = _invoice_theme(st.get("layout", "classic"), acc); classic = th["grid"]; bcol = th["bcol"]; pad = max(1, pad + th["pad_add"])
    if classic:
        tb = f'border="1" cellspacing="0" cellpadding="{pad}" style="border-color:{bcol}"'; cs = f"font-size:{tfs}pt;"
    else:
        tb = f'border="0" cellspacing="0" cellpadding="{pad + 1}"'; cs = f"font-size:{tfs}pt;border-bottom:1px solid {bcol};"
    gap = th["gap"] or max(2, pad * 2)
    sp = f'<table width="100%" cellspacing="0" cellpadding="0"><tr><td height="{gap}"></td></tr></table>'
    h = ['<html><body dir="rtl">']
    logo = logo_tag(st, int(st["logo_h"])); shop = _shop_header(st)
    title = st["proforma_title"] if pf else st["title"]
    meta = f'شماره: <b>{esc(inv["invoice_no"])}</b><br>تاریخ: {iso_to_j(inv["invoice_date"])}{(" — ساعت " + _inv_time(inv)) if _inv_time(inv) else ""}'
    if not pf: meta += f'<br>نوع: {esc(inv["kind"])}'
    h.append('<table dir="rtl" width="100%" cellspacing="0" cellpadding="2" border="0"><tr>')
    h.append(f'<td width="30%" align="right" valign="middle">{logo}{"<br>" if logo and shop else ""}{shop}</td>')
    h.append(f'<td width="40%" align="center" valign="middle"><span style="font-size:{max(8, int(round(int(st["title_size"]) * th["title_scale"])))}pt;color:{th["title_color"]}"><b>{esc(title)}</b></span></td>')
    h.append(f'<td width="30%" align="left" valign="middle">{meta}</td></tr></table>')
    h.append(f'<table width="100%" bgcolor="{th["band"]}" cellspacing="0" cellpadding="0"><tr><td height="2"></td></tr></table>{sp}' if th["band"] else sp)
    line1 = f'<b>مشتری:</b> {esc(cust["name"])}'
    if st["show_customer_phone"] and cust.get("phone"): line1 += f' &nbsp; <b>تلفن:</b> {esc(cust["phone"])}'
    if st["show_due"] and not pf and inv.get("due_date"): line1 += f' &nbsp; <b>سررسید:</b> {iso_to_j(inv["due_date"])}'
    lines = [line1]
    if st["show_customer_address"] and cust.get("address"): lines.append(f'<b>آدرس:</b> {esc(cust["address"])}')
    if inv.get("description"): lines.append(f'<b>شرح:</b> {esc(inv["description"])}')
    h.append(f'<table dir="rtl" width="100%" {tb}><tr><td bgcolor="{th["info_bg"]}" align="right" style="font-size:{tfs}pt;">{"<br>".join(lines)}</td></tr></table>{sp}')
    # items table
    fixed = (st["col_row"] if st["show_row_no"] else 0) + st["col_qty"] + st["col_price"] + st["col_amount"]
    rest = max(20, 100 - fixed)
    name_w = round(rest * 0.55) if st["show_desc"] else rest
    cols = []
    if st["show_row_no"]: cols.append(("ردیف", st["col_row"], "center"))
    cols.append(("نام کالا", name_w, "right"))
    if st["show_desc"]: cols.append(("شرح", rest - name_w, "right"))
    cols += [("تعداد", st["col_qty"], "center"), (f"قیمت واحد ({st['unit']})", st["col_price"], "right"), (f"مبلغ ({st['unit']})", st["col_amount"], "right")]
    h.append(f'<table dir="rtl" width="100%" {tb}><thead><tr>')
    for t, w, _ in cols:
        h.append(f'<th bgcolor="{th["head_bg"]}" width="{w}%" align="center" style="font-size:{tfs}pt;"><font color="{th["head_fg"]}">{esc(t)}</font></th>')
    h.append("</tr></thead>")
    unpriced = 0
    for n, it in enumerate(d["items"], 1):
        price = float(it["unit_price"]); amt = float(it["quantity"]) * price
        blank = pf and price == 0
        if blank: unpriced += 1
        bg = th["zebra"] if (st["zebra"] and n % 2 == 0) else "#ffffff"
        vals = []
        if st["show_row_no"]: vals.append((str(n), "center"))
        vals.append((it["product_name"], "right"))
        if st["show_desc"]: vals.append((it["description"], "right"))
        vals += [(fmt_qty(it["quantity"]), "center"), ("" if blank else money(price), "right"), ("" if blank else money(amt), "right")]
        h.append("<tr>" + "".join(f'<td bgcolor="{bg}" align="{a}" style="{cs}">{esc(v) or "&nbsp;"}</td>' for v, a in vals) + "</tr>")
    h.append(f"</table>{sp}")
    # totals
    rows = [("جمع کالاها", money(d["subtotal"]), False)]
    if float(inv.get("discount") or 0) > 0: rows.append(("تخفیف", money(inv["discount"]), False))
    rows.append(("مبلغ نهایی" if not pf else "جمع (اقلام قیمت‌دار)", money(d["total"]), True))
    if pf and unpriced: rows.append(("اقلام بدون قیمت", f"{unpriced} ردیف", False))
    if st["show_balance"] and not pf:
        if float(inv.get("paid_initial") or 0) > 0: rows.append(("پرداخت اولیه", money(inv["paid_initial"]), False))
        if d["linked"] > 0: rows.append(("دریافت‌های بعدی", money(d["linked"]), False))
        rows.append(("مانده این فاکتور", money(d["remain"]), True))
    if st["show_prev_balance"] and not pf: rows.append(("مانده کل حساب مشتری", money(d["balance"]), True))
    h.append(f'<table dir="rtl" width="{int(st["totals_width"])}%" align="left" {tb}>')
    for lbl, v, b in rows:
        bg = th["tot_bg"] if b else "#ffffff"
        val = f"{v} {unit}" if not v.endswith("ردیف") else v
        t1, t2 = (f"<b>{lbl}</b>", f"<b>{val}</b>") if b else (lbl, val)
        h.append(f'<tr><td bgcolor="{bg}" align="right" style="{cs}">{t1}</td><td bgcolor="{bg}" align="right" style="{cs}">{t2}</td></tr>')
    h.append("</table>")
    h.append('<br clear="all">')
    if st["show_payments"] and not pf and d["payments"]:
        h.append(f'<p style="font-size:{tfs}pt;"><b>دریافت‌های ثبت‌شده برای این فاکتور:</b></p><table dir="rtl" width="60%" align="right" {tb}>')
        for p in d["payments"]:
            h.append(f'<tr><td align="right" style="{cs}">{iso_to_j(p["payment_date"])}</td><td align="right" style="{cs}">{money(p["amount"])} {unit}</td>'
                     f'<td align="right" style="{cs}">{esc(p.get("method",""))} {esc(p.get("notes",""))}</td></tr>')
        h.append("</table>")
    if st["show_notes"] and inv.get("notes"):
        h.append(f'<p style="font-size:{tfs}pt;"><b>توضیحات:</b> {esc(inv["notes"])}</p>')
    if pf and st["proforma_note"]:
        h.append(f'<p align="center" style="font-size:{tfs}pt;">{esc(st["proforma_note"])}</p>')
    if st["show_signatures"]:
        h.append(f'{sp}<table dir="rtl" width="100%" border="0" cellspacing="0"><tr>'
                 f'<td align="center" style="font-size:{tfs}pt;">{esc(st["seller_label"])}<br>..............................</td>'
                 f'<td align="center" style="font-size:{tfs}pt;">{esc(st["buyer_label"])}<br>..............................</td></tr></table>')
    if st["footer"]:
        h.append(f'<p align="center"><span style="font-size:{max(6, tfs-1)}pt;color:#555">{esc(st["footer"])}</span></p>')
    h.append("</body></html>")
    return "".join(h)

# ───────────────────────── Excel export ─────────────────────────
def export_report_excel(rep, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = re.sub(r"[\\/*?:\[\]]", "-", rep["title"])[:31]
    ws.sheet_view.rightToLeft = True
    ws.append([rep["title"]]); ws["A1"].font = Font(bold=True, size=14)
    if rep.get("subtitle"): ws.append([rep["subtitle"]])
    ws.append([])
    hdr_row = ws.max_row + 1
    ws.append(rep["headers"])
    for cell in ws[hdr_row]:
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="286B4A")
        cell.alignment = Alignment(horizontal="center")
    for r, row in enumerate(rep["rows"]):
        out = [(v if (c in rep["money_cols"] or c in rep["qty_cols"]) and v is not None else ("" if v is None else v)) for c, v in enumerate(row)]
        ws.append(out)
        for c in rep["money_cols"]:
            ws.cell(row=ws.max_row, column=c + 1).number_format = "#,##0"
        if r in rep["bold"]:
            for cell in ws[ws.max_row]: cell.font = Font(bold=True)
    if rep["totals"]:
        ws.append([])
        for lbl, v in rep["totals"]:
            ws.append([lbl, v]); ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
            if isinstance(v, (int, float)): ws.cell(row=ws.max_row, column=2).number_format = "#,##0"
    for col in ws.columns:
        L = max((len(str(c.value or "")) for c in col[hdr_row - 1:]), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max(L + 2, 12), 40)
    ws.freeze_panes = ws.cell(row=hdr_row + 1, column=1)
    wb.save(path)

def strip_bidi(s): return s.replace("‪", "").replace("‬", "")

# ───────────────────────────── UI helpers ─────────────────────────────
def table_item(text, align=Qt.AlignRight | Qt.AlignVCenter):
    it = QTableWidgetItem(str(text if text is not None else ""))
    it.setTextAlignment(align)
    return it

def info(parent, title, text): QMessageBox.information(parent, title, text)
def warn(parent, title, text): QMessageBox.warning(parent, title, text)

def ask(parent, title, text):
    return QMessageBox.question(parent, title, text) == QMessageBox.Yes

class JalaliPicker(QDialog):
    def __init__(self, parent, jy, jm, jd):
        super().__init__(parent); self.setWindowTitle("انتخاب تاریخ شمسی"); self.picked = None; self.cur = (jy, jm, jd)
        lay = QVBoxLayout(self); top = QHBoxLayout()
        prev = QToolButton(); prev.setText("›"); nxt = QToolButton(); nxt.setText("‹")
        self.month = QComboBox(); self.month.addItems(J_MONTHS); self.month.setCurrentIndex(jm - 1)
        self.year = QSpinBox(); self.year.setRange(1300, 1600); self.year.setValue(jy)
        prev.clicked.connect(lambda: self.shift(-1)); nxt.clicked.connect(lambda: self.shift(1))
        for w in (prev, self.month, self.year, nxt): top.addWidget(w)
        lay.addLayout(top)
        grid = QGridLayout(); grid.setSpacing(2)
        for c, t in enumerate(J_WEEK):
            l = QLabel(t); l.setAlignment(Qt.AlignCenter); l.setStyleSheet("font-weight:bold;color:#286b4a;"); grid.addWidget(l, 0, c)
        self.cells = []
        for i in range(42):
            b = QPushButton(""); b.setFixedSize(40, 32); b.clicked.connect(lambda _=False, i=i: self.pick(i))
            grid.addWidget(b, 1 + i // 7, i % 7); self.cells.append(b)
        lay.addLayout(grid)
        today = QPushButton("امروز"); today.clicked.connect(self.go_today); lay.addWidget(today)
        self.month.currentIndexChanged.connect(self.rebuild); self.year.valueChanged.connect(self.rebuild)
        self.rebuild()
    def shift(self, delta):
        m = self.month.currentIndex() + delta; y = self.year.value()
        if m < 0: m, y = 11, y - 1
        if m > 11: m, y = 0, y + 1
        self.year.blockSignals(True); self.year.setValue(y); self.year.blockSignals(False)
        self.month.setCurrentIndex(m); self.rebuild()
    def go_today(self):
        jy, jm, jd = g2j(*[int(x) for x in today_iso().split("-")]); self.picked = (jy, jm, jd); self.accept()
    def rebuild(self):
        y = self.year.value(); m = self.month.currentIndex() + 1; ml = j_month_len(y, m)
        wd = (date(*j2g(y, m, 1)).weekday() + 2) % 7  # Saturday = 0
        for i, b in enumerate(self.cells):
            day = i - wd + 1
            if 1 <= day <= ml:
                b.setText(str(day)); b.setEnabled(True)
                sel = (y, m, day) == self.cur
                b.setStyleSheet("background:#286b4a;color:white;" if sel else "background:#e7eee9;color:#24342b;")
            else:
                b.setText(""); b.setEnabled(False); b.setStyleSheet("background:transparent;")
    def pick(self, i):
        t = self.cells[i].text()
        if t: self.picked = (self.year.value(), self.month.currentIndex() + 1, int(t)); self.accept()

class JalaliDateEdit(QWidget):
    """Text field 'YYYY/MM/DD' (Jalali) + calendar button. iso() -> ISO Gregorian, '' (empty, if allowed) or None (invalid)."""
    def __init__(self, iso=None, allow_empty=False, parent=None):
        super().__init__(parent); self.allow_empty = allow_empty
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(); self.edit.setPlaceholderText("۱۴۰۵/۰۱/۰۱"); self.edit.setLayoutDirection(Qt.LeftToRight)
        self.edit.setAlignment(Qt.AlignCenter); self.edit.setMaximumWidth(130)
        btn = QToolButton(); btn.setText("تقویم"); btn.clicked.connect(self.open_picker)
        lay.addWidget(self.edit); lay.addWidget(btn); lay.addStretch()
        self.edit.editingFinished.connect(self.normalize)
        self.setIso(iso if iso is not None else (None if allow_empty else today_iso()))
    def setIso(self, iso):
        self.edit.setText(iso_to_j(iso) if iso else ""); self.edit.setStyleSheet("")
    def iso(self):
        t = self.edit.text().strip()
        if not t: return "" if self.allow_empty else None
        return j_to_iso(t)
    def normalize(self):
        v = self.iso()
        if v: self.edit.setText(iso_to_j(v)); self.edit.setStyleSheet("")
        elif v is None: self.edit.setStyleSheet("border:1px solid #c0392b;")
    def open_picker(self):
        v = self.iso() or today_iso()
        jy, jm, jd = g2j(*[int(x) for x in v.split("-")])
        dlg = JalaliPicker(self, jy, jm, jd)
        if dlg.exec() and dlg.picked:
            self.edit.setText("%04d/%02d/%02d" % dlg.picked); self.edit.setStyleSheet("")

def need_date(parent, widget, label):
    v = widget.iso()
    if v is None:
        warn(parent, "تاریخ نامعتبر", f"{label} را به‌صورت شمسی و درست وارد کن (مثال: ۱۴۰۵/۰۷/۲۰)."); return None
    return v

_ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه", "ده", "یازده", "دوازده", "سیزده", "چهارده",
         "پانزده", "شانزده", "هفده", "هجده", "نوزده"]
_TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_HUND = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]
_SCALES = ["", "هزار", "میلیون", "میلیارد", "تریلیون", "کوادریلیون"]

def _three(n):
    parts = []; h, r = divmod(n, 100)
    if h: parts.append(_HUND[h])
    if r < 20:
        if r: parts.append(_ONES[r])
    else:
        t, o = divmod(r, 10); parts.append(_TENS[t])
        if o: parts.append(_ONES[o])
    return " و ".join(parts)

def num_to_words(n):
    n = int(round(n))
    if n == 0: return "صفر"
    neg = n < 0; n = abs(n); groups = []
    while n:
        n, g = divmod(n, 1000); groups.append(g)
    parts = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if not g: continue
        if i == 1 and g == 1: parts.append("هزار")
        else: parts.append(_three(g) + (" " + _SCALES[i] if i else ""))
    return ("منفی " if neg else "") + " و ".join(parts)

class FadeLabel(QLabel):
    """Shows an amount in words, then fades away by itself."""
    def __init__(self, hold_ms=2500, parent=None):
        super().__init__(parent); self.setWordWrap(True); self.hold = hold_ms
        self.setStyleSheet("color:#1e563a;font-weight:bold;background:transparent;")
        self.fx = QGraphicsOpacityEffect(self); self.fx.setOpacity(0.0); self.setGraphicsEffect(self.fx)
        self.anim = QPropertyAnimation(self.fx, b"opacity", self); self.anim.setDuration(700)
        self.anim.setStartValue(1.0); self.anim.setEndValue(0.0)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.anim.start)
    def show_words(self, amount, prefix="", unit="تومان"):
        self.anim.stop(); self.timer.stop()
        if not amount:
            self.fx.setOpacity(0.0); return
        self.setText(f"{prefix}{num_to_words(amount)} {unit}"); self.fx.setOpacity(1.0); self.timer.start(self.hold)

def with_words(spin):
    w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(2)
    lab = FadeLabel(); lab.setMinimumHeight(34); l.addWidget(spin); l.addWidget(lab)
    def typed(t):
        try: lab.show_words(parse_num(t))
        except Exception: lab.show_words(0)
    spin.lineEdit().textEdited.connect(typed)
    return w

class PriceDelegate(QStyledItemDelegate):
    def __init__(self, parent, cb):
        super().__init__(parent); self.cb = cb
    def createEditor(self, parent, option, index):
        e = QLineEdit(parent); e.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        e.textChanged.connect(lambda t: self.cb(t)); return e

# ───────────── voice typing (online speech-to-text) ─────────────
def pcm_to_wav(raw, sample_format, channels, rate):
    """Device PCM (any sample format) -> mono 16-bit WAV bytes. Raises ValueError when nothing audible was recorded."""
    import array, wave, io
    name = getattr(sample_format, "name", str(sample_format))
    if "Int16" in name:
        samples = array.array("h"); samples.frombytes(raw[: len(raw) // 2 * 2])
    elif "UInt8" in name:
        samples = array.array("h", [(b - 128) * 256 for b in raw])
    elif "Int32" in name:
        a = array.array("i"); a.frombytes(raw[: len(raw) // 4 * 4]); samples = array.array("h", [v >> 16 for v in a])
    elif "Float" in name:
        a = array.array("f"); a.frombytes(raw[: len(raw) // 4 * 4])
        samples = array.array("h", [max(-32768, min(32767, int(v * 32767))) for v in a])
    else:
        raise ValueError("فرمت صدای میکروفون پشتیبانی نمی‌شود.")
    if channels > 1:
        samples = array.array("h", [sum(samples[i:i + channels]) // channels for i in range(0, len(samples) - channels + 1, channels)])
    if len(samples) < rate // 5 or max(max(samples), -min(samples)) < 150:
        raise ValueError("صدایی ضبط نشد. میکروفون را بررسی کن و کمی بلندتر یا نزدیک‌تر صحبت کن.")
    buf = io.BytesIO(); w = wave.open(buf, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(samples.tobytes()); w.close()
    return buf.getvalue()

GOOGLE_STT_URL = "http://www.google.com/speech-api/v2/recognize"
# Chromium's public web-speech key (the same one open-source libraries use); can be replaced in settings.
GOOGLE_PUBLIC_KEY = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"

def transcribe_google(wav, key="", lang="fa-IR"):
    """Free Google web speech recogniser (unofficial endpoint, Persian). Best for short phrases."""
    import urllib.request, urllib.error, urllib.parse, wave, io
    r = wave.open(io.BytesIO(wav), "rb"); rate = r.getframerate(); pcm = r.readframes(r.getnframes()); r.close()
    url = GOOGLE_STT_URL + "?" + urllib.parse.urlencode({"client": "chromium", "lang": lang or "fa-IR", "key": key or GOOGLE_PUBLIC_KEY, "output": "json", "maxresults": "1"})
    req = urllib.request.Request(url, data=pcm, method="POST", headers={"Content-Type": f"audio/l16; rate={rate}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raise RuntimeError("گوگل درخواست را قبول نکرد" + (" (کلید نامعتبر یا محدود شده)." if e.code in (400, 403) else f" ({e.code})."))
    except urllib.error.URLError as e:
        raise RuntimeError(f"اتصال به اینترنت یا گوگل برقرار نشد: {getattr(e, 'reason', e)}")
    for line in body.splitlines():
        try: data = json.loads(line)
        except ValueError: continue
        for res in data.get("result", []):
            alts = res.get("alternative", [])
            if alts and alts[0].get("transcript"): return str(alts[0]["transcript"])
    return ""

class _TxSignals(QObject):
    done = Signal(str, str)   # text, error

class Dictation(QObject):
    """Record -> Google speech-to-text -> apply_fn(text). Works from a mic button, or programmatically (button=None)
    where problems are reported through on_error(message) and `error_kind` ('silence' / 'no_text' / 'error')."""
    def __init__(self, parent, button, apply_fn, label="🎤 تایپ با صدا", on_error=None):
        super().__init__(parent)
        self.parent_w = parent; self.btn = button; self.apply_fn = apply_fn; self.label = label; self.on_error = on_error
        self.src = None; self.buf = None; self.fmt = None; self.recording = False
        self._speech = False; self._quiet_ms = 0; self._elapsed = 0; self._gen = 0
        self.first_speech_ms = 0; self.force_auto = False; self.error_kind = ""
        self.poll = QTimer(self); self.poll.setInterval(200); self.poll.timeout.connect(self._poll)
        self.sig = _TxSignals(); self.sig.done.connect(self._finished)
        if button is not None: self._reset_button(); button.clicked.connect(self.toggle)
    def _ui(self, text=None, style=None, enabled=None):
        if self.btn is None: return
        if text is not None: self.btn.setText(text)
        if style is not None: self.btn.setStyleSheet(style)
        if enabled is not None: self.btn.setEnabled(enabled)
    def _reset_button(self): self._ui(self.label, "", True)
    def _err(self, title, msg, kind="error"):
        self.error_kind = kind
        if self.on_error: self.on_error(msg)
        else: warn(self.parent_w, title, msg)
    def _poll(self):
        """Stop ~1.5 s after the speaker finishes (16-bit devices only); also give up if nobody speaks at all."""
        if not self.recording: self.poll.stop(); return
        import array
        self._elapsed += 200
        n = int(self.fmt.sampleRate() * self.fmt.channelCount() * 2 * 0.2)
        data = bytes(self.buf.data())[-n:]
        a = array.array("h"); a.frombytes(data[: len(data) // 2 * 2])
        peak = max(max(a), -min(a)) if len(a) else 0
        if peak > 1200: self._speech = True; self._quiet_ms = 0
        elif peak < 700 and self._speech:
            self._quiet_ms += 200
            if self._quiet_ms >= 1500: return self.stop()
        if self.first_speech_ms and not self._speech and self._elapsed >= self.first_speech_ms: self.stop()
    def toggle(self, *_):
        if self.recording: self.stop()
        else: self.start()
    def start(self, first_speech_ms=0, force_auto=False):
        try:
            from PySide6.QtMultimedia import QAudioSource, QAudioFormat, QMediaDevices
        except Exception:
            self._err("تایپ با صدا", "بخش صدای Qt نصب نیست. دستور pip install --upgrade PySide6 را اجرا کن."); return False
        dev = QMediaDevices.defaultAudioInput()
        want = get_pref("stt_mic", "")
        if want:
            for d in QMediaDevices.audioInputs():
                if d.description() == want: dev = d; break
        if dev.isNull():
            self._err("میکروفون", "میکروفونی پیدا نشد. میکروفون را وصل کن و مجوز ویندوز را بررسی کن."); return False
        fmt = QAudioFormat(); fmt.setSampleRate(16000); fmt.setChannelCount(1); fmt.setSampleFormat(QAudioFormat.Int16)
        if not dev.isFormatSupported(fmt): fmt = dev.preferredFormat()
        self.fmt = fmt; self._gen += 1; gen = self._gen
        self.src = QAudioSource(dev, fmt, self); self.buf = QBuffer(self); self.buf.open(QIODevice.WriteOnly)
        self.src.start(self.buf); self.recording = True
        self._speech = False; self._quiet_ms = 0; self._elapsed = 0; self.first_speech_ms = first_speech_ms; self.force_auto = force_auto
        if (force_auto or get_pref("stt_auto", "1") == "1") and "Int16" in getattr(fmt.sampleFormat(), "name", str(fmt.sampleFormat())): self.poll.start()
        self._ui("⏹ توقف و تبدیل به متن", "background:#c62828;color:white;font-weight:bold;")
        try: secs = max(5, min(60, int(float(get_pref("stt_max", "30") or 30))))
        except Exception: secs = 30
        QTimer.singleShot(secs * 1000, lambda: self.stop() if (self.recording and self._gen == gen) else None)
        return True
    def stop(self):
        if not self.recording: return
        self.recording = False; self.poll.stop()
        self.src.stop(); raw = bytes(self.buf.data()); self.buf.close()
        self._ui("در حال تبدیل صدا به متن…", "", False)
        try:
            wav = pcm_to_wav(raw, self.fmt.sampleFormat(), self.fmt.channelCount(), self.fmt.sampleRate())
        except Exception as e:
            self._reset_button(); return self._err("تایپ با صدا", str(e), "silence")
        import threading
        threading.Thread(target=self._work, args=(wav, get_pref("google_key", "").strip(), get_pref("stt_lang", "fa-IR")), daemon=True).start()
    def _work(self, wav, key, lang):
        try:
            self.sig.done.emit(transcribe_google(wav, key, lang), "")
        except Exception as e: self.sig.done.emit("", str(e))
    def _finished(self, text, err):
        self._reset_button()
        if err: return self._err("تایپ با صدا", err)
        if not text.strip(): return self._err("تایپ با صدا", "متنی تشخیص داده نشد. دوباره امتحان کن.", "no_text")
        self.error_kind = ""
        self.apply_fn(text.strip())

# ───────────── voice assistant: understands spoken commands (offline rules, no AI service needed) ─────────────
_UNITS = {"صفر": 0, "یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "شیش": 6, "هفت": 7, "هشت": 8, "نه": 9,
          "ده": 10, "یازده": 11, "دوازده": 12, "سیزده": 13, "چهارده": 14, "پانزده": 15, "پونزده": 15, "شانزده": 16, "شونزده": 16,
          "هفده": 17, "هجده": 18, "هیجده": 18, "نوزده": 19, "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50, "شصت": 60,
          "هفتاد": 70, "هشتاد": 80, "نود": 90, "صد": 100, "یکصد": 100, "دویست": 200, "سیصد": 300, "چهارصد": 400,
          "پانصد": 500, "ششصد": 600, "شیشصد": 600, "هفتصد": 700, "هشتصد": 800, "نهصد": 900}
_SPOKEN_SCALES = {"هزار": 1000, "میلیون": 10 ** 6, "ملیون": 10 ** 6, "میلیارد": 10 ** 9}
_DIGITS = re.compile(r"\d+(\.\d+)?$")

def _tok(text):
    t = norm_key(text)
    t = re.sub(r"(?<=\d)[,،](?=\d{3})", "", t)
    t = re.sub(r"[^\w\s.]", " ", t)
    return [w.strip(".") for w in t.split() if w.strip(".")]

def _is_num(t): return bool(_DIGITS.match(t)) or t in _UNITS or t in _SPOKEN_SCALES or t == "نیم"

def _span_value(toks):
    total = 0.0; cur = 0.0; last_scale = 0
    for t in toks:
        if t == "و": continue
        if t == "نیم": cur += 0.5
        elif _DIGITS.match(t): cur += float(t)
        elif t in _UNITS: cur += _UNITS[t]
        elif t in _SPOKEN_SCALES:
            total += (cur or 1) * _SPOKEN_SCALES[t]; cur = 0.0; last_scale = _SPOKEN_SCALES[t]
    if last_scale >= 10 ** 6 and 0 < cur < 1000 and cur == int(cur):   # "یک میلیون و دویست" = 1,200,000
        cur *= last_scale // 1000
    return total + cur

def _find_spans(toks):
    """Number phrases (digits or Persian words) -> [(start, end, value)]."""
    spans = []; i = 0
    while i < len(toks):
        if _is_num(toks[i]) and toks[i] != "نیم":
            j = i + 1
            while j < len(toks):
                prev, t = toks[j - 1], toks[j]
                if t in _SPOKEN_SCALES and _is_num(prev): j += 1
                elif t == "و" and j + 1 < len(toks) and _is_num(toks[j + 1]) and (prev in _SPOKEN_SCALES or prev in _UNITS or _DIGITS.match(prev)): j += 2
                elif _is_num(t) and not _DIGITS.match(t) and (prev in _SPOKEN_SCALES or (prev in _UNITS and prev not in ("ده",) )) and prev != "و": j += 1
                elif _DIGITS.match(t) and prev in _SPOKEN_SCALES: j += 1
                else: break
            spans.append((i, j, _span_value(toks[i:j]))); i = j
        else:
            i += 1
    return spans

_FILL = {"اضافه", "کن", "بنویس", "بزن", "ثبت", "بعدی", "ردیف", "کالا", "بذار", "بزار", "بده", "لطفا"}
_PRICE_MARK = {"به", "قیمت", "فی", "مبلغ", "هر", "کدام", "هرکدام", "هریک", "هرعدد", "واحد", "قیمتش", "عدد", "یک", "هرتا"}
_CURRENCY = {"تومان", "تومن", "تومانی", "تومنی", "ریال", "هزارتومان"}

def _thousands(v):
    """Shop prices are in thousands of toman: 'هشتصد' means 800,000."""
    return (v * 1000, True) if 0 < v < 1000 else (v, False)

def parse_item_phrase(text):
    """'سه عدد لنت ترمز جلو قیمت هشتصد و پنجاه هزار تومان' -> dict(qty, name, price|None, guessed)"""
    toks = _tok(text)
    while toks and toks[0] in _FILL: toks.pop(0)
    while toks and toks[-1] in _FILL: toks.pop()
    spans = _find_spans(toks)
    if not toks or (len(spans) == 1 and spans[0][0] == 0 and spans[0][1] == len(toks)): return None
    drop = set(); qty = 1.0; price = None; guessed = False
    first = spans[0] if spans else None
    if first and first[0] == 0:
        qty = first[2] or 1.0; drop.update(range(first[0], first[1]))
        if first[1] < len(toks) and toks[first[1]] in ("عدد", "تا", "دانه"): drop.add(first[1])
        rest = spans[1:]
    else:
        rest = list(spans)
    if rest:
        s, e, v = rest[-1]
        end = e
        while end < len(toks) and toks[end] in _CURRENCY: end += 1
        marker = s > 0 and toks[s - 1] in _PRICE_MARK
        if end == len(toks) or marker:
            if any(toks[k] == "ریال" for k in range(e, end)): v = v / 10
            price, guessed = _thousands(v)
            drop.update(range(s, end))
            k = s - 1
            while k >= 0 and toks[k] in _PRICE_MARK: drop.add(k); k -= 1
    name = " ".join(t for i, t in enumerate(toks) if i not in drop).strip()
    while name.split() and name.split()[0] in _FILL: name = " ".join(name.split()[1:])
    return dict(qty=qty, name=name, price=price, guessed=guessed) if name else None

_HONORIFICS = {"آقای", "اقای", "خانم", "جناب", "دکتر", "مهندس", "شرکت", "فروشگاه", "حاج", "حاجی", "سرکار"}
_CMD_STOP = {"فاکتور", "فروش", "پیش", "باز", "کن", "بزن", "بساز", "ایجاد", "جدید", "بنویس", "ثبت", "صادر", "برای", "به", "نام",
             "مشتری", "را", "رو", "یک", "یه", "مانده", "حساب", "بدهی", "طلب", "چقدره", "چقدر", "است", "هست", "چنده", "چیه",
             "بده", "نشان", "نشون", "بگو", "صورت", "صورتحساب", "گردش", "و", "از", "لطفا", "میخوام", "می", "خوام", "میخواهم",
             "من", "برو", "نمایش", "فاکتورش", "ریز", "نقدی", "نسیه", "اعتباری"}

def _customer_guess(toks): return " ".join(t for t in toks if t not in _CMD_STOP and not _DIGITS.match(t))

def rank_customers(guess, customers):
    """customers: [(id, name)] -> [(score, id, name)] best first."""
    import difflib
    g = [w for w in norm_key(guess).split() if w not in _HONORIFICS]; gs = " ".join(g); out = []
    for cid, name in customers:
        n = [w for w in norm_key(name).split() if w not in _HONORIFICS]; ns = " ".join(n)
        if not gs or not ns: continue
        if gs == ns: sc = 1.0
        elif set(g) <= set(n) or set(n) <= set(g): sc = 0.9 - 0.02 * abs(len(g) - len(n))
        else:
            tok = sum(max(difflib.SequenceMatcher(None, a, b).ratio() for b in n) for a in g) / len(g)
            sc = max(difflib.SequenceMatcher(None, gs, ns).ratio(), tok * 0.9)
        out.append((sc, cid, name))
    out.sort(key=lambda x: -x[0]); return out

_TAB_WORDS = [("پیش فاکتورها", "proformas"), ("پیش فاکتور ها", "proformas"), ("فاکتورها", "invoices"), ("فاکتور ها", "invoices"),
              ("مشتریان", "customers"), ("مشتری ها", "customers"), ("دریافت", "payments"), ("پرداخت ها", "payments"),
              ("چک ها", "cheques"), ("چکها", "cheques"), ("بدهکاران", "reports"), ("گزارش", "reports"),
              ("تنظیمات", "settings"), ("پشتیبان", "settings")]

def parse_main_command(text):
    toks = _tok(text); t = " ".join(toks); words = set(toks)
    if not toks: return dict(action="none")
    if "صورتحساب" in words or "صورت حساب" in t or "گردش" in words:
        return dict(action="statement", customer=_customer_guess(toks))
    if words & {"مانده", "بدهی", "طلب"} or ("چقدر" in words or "چقدره" in words) and "حساب" in words:
        return dict(action="balance", customer=_customer_guess(toks))
    if "پیش فاکتور" in t and "پیش فاکتورها" not in t and "پیش فاکتور ها" not in t:
        return dict(action="new_proforma", customer=_customer_guess(toks))
    if "فاکتور" in words and words & {"باز", "جدید", "بزن", "ثبت", "بساز", "ایجاد", "صادر", "بنویس", "برای", "فروش"}:
        return dict(action="new_invoice", customer=_customer_guess(toks))
    for phrase, tab in _TAB_WORDS:
        if phrase in t: return dict(action="goto", tab=tab)
    return dict(action="unknown")

def parse_invoice_command(text):
    toks = _tok(text); t = " ".join(toks); words = set(toks)
    if not toks: return dict(action="none")
    if re.search(r"ردیف (اول|یک|1)|یکی یکی|ثبت اقلام|شروع (ثبت )?(اقلام|کالا)|(اقلام|کالاها) را (می ?گویم|میگم|میگویم)", t): return dict(action="entry_mode")
    if re.fullmatch(r"(ذخیره|ثبت|تمام|تموم|پایان|اتمام)( کن| شد| بزن| فاکتور| نهایی)*", t): return dict(action="save")
    if re.fullmatch(r"(ذخیره|ثبت) و (چاپ|پرینت)( کن| بزن| فاکتور)*", t): return dict(action="save_print")
    if "تخفیف" in words:
        if "بدون" in words: return dict(action="discount", value=0.0)
        spans = _find_spans(toks)
        if spans: return dict(action="discount", value=_thousands(spans[-1][2])[0])
    if words & {"حذف", "پاک"} and words & {"آخر", "آخری", "قبلی", "ردیف"}: return dict(action="delete_last")
    if "سررسید" in words:
        spans = _find_spans(toks)
        if spans:
            days = spans[0][2] * (30 if "ماه" in words else 7 if "هفته" in words else 1)
            return dict(action="due_days", days=int(days))
    kinds = {"نقدی": "نقدی", "نقد": "نقدی", "نسیه": "اعتباری", "اعتباری": "اعتباری", "چکی": "چکی", "چک": "چکی", "ترکیبی": "ترکیبی"}
    if len(toks) <= 4 and not _find_spans(toks):
        for k, v in kinds.items():
            if k in words: return dict(action="kind", value=v)
    if toks[0] in ("مشتری", "برای") and len(toks) > 1:
        g = _customer_guess(toks)
        if g: return dict(action="customer", customer=g)
    item = parse_item_phrase(text)
    return dict(action="item", item=item) if item else dict(action="unknown")

ASSISTANT_TIP_MAIN = ("مثال‌ها:\n• فاکتور فروش برای علی رضایی باز کن\n• پیش‌فاکتور برای شرکت پارس\n• مانده حساب علی رضایی\n"
                      "• صورت‌حساب رضایی\n• برو به چک‌ها\n\nکلید F9 هم شروع/پایان ضبط را می‌زند.")
ASSISTANT_TIP_INVOICE = ("مثال‌ها:\n• سه عدد لنت ترمز جلو قیمت هشتصد و پنجاه هزار\n• دو تا فیلتر روغن (قیمت از حافظه)\n• تخفیف پنجاه هزار\n"
                         "• نقدی / اعتباری / چکی\n• سررسید سی روز\n• مشتری علی رضایی\n• حذف ردیف آخر\n• ذخیره کن\n\nکلید F9 هم شروع/پایان ضبط را می‌زند.")

# ───────────── smart assistant: teaching data, wake word, AI agent ─────────────
def alias_target(kind, spoken):
    k = norm_key(spoken)
    if not k: return None
    with db() as con:
        r = con.execute("SELECT target FROM voice_alias WHERE kind=? AND spoken_key=?", (kind, k)).fetchone()
    return r["target"] if r else None

def learn_alias(kind, spoken, target, con=None):
    """Remember 'when I say X I mean Y' (kind = customer / product). Pass con inside an open transaction."""
    k = norm_key(spoken)
    if not k or not str(target).strip() or k == norm_key(target): return
    sql = ("INSERT INTO voice_alias(kind,spoken_key,spoken,target) VALUES(?,?,?,?) "
           "ON CONFLICT(kind,spoken_key) DO UPDATE SET target=excluded.target, uses=voice_alias.uses+1")
    args = (kind, k, str(spoken).strip(), str(target).strip())
    if con is not None: con.execute(sql, args)
    else:
        with db() as c: c.execute(sql, args)

def macro_for(text):
    key = " ".join(_tok(text))
    if not key: return None
    with db() as con:
        r = con.execute("SELECT * FROM voice_macro WHERE trigger_key=?", (key,)).fetchone()
    return dict(r) if r else None

def resolve_customer(guess, customers):
    """customers: [(id, name)] -> dict(status='ok', id, name) | dict(status='ambiguous', candidates=[(id,name)]) | dict(status='not_found')"""
    al = alias_target("customer", guess)
    if al:
        for cid, name in customers:
            if norm_key(name) == norm_key(al): return dict(status="ok", id=cid, name=name)
    ranked = rank_customers(guess, customers)
    if ranked and ranked[0][0] >= 0.85 and (len(ranked) < 2 or ranked[1][0] < ranked[0][0] - 0.08):
        return dict(status="ok", id=ranked[0][1], name=ranked[0][2])
    close = [r for r in ranked if r[0] >= 0.5][:3]
    if close: return dict(status="ambiguous", candidates=[(r[1], r[2]) for r in close])
    return dict(status="not_found")

def resolve_product(pm, spoken, customer_id=None):
    """-> (name, matched). Taught aliases first, then the product memory (fuzzy)."""
    al = alias_target("product", spoken)
    if al: return al, True
    sug = pm.suggest(spoken, customer_id, 1)
    if sug:
        import difflib
        kn, kc = norm_key(spoken), norm_key(sug[0])
        if set(kn.split()) <= set(kc.split()) or difflib.SequenceMatcher(None, kn, kc).ratio() >= 0.8: return sug[0], True
    return spoken, False

class VoskKit:
    """Vosk models are loaded once and shared (wake word + voice print)."""
    _inst = None
    def __init__(self):
        try:
            import vosk
        except Exception:
            raise RuntimeError("بسته‌ی vosk نصب نیست (در تنظیمات دستیار دکمه‌ی «نصب vosk» یا دستور pip install vosk).")
        try: vosk.SetLogLevel(-1)
        except Exception: pass
        import assistant_ai as A
        base = str(APP_DIR / "models")
        self.vosk = vosk; self.base = base
        self.model_dir = A.find_model_dir(base, "vosk-model-small-fa")
        if not self.model_dir: raise RuntimeError("مدل فارسی Vosk هنوز دانلود نشده است (تنظیمات دستیار ← دانلود مدل فارسی).")
        self.model = vosk.Model(self.model_dir)
        sp = A.find_model_dir(base, "vosk-model-spk")
        self.spk = vosk.SpkModel(sp) if sp else None
    @classmethod
    def get(cls):
        if cls._inst is None: cls._inst = cls()
        return cls._inst
    @classmethod
    def reset(cls): cls._inst = None

class VoiceHud(QWidget):
    """Small always-on-top banner that shows what the assistant is doing (never steals focus)."""
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True); self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 9, 16, 9)
        self.lbl = QLabel(""); self.lbl.setWordWrap(True); lay.addWidget(self.lbl)
        self.setMaximumWidth(560)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.hide)
    def show_text(self, text, color="#355c7d", ms=0):
        self.lbl.setText(text)
        self.setStyleSheet(f"background:{color};color:white;border-radius:10px;font-size:15px;")
        self.adjustSize()
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2, scr.top() + 28); self.show()
        self.timer.stop()
        if ms: self.timer.start(ms)

class WakeListener(QObject):
    """Always-on microphone loop. Audio is processed locally by Vosk and thrown away; only the wake word / yes / no events leave it."""
    event = Signal(str)
    failed = Signal(str)
    def __init__(self, parent):
        super().__init__(parent)
        self.engine = None; self.src = None; self.io = None; self.fmt = None; self.paused = False; self.running = False
        self.timer = QTimer(self); self.timer.setInterval(100); self.timer.timeout.connect(self._tick)
    def _device(self):
        from PySide6.QtMultimedia import QMediaDevices
        dev = QMediaDevices.defaultAudioInput(); want = get_pref("stt_mic", "")
        if want:
            for d in QMediaDevices.audioInputs():
                if d.description() == want: dev = d; break
        return dev
    def start(self):
        import assistant_ai as A
        kit = VoskKit.get()
        words = [w.strip() for w in re.split(r"[،,]", get_pref("wake_word", "دستیار")) if w.strip()] or ["دستیار"]
        yes = ["بله", "آره", "تایید", "باشه", "بفرما", "اوکی"]; no = ["نه", "نخیر", "لغو", "بیخیال"]
        g_wake = json.dumps([norm_key(w) for w in words] + ["[unk]"], ensure_ascii=False)
        g_conf = json.dumps(yes + no + ["[unk]"], ensure_ascii=False)
        rec = kit.vosk.KaldiRecognizer
        self.engine = A.WakeEngine(lambda: rec(kit.model, 16000, g_wake), lambda: rec(kit.model, 16000, g_conf), words, yes, no)
        self._open_source(); self.running = True; self.paused = False; self.timer.start()
    def _open_source(self):
        from PySide6.QtMultimedia import QAudioSource, QAudioFormat
        dev = self._device()
        if dev.isNull(): raise RuntimeError("میکروفونی پیدا نشد.")
        fmt = QAudioFormat(); fmt.setSampleRate(16000); fmt.setChannelCount(1); fmt.setSampleFormat(QAudioFormat.Int16)
        if not dev.isFormatSupported(fmt): raise RuntimeError("میکروفون، فرمت ۱۶ کیلوهرتز تک‌کاناله را پشتیبانی نمی‌کند.")
        self.fmt = fmt; self.src = QAudioSource(dev, fmt, self); self.io = self.src.start()
    def stop(self):
        self.running = False; self.timer.stop()
        try:
            if self.src: self.src.stop()
        except Exception: pass
        self.src = None; self.io = None
    def pause(self):
        if not self.running or self.paused: return
        self.paused = True; self.timer.stop()
        try: self.src.stop()
        except Exception: pass
        self.src = None; self.io = None
    def resume(self):
        if not self.running or not self.paused: return
        try:
            self._open_source(); self.engine.reset(); self.paused = False; self.timer.start()
        except Exception as e:
            self.running = False; self.failed.emit(str(e))
    def _tick(self):
        if self.paused or self.io is None: return
        try:
            data = bytes(self.io.readAll())
            if data:
                for ev in self.engine.feed(data): self.event.emit(ev)
        except Exception as e:
            self.stop(); self.failed.emit(str(e))
    def speaker_check(self):
        """-> (ok, distance, message). Uses the audio from the wake word until the confirmation."""
        import assistant_ai as A
        try:
            kit = VoskKit.get()
            if kit.spk is None: return False, 1.0, "مدل تشخیص گوینده دانلود نشده است."
            enrolled = json.loads(get_pref("voiceprint", "") or "null")
            if not enrolled: return False, 1.0, "صدای شما هنوز ثبت نشده است."
            vec = A.speaker_vector(kit.model, kit.spk, self.engine.audio_since_wake(), kit.vosk.KaldiRecognizer)
            ok, dist = A.speaker_ok(vec, enrolled, float(get_pref("spk_threshold", "0.5") or 0.5))
            return ok, dist, ("" if vec else "صدا برای تشخیص گوینده خیلی کوتاه بود.")
        except Exception as e:
            return False, 1.0, str(e)

class PcmRecorder(QObject):
    """Records a fixed number of seconds of 16 kHz mono PCM (used to enrol / test the voice print)."""
    done = Signal(object, str)
    def start(self, seconds):
        try:
            from PySide6.QtMultimedia import QAudioSource, QAudioFormat, QMediaDevices
            dev = QMediaDevices.defaultAudioInput(); want = get_pref("stt_mic", "")
            if want:
                for d in QMediaDevices.audioInputs():
                    if d.description() == want: dev = d; break
            fmt = QAudioFormat(); fmt.setSampleRate(16000); fmt.setChannelCount(1); fmt.setSampleFormat(QAudioFormat.Int16)
            if dev.isNull() or not dev.isFormatSupported(fmt): raise RuntimeError("میکروفون مناسب پیدا نشد.")
            self.src = QAudioSource(dev, fmt, self); self.buf = QBuffer(self); self.buf.open(QIODevice.WriteOnly); self.src.start(self.buf)
            QTimer.singleShot(int(seconds * 1000), self._stop)
        except Exception as e:
            self.done.emit(b"", str(e))
    def _stop(self):
        try: self.src.stop(); data = bytes(self.buf.data()); self.buf.close()
        except Exception as e: return self.done.emit(b"", str(e))
        self.done.emit(data, "")

# ───────────── form fields: every dialog field the AI may fill (declared in assistant_ai.FORM_FIELDS) ─────────────
def _pick_customer_combo(combo, name):
    customers = [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count()) if combo.itemData(i) is not None]
    r = resolve_customer(str(name), customers)
    if r["status"] == "ok": combo.setCurrentIndex(combo.findData(r["id"])); return
    if r["status"] == "ambiguous": raise ValueError("ambiguous customer, candidates: " + ", ".join(n for _, n in r["candidates"]))
    raise ValueError("customer not found: " + str(name))

def _pick_invoice_combo(combo, number):
    n = norm_key(str(number))
    for i in range(combo.count()):
        if norm_key(combo.itemText(i)).startswith(n + " "): combo.setCurrentIndex(i); return
    raise ValueError("invoice number not found for this customer: " + str(number))

def _set_jdate(widget, text):
    iso = j_to_iso(str(text))
    if not iso: raise ValueError("invalid Jalali date, use YYYY/MM/DD")
    widget.setIso(iso)

def _set_enum(combo, value):
    ix = combo.findText(str(value))
    if ix < 0: raise ValueError("not an allowed value: " + str(value))
    combo.setCurrentIndex(ix)

def form_setters(d, kind):
    """key -> function(value) for the open dialog (raises ValueError with a reason the AI can read)."""
    num = lambda w: (lambda v: w.setValue(float(v)))
    if kind == "customer":
        return {"name": lambda v: d.name.setText(str(v)), "phone": lambda v: d.phone.setText(str(v)),
                "address": lambda v: d.address.setText(str(v)), "notes": lambda v: d.notes.setPlainText(str(v))}
    if kind == "payment":
        return {"customer_name": lambda v: _pick_customer_combo(d.cbox, v), "related_invoice_no": lambda v: _pick_invoice_combo(d.ibox, v),
                "date": lambda v: _set_jdate(d.date, v), "amount": num(d.amount), "method": lambda v: _set_enum(d.method, v),
                "reference": lambda v: d.ref.setText(str(v)), "notes": lambda v: d.notes.setText(str(v))}
    if kind == "cheque":
        return {"customer_name": lambda v: _pick_customer_combo(d.cb, v), "related_invoice_no": lambda v: _pick_invoice_combo(d.ib, v),
                "cheque_no": lambda v: d.no.setText(str(v)), "bank": lambda v: d.bank.setText(str(v)), "issuer": lambda v: d.issuer.setText(str(v)),
                "amount": num(d.amount), "due_date": lambda v: _set_jdate(d.due, v), "status": lambda v: _set_enum(d.status, v),
                "notes": lambda v: d.notes.setText(str(v))}
    if kind == "invoice":
        return {"customer_name": lambda v: _pick_customer_combo(d.customer, v), "date": lambda v: _set_jdate(d.inv_date, v),
                "kind": lambda v: _set_enum(d.kind, v), "due_date": lambda v: _set_jdate(d.due, v), "description": lambda v: d.description.setText(str(v)),
                "discount": num(d.discount), "paid_amount": num(d.initial_paid), "notes": lambda v: d.notes.setText(str(v))}
    return {}

def apply_fields(setters, fields):
    """-> (names_set, {name: error}). Unknown names are reported, never ignored silently."""
    done, errors = [], {}
    for k, v in (fields or {}).items():
        if v in (None, ""): continue
        fn = setters.get(k)
        if fn is None: errors[k] = "unknown field"; continue
        try: fn(v); done.append(k)
        except Exception as e: errors[k] = str(e)
    return done, errors

def form_state(d, kind):
    """Current values (for the AI's context) – best effort."""
    def g(fn):
        try: return str(fn())
        except Exception: return ""
    if kind == "customer": return {"name": d.name.text(), "phone": d.phone.text(), "address": d.address.text()}
    if kind == "payment": return {"customer": d.cbox.currentText(), "amount": g(d.amount.value), "method": d.method.currentText()}
    if kind == "cheque": return {"customer": d.cb.currentText(), "amount": g(d.amount.value), "cheque_no": d.no.text(), "bank": d.bank.text(), "status": d.status.currentText()}
    return {}

def install_voice(dlg, parent, kind):
    """F9 inside this dialog talks to the assistant; also applies a form pre-fill requested by voice from the main window."""
    va = getattr(parent, "va", None)
    if not VOICE_ASSISTANT or va is None: return
    sc = QShortcut(QKeySequence("F9"), dlg); sc.activated.connect(va.listen_toggle)
    pre = getattr(parent, "_voice_form", None)
    if pre and pre.get("form") == kind:
        parent._voice_form = None
        _, errors = apply_fields(form_setters(dlg, kind), pre.get("fields") or {})
        if errors: va.hud.show_text("⚠ " + "؛ ".join(f"{k}: {v}" for k, v in errors.items()), "#b26a00", 7000)

class MainHost:
    """What the AI can do from the main window."""
    def __init__(self, win):
        import assistant_ai as A
        self.win = win; self.tools = A.TOOLS_MAIN + [A.report_tool([(k, l) for k, l, _ in REPORTS])]
    def describe(self): return f"Main window, current section: {self.win.tabs.tabText(self.win.tabs.currentIndex())}."
    def offline(self, text): self.win.voice_command(text)
    def _customers(self):
        with db() as con: return [(r["id"], r["name"]) for r in con.execute("SELECT id,name FROM customers")]
    def _pick(self, guess):
        r = resolve_customer(guess, self._customers())
        if r["status"] == "ambiguous": return None, {"error": "ambiguous", "candidates": [n for _, n in r["candidates"]]}
        if r["status"] == "not_found": return None, {"error": "not_found", "customer": guess}
        return r["id"], None
    def _tab(self, key):
        names = dict(invoices="فاکتورها", proformas="پیش فاکتورها", customers="مشتریان", payments="دریافت ها", cheques="چک ها", reports="گزارش ها و بدهکاران", settings="پشتیبان و تنظیمات")
        for i in range(self.win.tabs.count()):
            if norm_key(self.win.tabs.tabText(i)) == names.get(key, ""): self.win.tabs.setCurrentIndex(i); return True
        return False
    def open_invoice(self, args):
        cid = None; guess = str(args.get("customer_name") or "").strip()
        if guess:
            cid, err = self._pick(guess)
            if err: return err
        self.win._voice_prefill = dict(customer_id=cid, kind=args.get("kind") or "", items=args.get("items") or [],
                                       discount=float(args.get("discount") or 0), due_days=int(args.get("due_days") or 0))
        QTimer.singleShot(0, self.win.new_proforma if args.get("proforma") else self.win.new_invoice)
        return {"ok": True, "note": "window opened; the user continues in it"}
    def quick_info(self, what):
        today = today_iso(); w = self.win
        with db() as con:
            invs = invoice_rows(con)
            if what == "overdue":
                over = [i for i in invs if i["remain"] > 0.5 and i["due_date"] and i["due_date"] < today]
                msg = f"{len(over)} فاکتور سررسید گذشته — جمع مانده: {money(sum(i['remain'] for i in over))} تومان" if over else "فاکتور سررسید گذشته‌ای نیست ✓"
            elif what == "total_debts":
                f = customer_figures(con); debt = sum(x["balance"] for x in f.values() if x["balance"] > 0.5)
                msg = f"جمع مانده‌ی بدهکاران: {money(debt)} تومان ({sum(1 for x in f.values() if x['balance'] > 0.5)} مشتری)"
            elif what == "cheques_due_soon":
                end = (date.today() + timedelta(days=7)).isoformat(); ph = ",".join("?" * len(CHQ_CLOSED))
                r = con.execute(f"SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM cheques WHERE due_date>=? AND due_date<=? AND status NOT IN ({ph})", (today, end, *CHQ_CLOSED)).fetchone()
                msg = f"{r['n']} چک تا ۷ روز آینده سررسید دارد — جمع: {money(r['s'])} تومان"
            elif what in ("today_sales", "month_sales"):
                start = today if what == "today_sales" else today[:8] + "01"
                sel = [i for i in invs if i["invoice_date"] >= start and i["invoice_date"] <= today]
                msg = f"{'فروش امروز' if what == 'today_sales' else 'فروش این ماه (میلادی)'}: {len(sel)} فاکتور — {money(sum(i['total'] for i in sel))} تومان"
            else: return {"error": "unknown"}
        QTimer.singleShot(0, lambda: info(w, "اطلاعات سریع", msg)); return {"ok": True, "shown": True}
    def execute(self, name, args):
        w = self.win
        if name == "open_invoice": return self.open_invoice(args)
        if name in ("customer_balance", "open_statement"):
            cid, err = self._pick(str(args.get("customer_name") or ""))
            if err: return err
            QTimer.singleShot(0, (lambda c=cid: w.show_statement(c)) if name == "open_statement" else (lambda c=cid: w.show_balance(c)))
            return {"ok": True, "shown": True}
        if name == "create_customer":
            nm = str(args.get("name") or "").strip()
            if not nm: return {"error": "empty name"}
            with db() as con: con.execute("INSERT INTO customers(name,created_at) VALUES(?,?)", (nm, datetime.now().isoformat(timespec="seconds")))
            w.refresh_all(); return {"ok": True, "created": nm}
        if name == "go_to_tab": return {"ok": True} if self._tab(args.get("tab")) else {"error": "unknown tab"}
        if name == "show_overdue": self._tab("invoices"); return self.quick_info("overdue")
        if name == "quick_info": return self.quick_info(str(args.get("what") or ""))
        if name == "backup_now":
            p = auto_backup(force=True); return {"ok": True, "file": p.name if p else ""}
        if name == "open_form":
            kind = args.get("form"); opener = {"customer": w.new_customer, "payment": w.new_payment, "cheque": w.new_cheque}.get(kind)
            if opener is None: return {"error": "unknown form"}
            w._voice_form = dict(form=kind, fields={k: v for k, v in args.items() if k != "form"})
            QTimer.singleShot(0, opener); return {"ok": True, "note": "form opened and filled; continue by voice inside it, then save"}
        if name == "search_invoices":
            self._tab("invoices")
            w.inv_search.setText(str(args.get("text") or ""))
            for combo, key in ((w.inv_year, "year"), (w.inv_month, "month"), (w.inv_day, "day")):
                v = args.get(key); combo.setCurrentIndex(max(0, combo.findData(int(v)))) if v else combo.setCurrentIndex(0)
            w.refresh_invoices(); return {"ok": True, "shown": True}
        if name == "open_invoice_by_number":
            no = str(args.get("number") or "").strip()
            with db() as con: r = con.execute("SELECT id FROM invoices WHERE invoice_no=?", (no,)).fetchone()
            if not r: return {"error": "not_found", "number": no}
            iid = r["id"]; act = args.get("action")
            if act in ("view", "print"): QTimer.singleShot(0, lambda: w.preview_invoice(iid=iid))
            elif act == "receipt": QTimer.singleShot(0, lambda: w.print_receipt(iid))
            elif act == "share": QTimer.singleShot(0, lambda: ShareDialog(w, lambda: create_invoice_pdf(iid)).exec())
            elif act == "pdf": QTimer.singleShot(0, lambda: reveal_in_folder(create_invoice_pdf(iid)))
            else: return {"error": "unknown action"}
            return {"ok": True}
        if name == "run_report":
            keys = [k for k, _, _ in REPORTS]; key = args.get("report")
            if key not in keys: return {"error": "unknown report"}
            w.rep_type.setCurrentIndex(keys.index(key))
            g = str(args.get("customer_name") or "").strip()
            if g:
                cs = [(w.rep_cust.itemData(i), w.rep_cust.itemText(i)) for i in range(w.rep_cust.count()) if w.rep_cust.itemData(i) is not None]
                r = resolve_customer(g, cs)
                if r["status"] != "ok": return {"error": r["status"], "customer": g, **({"candidates": [n for _, n in r["candidates"]]} if r["status"] == "ambiguous" else {})}
                w.rep_cust.setCurrentIndex(w.rep_cust.findData(r["id"]))
            for key2, widget in (("from_date", w.rep_from), ("to_date", w.rep_to)):
                if args.get(key2):
                    iso = j_to_iso(str(args[key2]))
                    if not iso: return {"error": "invalid date " + str(args[key2])}
                    widget.setIso(iso)
            self._tab("reports"); w.run_report(); return {"ok": True, "shown": True}
        return {"error": "unknown tool " + name}

class DialogHost:
    """What the AI can do inside the open invoice window."""
    def __init__(self, dlg):
        import assistant_ai as A
        self.dlg = dlg
        self.tools = A.TOOLS_INVOICE + [A.fields_tool(A.FORM_FIELDS["invoice"], "Set header fields of the open invoice (customer, date, kind, due date, description, discount, paid amount, internal note).")]
    def describe(self):
        d = self.dlg
        return (f"A NEW {'proforma' if d.pf else 'sales invoice'} window is open. Customer: {d.customer.currentText() or '-'}; "
                f"rows so far: {d.items.rowCount()}; kind: {d.kind.currentText() if not d.pf else '-'}.")
    def offline(self, text): self.dlg._assistant_offline(text)
    def execute(self, name, args):
        d = self.dlg
        if name == "add_items":
            n = 0
            for it in args.get("items") or []:
                nm = str(it.get("name") or "").strip()
                if not nm: continue
                price = it.get("unit_price"); price = float(price) if price not in (None, "") else None
                d._add_voice_item(nm, float(it.get("quantity") or 1), price, False, str(it.get("description") or "")); n += 1
            return {"ok": True, "added": n, "rows_total": d.items.rowCount()}
        if name == "set_fields":
            done, errors = apply_fields(form_setters(d, "invoice"), args)
            return {"ok": not errors, "set": done, "errors": errors}
        if name == "set_customer":
            customers = [(d.customer.itemData(i), d.customer.itemText(i)) for i in range(d.customer.count())]
            r = resolve_customer(str(args.get("customer_name") or ""), customers)
            if r["status"] == "ambiguous": return {"error": "ambiguous", "candidates": [n for _, n in r["candidates"]]}
            if r["status"] == "not_found": return {"error": "not_found", "customer": args.get("customer_name")}
            d.customer.setCurrentIndex(d.customer.findData(r["id"])); return {"ok": True, "customer": r["name"]}
        if name == "create_customer":
            nm = str(args.get("name") or "").strip()
            if not nm: return {"error": "empty name"}
            with db() as con: cid = con.execute("INSERT INTO customers(name,created_at) VALUES(?,?)", (nm, datetime.now().isoformat(timespec="seconds"))).lastrowid
            d.customer.addItem(nm, cid); d.customer.setCurrentIndex(d.customer.findData(cid)); return {"ok": True, "created": nm}
        if name == "set_discount":
            d.discount.setValue(float(args.get("amount") or 0)); return {"ok": True}
        if name == "set_kind":
            if d.pf: return {"error": "proforma has no payment kind"}
            ix = d.kind.findText(str(args.get("kind") or ""))
            if ix < 0: return {"error": "unknown kind"}
            d.kind.setCurrentIndex(ix); return {"ok": True}
        if name == "set_due_days":
            d.due.setIso((date.today() + timedelta(days=int(args.get("days") or 0))).isoformat()); return {"ok": True, "applies": bool(d.due.isEnabled())}
        if name == "remove_last_item":
            n = d.items.rowCount()
            if n: d.items.removeRow(n - 1); d.recalc()
            return {"ok": True, "rows_total": d.items.rowCount()}
        if name == "save_invoice":
            QTimer.singleShot(0, lambda: d.save_validate(bool(args.get("print_after")))); return {"ok": True}
        return {"error": "unknown tool " + name}

class FormHost:
    """What the AI can do inside a customer / payment / cheque form."""
    def __init__(self, dlg, kind):
        import assistant_ai as A
        self.dlg = dlg; self.kind = kind
        self.tools = A.TOOLS_FORM + [A.fields_tool(A.FORM_FIELDS[kind], f"Fill fields of the open {kind} form. Only send fields the user actually mentioned.")]
    def describe(self): return f"A '{self.kind}' form is open. Current values: {json.dumps(form_state(self.dlg, self.kind), ensure_ascii=False)}."
    def offline(self, text):
        t = " ".join(_tok(text))
        if re.fullmatch(r"(ذخیره|ثبت|تمام)( کن| شد| بزن)*", t): QTimer.singleShot(0, self.dlg.accept); return None
        if re.fullmatch(r"(لغو|انصراف|بیخیال|ببند)( کن| شد)*", t): QTimer.singleShot(0, self.dlg.reject); return None
        return "برای پر کردن این فرم با صدا، هوش مصنوعی را روشن کن (تنظیمات دستیار). فعلاً فقط «ذخیره کن» و «لغو» کار می‌کنند."
    def execute(self, name, args):
        d = self.dlg
        if name == "set_fields":
            done, errors = apply_fields(form_setters(d, self.kind), args)
            return {"ok": not errors, "set": done, "errors": errors}
        if name == "save_form": QTimer.singleShot(0, d.accept); return {"ok": True}
        if name == "cancel_form": QTimer.singleShot(0, d.reject); return {"ok": True}
        return {"error": "unknown tool " + name}

class VoiceAssistant(QObject):
    """Conversation manager: wake word -> confirm -> (voice check) -> (passphrase) -> command -> AI agent (ask / act / confirm)
    or guided row-by-row entry -> follow-up. Everything that needs a decision goes through yes / no questions."""
    turn_ready = Signal(object, str)
    END_WORDS = ("تمام", "خداحافظ", "بس", "کافیه", "لغو", "بیخیال", "ممنون")
    ENTRY_END = ("تمام", "همین", "پایان", "اتمام", "کافیه", "بس")
    YES_WORDS = ("بله", "آره", "تایید", "باشه", "اوکی", "بزن", "ذخیره", "درسته", "بفرما", "صحیح", "ok")
    NO_WORDS = ("نه", "نخیر", "خیر", "نمیخوام", "نمی", "لغو", "بیخیال", "اشتباه")
    SKIP_WORDS = ("بعدا", "بعداً", "نمیدونم", "رد", "بیخیال", "هیچی")
    def __init__(self, win):
        super().__init__(win)
        self.win = win; self.hud = VoiceHud(); self.listener = None
        self.dict = Dictation(self, None, self._on_text, on_error=self._on_listen_problem)
        self.agent = None; self.ctx = None; self.pending = []; self.results = []; self.current = None
        self.mode = "idle"; self.busy = False; self.from_wake = False; self.orig_text = ""; self.tries = 0; self.tts = None; self.tts_ok = False
        self.want_entry = False; self.entry_pending = None; self.entry_row = -1; self.entry_count = 0
        self.turn_ready.connect(self._on_turn)
    # ── helpers ──
    def ai_enabled(self): return get_pref("ai_enabled", "0") == "1" and bool(get_pref("gemini_key", "").strip())
    def listening(self): return self.listener is not None and self.listener.running
    def _is_yes(self, text): t = _tok(text); return any(w in t for w in self.YES_WORDS)
    def _is_no(self, text): t = _tok(text); return any(w in t for w in self.NO_WORDS) and not self._is_yes(text)
    def listen_toggle(self):
        """F9: stop the current recording, or start one command (works inside every dialog)."""
        if self.dict.recording: return self.dict.stop()
        if self.busy: return
        self.from_wake = False; self._begin_command()
    # ── always-on ──
    def start_always_on(self):
        if self.listening(): return True
        try:
            self.listener = WakeListener(self); self.listener.event.connect(self._on_wake_event); self.listener.failed.connect(self._on_listener_failed)
            self.listener.start()
        except Exception as e:
            self.listener = None; self._on_listener_failed(str(e)); return False
        self.win.update_listen_label(); return True
    def stop_always_on(self):
        if self.listener: self.listener.stop(); self.listener = None
        self.busy = False; self.mode = "idle"; self.win.update_listen_label()
    def _on_listener_failed(self, msg):
        self.listener = None; self.busy = False; self.win.update_listen_label()
        self.hud.show_text("⚠ گوش‌دادن همیشگی متوقف شد: " + msg, "#c62828", 7000)
    def _on_wake_event(self, ev):
        if ev == "wake":
            QApplication.beep()
            if get_pref("wake_confirm", "1") == "1": self.hud.show_text("🎧 شنیدم! فعال شوم؟ بگو «بله»", "#355c7d")
            else: self.listener.engine.reset(); self._after_confirm()
        elif ev == "yes": self._after_confirm()
        elif ev == "no": self.hud.show_text("باشه، لغو شد.", "#5a6b7b", 2000)
        elif ev == "timeout": self.hud.show_text("جوابی نشنیدم.", "#5a6b7b", 2000)
    def _after_confirm(self):
        self.from_wake = True; self.busy = True
        if get_pref("spk_enabled", "0") == "1":
            ok, dist, msg = self.listener.speaker_check()
            if not ok:
                self.hud.show_text(f"🔒 صدا شناخته نشد ({dist:.2f}) {msg}", "#c62828", 4000); self.busy = False; self.from_wake = False
                self.listener.engine.reset(); return
        if get_pref("pass_enabled", "0") == "1" and get_pref("pass_hash", ""): self.tries = 2; self._ask_passphrase()
        else: self._begin_command()
    def _ask_passphrase(self):
        self.mode = "passphrase"; self.listener.pause(); self.hud.show_text("🔐 رمز را بگو", "#6a1b9a")
        if not self.dict.start(first_speech_ms=7000, force_auto=True): self._finish(False)
    def _begin_command(self):
        self.mode = "command"; self.busy = True
        if self.listener: self.listener.pause()
        self.hud.show_text("🎙 بفرمایید…", "#2e7d32")
        if not self.dict.start(first_speech_ms=8000, force_auto=True): self._finish(False)
    # ── listening results ──
    def _on_text(self, text):
        m = self.mode
        try:
            if m == "passphrase":
                import assistant_ai as A
                if A.check_phrase(text, get_pref("pass_salt", ""), get_pref("pass_hash", "")): return self._begin_command()
                self.tries -= 1
                if self.tries > 0:
                    self.hud.show_text("رمز درست نبود؛ دوباره بگو", "#b26a00")
                    if not self.dict.start(first_speech_ms=7000, force_auto=True): self._finish(False)
                else:
                    self.hud.show_text("🔒 رمز نادرست", "#c62828", 3000); self._finish(False)
            elif m == "command":
                toks = _tok(text)
                if toks and len(toks) <= 3 and any(w in toks for w in self.END_WORDS):
                    self.hud.show_text("باشه 👋", "#5a6b7b", 1500); return self._finish(False)
                self.handle_text(text)
            elif m == "answer": self._on_answer(text)
            elif m == "confirm": self._on_confirm(text)
            elif m == "entry": self._entry_text(text)
            elif m == "entry_confirm": self._entry_confirm(text)
            elif m == "entry_price": self._entry_price(text)
        except RuntimeError:           # the window was closed while we were listening
            self._finish(False)
    def _on_listen_problem(self, msg):
        kind = self.dict.error_kind
        if self.mode.startswith("entry"):
            self.hud.show_text(f"✅ ثبت اقلام پایان یافت ({self.entry_count} ردیف) — صدایی نشنیدم.", "#5a6b7b", 3500)
        elif self.mode in ("answer", "confirm"): self.hud.show_text("جوابی نشنیدم؛ کار لغو شد.", "#5a6b7b", 3000)
        else: self.hud.show_text("چیزی نشنیدم." if kind == "silence" else "⚠ " + msg, "#5a6b7b", 2500)
        self._finish(False)
    # ── understanding a command ──
    def _context(self):
        w = QApplication.activeModalWidget()
        if isinstance(w, InvoiceDialog): return DialogHost(w)
        if isinstance(w, CustomerDialog): return FormHost(w, "customer")
        if isinstance(w, PaymentDialog): return FormHost(w, "payment")
        if isinstance(w, ChequeDialog): return FormHost(w, "cheque")
        return MainHost(self.win)
    def handle_text(self, text, ctx=None):
        self.ctx = ctx or self._context(); self.orig_text = text; self.busy = True
        self.hud.show_text("🎙 «" + text + "»", "#355c7d")
        macro = macro_for(text)
        if macro: self._run_macro(macro); return self._finish()
        if not self.ai_enabled(): return self._offline(text)
        try: self._start_agent(text)
        except Exception as e:
            self.hud.show_text("⚠ " + str(e), "#b26a00", 4000); self._offline(text)
    def _offline(self, text):
        try: msg = self.ctx.offline(text)
        except Exception as e: msg = "⚠ " + str(e)
        if isinstance(msg, str) and msg: self.hud.show_text(msg, "#b26a00", 5000)
        if self.mode.startswith("entry"): return      # guided entry took over
        self._finish()
    def _run_macro(self, m):
        items = []
        for part in re.split(r"[;؛\n]", m.get("items") or ""):
            it = parse_item_phrase(part) if part.strip() else None
            if it: items.append(dict(name=it["name"], quantity=it["qty"], unit_price=it["price"]))
        args = dict(customer_name=m.get("customer") or "", proforma=bool(m.get("proforma")), kind=m.get("kind") or "", items=items, discount=m.get("discount") or 0)
        res = MainHost(self.win).open_invoice(args)
        self.hud.show_text("⚡ میانبر «" + m["trigger"] + "» اجرا شد" if res.get("ok") else "⚠ میانبر: " + str(res.get("error")), "#2e7d32" if res.get("ok") else "#b26a00", 3500)
    def _start_agent(self, text):
        import assistant_ai as A
        model = get_pref("gemini_model", "").strip() or A.DEFAULT_MODELS[0]
        sysmsg = A.build_system_prompt(self.ctx.describe(), date.today().isoformat(), iso_to_j(today_iso()), get_pref("ai_notes", ""))
        self.agent = A.Agent(get_pref("gemini_key", "").strip(), model, sysmsg, self.ctx.tools)
        self._net(lambda: self.agent.send_text(text))
    def _net(self, fn):
        self.busy = True; self.hud.show_text("⏳ در حال فکر کردن…", "#5a6b7b")
        import threading
        def work():
            try: self.turn_ready.emit(fn(), "")
            except Exception as e: self.turn_ready.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()
    def _on_turn(self, turn, err):
        if self.agent is None: return
        if err:
            self.hud.show_text("⚠ " + err + " — حالت آفلاین", "#b26a00", 4500)
            try: self.ctx.offline(self.orig_text)
            except Exception: pass
            return self._finish()
        if turn["calls"]: self.pending = list(turn["calls"]); self.results = []; return self._run_calls()
        msg = turn["text"] or "انجام شد."
        self.hud.show_text("✅ " + msg, "#2e7d32", 4500); self.speak(msg); self._finish()
    def _run_calls(self):
        while self.pending:
            call = self.pending.pop(0); name = call["name"]; args = call["args"]
            if name in ("ask_user", "confirm_user"): self.current = call; return self._ask(str(args.get("question") or "؟"), name == "confirm_user")
            if name in ("save_invoice", "save_form"): self.current = call; return self._ask_confirm()
            if name == "start_item_entry":
                self.want_entry = True; self.results.append(dict(name=name, response={"ok": True, "note": "guided entry starts when you finish"}, id=call.get("id"))); continue
            self.results.append(dict(name=name, response=self._exec(name, args), id=call.get("id")))
        res = self.results; self.results = []
        self._net(lambda: self.agent.send_results(res))
    def _exec(self, name, args):
        try:
            if name == "teach_alias":
                learn_alias("customer" if args.get("kind") == "customer" else "product", str(args.get("spoken") or ""), str(args.get("meaning") or ""))
                return {"ok": True}
            if name == "teach_rule":
                rule = str(args.get("rule") or "").strip()
                if not rule: return {"error": "empty rule"}
                old = get_pref("ai_notes", ""); set_pref("ai_notes", ((old + "\n") if old else "") [-2900:] + "• " + rule)
                return {"ok": True}
            return self.ctx.execute(name, args) or {"ok": True}
        except Exception as e: return {"error": str(e)}
    def _ask(self, question, yes_no=False):
        self.mode = "answer"; self.hud.show_text("❓ " + question + ("  (بله / نه)" if yes_no else ""), "#e65100"); QApplication.beep(); self.speak(question)
        if self.listener: self.listener.pause()
        if not self.dict.start(first_speech_ms=9000, force_auto=True): self._finish(False)
    def _on_answer(self, text):
        c = self.current; self.mode = "idle"; resp = {"answer": text}
        if c["name"] == "confirm_user": resp.update(confirmed=self._is_yes(text), declined=self._is_no(text))
        self.results.append(dict(name=c["name"], response=resp, id=c.get("id"))); self._run_calls()
    def _ask_confirm(self):
        self.mode = "confirm"; self.hud.show_text("💾 ذخیره کنم؟ بگو «بله» یا «نه»", "#e65100"); QApplication.beep(); self.speak("ذخیره کنم؟")
        if self.listener: self.listener.pause()
        if not self.dict.start(first_speech_ms=8000, force_auto=True): self._finish(False)
    def _on_confirm(self, text):
        c = self.current; self.mode = "idle"
        res = self._exec(c["name"], c["args"]) if self._is_yes(text) else {"cancelled": True, "note": "the user declined"}
        self.results.append(dict(name=c["name"], response=res, id=c.get("id"))); self._run_calls()
    # ── guided row-by-row entry (offline, with suggestions and yes/no) ──
    def start_entry(self, host):
        self.ctx = host; self.busy = True; self.entry_count = 0; self.entry_pending = None; self._entry_next()
    def _entry_listen(self, mode, text, color="#2e7d32", ms=12000):
        self.mode = mode; self.hud.show_text(text, color)
        if self.listener: self.listener.pause()
        if not self.dict.start(first_speech_ms=ms, force_auto=True): self._finish(False)
    def _filled_rows(self):
        it = self.ctx.dlg.items
        return sum(1 for i in range(it.rowCount()) if it.item(i, 0) and it.item(i, 0).text().strip())
    def _entry_next(self):
        self._entry_listen("entry", f"📝 ردیف {self._filled_rows() + 1} — نام کالا؟  (برای پایان بگو «تمام»)")
    def _entry_text(self, text):
        toks = _tok(text); dlg = self.ctx.dlg
        if toks and len(toks) <= 3 and any(w in toks for w in self.ENTRY_END):
            self.hud.show_text(f"✅ ثبت اقلام تمام شد ({self.entry_count} ردیف). حالا بگو «ذخیره کن» یا ادامه بده.", "#2e7d32", 4500); return self._finish()
        if set(toks) & {"حذف", "پاک"} and set(toks) & {"آخر", "آخری", "قبلی"}:
            n = dlg.items.rowCount()
            for i in range(n - 1, -1, -1):
                if dlg.items.item(i, 0) and dlg.items.item(i, 0).text().strip(): dlg.items.removeRow(i); dlg.recalc(); self.entry_count = max(0, self.entry_count - 1); break
            self.hud.show_text("🗑 آخرین ردیف حذف شد.", "#5a6b7b", 1500); return self._entry_next()
        it = parse_item_phrase(text)
        if not it: self.hud.show_text("اسم کالا را بگو (مثلاً «۲ لنت ترمز»).", "#b26a00", 2500); return self._entry_next()
        status, value, more = self._entry_decide(dlg, it["name"], dlg.customer.currentData())
        if status == "ok": return self._entry_add(value, it["qty"], it["price"], it["guessed"], raw=it["name"])
        if status == "new": return self._entry_add(it["name"], it["qty"], it["price"], it["guessed"], raw=it["name"])
        self.entry_pending = dict(spoken=it["name"], qty=it["qty"], price=it["price"], guessed=it["guessed"], suggestion=value, more=more)
        self._entry_ask_suggestion()
    def _entry_decide(self, dlg, spoken, cid):
        """-> ('ok', name, []) sure | ('ask', suggestion, [other suggestions]) unsure | ('new', spoken, []) unknown product."""
        al = alias_target("product", spoken)
        if al: return "ok", al, []
        pm = dlg.pm; key = norm_key(spoken)
        if key in pm.by_key: return "ok", pm.by_key[key]["name"], []
        cands = pm.suggest(spoken, cid, 4); toks = set(key.split())
        if len(cands) == 1 and toks <= set(norm_key(cands[0]).split()): return "ok", cands[0], []
        if cands: return "ask", cands[0], cands[1:]
        return "new", spoken, []
    def _entry_ask_suggestion(self):
        p = self.entry_pending
        self.speak(f"منظورت {p['suggestion']} است؟")
        self._entry_listen("entry_confirm", f"❓ «{p['spoken']}» را دقیق نمی‌شناسم. منظورت «{p['suggestion']}» است؟  (بله / نه — یا اسم درست را بگو)", "#e65100", 9000)
    def _entry_confirm(self, text):
        p = self.entry_pending
        if self._is_yes(text):
            self.entry_pending = None; learn_alias("product", p["spoken"], p["suggestion"])
            self.hud.show_text(f"👍 یاد گرفتم: «{p['spoken']}» یعنی «{p['suggestion']}»", "#2e7d32", 2000)
            return self._entry_add(p["suggestion"], p["qty"], p["price"], p["guessed"], raw=p["spoken"])
        if self._is_no(text):
            if p["more"]:
                p["suggestion"] = p["more"].pop(0); return self._entry_ask_suggestion()
            self.entry_pending = None
            self.hud.show_text(f"باشه، «{p['spoken']}» به‌عنوان کالای جدید ثبت می‌شود.", "#5a6b7b", 2000)
            return self._entry_add(p["spoken"], p["qty"], p["price"], p["guessed"], raw=p["spoken"])
        self.entry_pending = None
        self._entry_text(text)       # neither yes nor no: treat it as the correct name
    def _entry_add(self, name, qty, price, guessed, raw=None):
        dlg = self.ctx.dlg
        r, name, price2, from_mem = dlg._add_voice_item(name, qty, price, guessed, resolve=False, raw=raw)
        self.entry_count += 1; self.entry_row = r
        if price2 is None:
            self.hud.show_text(f"✔ {fmt_qty(qty)} × {name}", "#2e7d32", 1500)
            return self._entry_listen("entry_price", f"💰 قیمت «{name}» چقدر است؟  (یا بگو «بعداً»)", "#e65100", 9000)
        self.hud.show_text(f"✔ ردیف {r + 1}: {fmt_qty(qty)} × {name} — {money(price2)} تومان" + (" (از حافظه)" if from_mem else ""), "#2e7d32", 1800)
        self._entry_next()
    def _entry_price(self, text):
        toks = _tok(text)
        if not (set(toks) & set(self.SKIP_WORDS)):
            spans = _find_spans(toks)
            if spans:
                v = _thousands(spans[-1][2])[0]; self.ctx.dlg._set_row_price(self.entry_row, v)
                self.hud.show_text(f"💰 {money(v)} تومان", "#2e7d32", 1500)
            else: self.hud.show_text("قیمت را نفهمیدم؛ بعداً خودت وارد کن.", "#b26a00", 2000)
        self._entry_next()
    # ── end of a task ──
    def _finish(self, followup=True):
        self.busy = False; self.mode = "idle"; self.agent = None; self.pending = []; self.results = []; self.entry_pending = None
        if self.want_entry and isinstance(self.ctx, DialogHost) and followup:
            self.want_entry = False; return self.start_entry(self.ctx)
        self.want_entry = False
        if self.listener and self.listener.running:
            if followup and self.from_wake and get_pref("followup", "1") == "1":
                self.mode = "command"; self.busy = True
                self.hud.show_text("🎧 ادامه بده… (یا بگو «تمام»)", "#355c7d"); self.listener.pause()
                if not self.dict.start(first_speech_ms=8000, force_auto=True): self._finish(False)
                return
            self.listener.resume()
        self.from_wake = False
    def speak(self, text):
        """Optional spoken reply – only if Windows has a Persian voice installed (most PCs do not)."""
        if get_pref("tts", "1") != "1": return
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
            from PySide6.QtCore import QLocale
            if self.tts is None:
                self.tts = QTextToSpeech(self)
                voices = [v for v in self.tts.availableVoices() if v.locale().language() == QLocale.Persian]
                self.tts_ok = bool(voices)
                if voices: self.tts.setVoice(voices[0])
            if self.tts_ok: self.tts.say(text)
        except Exception:
            pass

class _DlSignals(QObject):
    progress = Signal(int, int)
    done = Signal(str)

class AssistantSettingsDialog(QDialog):
    """Teach the assistant (aliases, shortcuts, notes for the AI) and configure AI / always-on listening / voice lock."""
    def __init__(self, win):
        super().__init__(win); self.win = win
        self.setWindowTitle("آموزش و تنظیمات دستیار هوشمند"); self.resize(920, 660)
        lay = QVBoxLayout(self); self.tabs = QTabWidget(); lay.addWidget(self.tabs)
        self._build_alias_tab(); self._build_macro_tab(); self._build_notes_tab(); self._build_ai_tab(); self._build_voice_tab()
        bb = bbox(QDialogButtonBox.Save | QDialogButtonBox.Close); lay.addWidget(bb)
        bb.accepted.connect(self.save_all); bb.rejected.connect(self.reject)
        self.dl = _DlSignals(); self.dl.progress.connect(self._on_progress); self.dl.done.connect(self._on_dl_done)
        self.refresh_status()
    # ── tabs ──
    def _table_tab(self, title, headers, hint):
        w = QWidget(); l = QVBoxLayout(w); h = QLabel(hint); h.setWordWrap(True); l.addWidget(h)
        t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers); t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive); l.addWidget(t)
        row = QHBoxLayout(); a = QPushButton("＋ افزودن"); d = QPushButton("حذف ردیف انتخاب‌شده"); row.addWidget(a); row.addWidget(d); row.addStretch(); l.addLayout(row)
        d.clicked.connect(lambda: t.removeRow(t.currentRow()) if t.currentRow() >= 0 else None)
        self.tabs.addTab(w, title); return t, a
    def _build_alias_tab(self):
        self.alias_t, add = self._table_tab("مترادف‌ها", ["نوع", "وقتی می‌گویم", "یعنی (نام واقعی در برنامه)"],
            "اینجا به دستیار یاد می‌دهی کلمه‌ها یعنی چه؛ مثلاً «لنت» یعنی «لنت ترمز جلو» یا «آقا رضا» یعنی «رضا کریمی». "
            "اگر هنگام انتخاب مشتری یا ویرایش نام کالا چیزی را اصلاح کنی، خودش هم اضافه می‌کند.")
        add.clicked.connect(lambda: self._add_alias_row("مشتری", "", ""))
        with db() as con:
            for r in con.execute("SELECT kind,spoken,target FROM voice_alias ORDER BY kind, spoken"):
                self._add_alias_row("مشتری" if r["kind"] == "customer" else "کالا", r["spoken"], r["target"])
    def _add_alias_row(self, kind, spoken, target):
        t = self.alias_t; r = t.rowCount(); t.insertRow(r)
        c = QComboBox(); c.addItems(["مشتری", "کالا"]); c.setCurrentText(kind); t.setCellWidget(r, 0, c)
        t.setItem(r, 1, QTableWidgetItem(spoken)); t.setItem(r, 2, QTableWidgetItem(target))
    def _build_macro_tab(self):
        self.macro_t, add = self._table_tab("میانبرها", ["عبارت فعال‌ساز", "مشتری", "نوع (نقدی/اعتباری/چکی/ترکیبی یا خالی)", "اقلام (با ؛ جدا کن)", "پیش‌فاکتور؟ (بله/خیر)"],
            "با گفتن یک عبارت، یک فاکتور از پیش آماده باز می‌شود. مثال: عبارت «سفارش همیشگی رضایی» ← مشتری «علی رضایی» ← "
            "اقلام «۲ لنت ترمز جلو ۸۵۰ هزار؛ ۱ فیلتر روغن». میانبرها قبل از هوش مصنوعی و بدون اینترنت اجرا می‌شوند.")
        add.clicked.connect(lambda: self._add_macro_row("", "", "", "", "خیر"))
        with db() as con:
            for r in con.execute("SELECT * FROM voice_macro ORDER BY trigger"):
                self._add_macro_row(r["trigger"], r["customer"], r["kind"], r["items"], "بله" if r["proforma"] else "خیر")
    def _add_macro_row(self, *vals):
        t = self.macro_t; r = t.rowCount(); t.insertRow(r)
        for c, v in enumerate(vals): t.setItem(r, c, QTableWidgetItem(str(v or "")))
    def _build_notes_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        h = QLabel("هر چیزی که می‌خواهی هوش مصنوعی همیشه بداند یا رعایت کند را به زبان ساده بنویس (هر خط یک مورد). مثال:\n"
                   "• وقتی می‌گویم «روغن» منظورم روغن موتور ۴ لیتری است.\n• برای مشتری‌های شرکتی همیشه فاکتور اعتباری با سررسید ۳۰ روز بزن.\n"
                   "• قیمت‌ها را همیشه به تومان بگیر.")
        h.setWordWrap(True); l.addWidget(h)
        self.notes_e = QTextEdit(); self.notes_e.setPlainText(get_pref("ai_notes", "")); l.addWidget(self.notes_e)
        self.tabs.addTab(w, "آموزش به هوش مصنوعی")
    def _build_ai_tab(self):
        w = QWidget(); f = QFormLayout(w)
        self.ai_on = QCheckBox("استفاده از هوش مصنوعی (فهم جمله‌های آزاد، پرسیدن سؤال و ادامه‌ی گفتگو)"); self.ai_on.setChecked(get_pref("ai_enabled", "0") == "1")
        self.gem_key = QLineEdit(get_pref("gemini_key", "")); self.gem_key.setEchoMode(QLineEdit.Password); self.gem_key.setPlaceholderText("کلید Gemini")
        self.gem_model = QLineEdit(get_pref("gemini_model", "") or "gemini-2.5-flash")
        self.followup = QCheckBox("بعد از هر کار، چند ثانیه منتظر دستور بعدی بمان (بدون گفتن دوباره‌ی نام)"); self.followup.setChecked(get_pref("followup", "1") == "1")
        self.tts_on = QCheckBox("اگر ویندوز صدای فارسی دارد، سؤال‌ها را بلند بخوان"); self.tts_on.setChecked(get_pref("tts", "1") == "1")
        info_l = QLabel("کلید رایگان: aistudio.google.com ← Get API key. دسترسی و سهمیه‌ی رایگان به کشور و حساب شما بستگی دارد. "
                        "متن جمله‌هایی که می‌گویی برای فهمیدن به Gemini فرستاده می‌شود؛ فهرست مشتری‌ها و قیمت‌ها فرستاده نمی‌شود (تطبیق نام روی همین کامپیوتر انجام می‌شود).")
        info_l.setWordWrap(True)
        f.addRow(self.ai_on); f.addRow("کلید Gemini:", self.gem_key); f.addRow("مدل:", self.gem_model); f.addRow(self.followup); f.addRow(self.tts_on); f.addRow(info_l)
        self.tabs.addTab(w, "هوش مصنوعی")
    def _build_voice_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.status = QLabel(""); self.status.setWordWrap(True); l.addWidget(self.status)
        r1 = QHBoxLayout()
        b_vosk = QPushButton("نصب vosk"); b_fa = QPushButton("دانلود مدل فارسی (≈۵۳ مگابایت)"); b_spk = QPushButton("دانلود مدل تشخیص گوینده (≈۱۳ مگابایت)")
        for b in (b_vosk, b_fa, b_spk): r1.addWidget(b)
        l.addLayout(r1)
        b_vosk.clicked.connect(self._install_vosk); b_fa.clicked.connect(lambda: self._download("fa")); b_spk.clicked.connect(lambda: self._download("spk"))
        self.always = QCheckBox("گوش‌دادن همیشگی (با هر بار اجرای برنامه فعال می‌شود)"); self.always.setChecked(get_pref("always_on", "0") == "1"); l.addWidget(self.always)
        r2 = QHBoxLayout(); self.wake_e = QLineEdit(get_pref("wake_word", "دستیار")); r2.addWidget(QLabel("کلمه‌ی بیدارباش (چند کلمه با ، جدا کن):")); r2.addWidget(self.wake_e)
        b_chk = QPushButton("بررسی کلمه"); b_chk.clicked.connect(self._check_wake); r2.addWidget(b_chk); l.addLayout(r2)
        self.confirm_c = QCheckBox("بعد از شنیدن نام، تایید بخواه («بله» بگویم تا شروع کند)"); self.confirm_c.setChecked(get_pref("wake_confirm", "1") == "1"); l.addWidget(self.confirm_c)
        g1 = QGroupBox("قفل صدا (آزمایشی): فقط صدای خودم"); gl = QVBoxLayout(g1)
        self.spk_on = QCheckBox("فعال"); self.spk_on.setChecked(get_pref("spk_enabled", "0") == "1"); gl.addWidget(self.spk_on)
        rr = QHBoxLayout(); self.spk_thr = QDoubleSpinBox(); self.spk_thr.setRange(0.1, 1.2); self.spk_thr.setSingleStep(0.05); self.spk_thr.setDecimals(2)
        self.spk_thr.setValue(float(get_pref("spk_threshold", "0.5") or 0.5)); rr.addWidget(QLabel("آستانه (کمتر = سخت‌گیرتر):")); rr.addWidget(self.spk_thr)
        b_en = QPushButton("ثبت صدای من (۳ نمونه)"); b_te = QPushButton("تست صدا"); rr.addWidget(b_en); rr.addWidget(b_te); gl.addLayout(rr)
        self.spk_status = QLabel(""); self.spk_status.setWordWrap(True); gl.addWidget(self.spk_status)
        b_en.clicked.connect(self._enroll); b_te.clicked.connect(self._test_voice); l.addWidget(g1)
        g2 = QGroupBox("رمز گفتاری"); pl = QHBoxLayout(g2)
        self.pass_on = QCheckBox("بعد از تایید، رمز بخواه"); self.pass_on.setChecked(get_pref("pass_enabled", "0") == "1")
        self.pass_e = QLineEdit(); self.pass_e.setEchoMode(QLineEdit.Password); self.pass_e.setPlaceholderText("رمز جدید (خالی = بدون تغییر)")
        pl.addWidget(self.pass_on); pl.addWidget(self.pass_e); l.addWidget(g2)
        warn_l = QLabel("نکته: قفل صدا و رمز فقط یک فیلتر راحتی‌اند، نه امنیت واقعی؛ با صدای ضبط‌شده یا محیط پرسروصدا ممکن است خطا کنند. "
                        "تا قبل از شنیدن نام، هیچ صدایی ذخیره یا فرستاده نمی‌شود؛ بعد از آن فقط خودِ دستور برای تبدیل به متن به گوگل می‌رود.")
        warn_l.setWordWrap(True); l.addWidget(warn_l); l.addStretch()
        self.tabs.addTab(w, "گوش‌دادن همیشگی و قفل صدا")
    # ── status / tools ──
    def refresh_status(self):
        import assistant_ai as A
        try: import vosk; v = "نصب است ✓"
        except Exception: v = "نصب نیست ✗"
        base = str(APP_DIR / "models")
        fa = "دانلود شده ✓" if A.find_model_dir(base, "vosk-model-small-fa") else "دانلود نشده ✗"
        sp = "دانلود شده ✓" if A.find_model_dir(base, "vosk-model-spk") else "دانلود نشده ✗"
        vp = "ثبت شده ✓" if get_pref("voiceprint", "") else "ثبت نشده"
        self.status.setText(f"vosk: {v}   |   مدل فارسی: {fa}   |   مدل تشخیص گوینده: {sp}   |   صدای من: {vp}")
        if not hasattr(self, "_spk_set"): self.spk_status.setText(""); self._spk_set = True
    def _install_vosk(self):
        if getattr(sys, "frozen", False): return warn(self, "نصب vosk", "در نسخه‌ی exe نصب خودکار ممکن نیست؛ از نسخه‌ی پایتون استفاده کن.")
        self.status.setText("در حال نصب vosk (چند دقیقه صبر کن)…")
        import threading, subprocess
        def work():
            try:
                flags = 0x08000000 if os.name == "nt" else 0
                p = subprocess.run([sys.executable, "-m", "pip", "install", "vosk"], capture_output=True, text=True, creationflags=flags)
                self.dl.done.emit("" if p.returncode == 0 else (p.stderr or p.stdout)[-300:])
            except Exception as e: self.dl.done.emit(str(e))
        threading.Thread(target=work, daemon=True).start()
    def _download(self, kind):
        import threading, assistant_ai as A
        self.status.setText("در حال دانلود…")
        def work():
            try: A.download_and_extract(A.MODEL_URLS[kind], str(APP_DIR / "models"), lambda d, t: self.dl.progress.emit(d, t)); self.dl.done.emit("")
            except Exception as e: self.dl.done.emit(str(e))
        threading.Thread(target=work, daemon=True).start()
    def _on_progress(self, done, total):
        self.status.setText(f"در حال دانلود… {done // 1048576} از {total // 1048576 if total else '؟'} مگابایت")
    def _on_dl_done(self, err):
        VoskKit.reset(); self.refresh_status()
        if err: warn(self, "ناموفق", err)
    def _check_wake(self):
        import assistant_ai as A
        d = A.find_model_dir(str(APP_DIR / "models"), "vosk-model-small-fa")
        if not d: return warn(self, "بررسی کلمه", "ابتدا مدل فارسی را دانلود کن.")
        bad = []
        for wd in [x.strip() for x in re.split(r"[،,]", self.wake_e.text()) if x.strip()]:
            if A.wake_word_in_vocab(d, norm_key(wd)) is False: bad.append(wd)
        if bad: warn(self, "کلمه شناخته نمی‌شود", "این کلمه‌ها در واژگان مدل نیستند و شنیده نمی‌شوند: " + "، ".join(bad) + "\nیک کلمه‌ی رایج فارسی انتخاب کن (مثل «دستیار»، «هوشمند»، «فاکتور»).")
        else: info(self, "بررسی کلمه", "کلمه برای مدل شناخته‌شده است ✓")
    # ── voice print ──
    def _enroll(self):
        try: kit = VoskKit.get()
        except Exception as e: return warn(self, "قفل صدا", str(e))
        if kit.spk is None: return warn(self, "قفل صدا", "ابتدا «مدل تشخیص گوینده» را دانلود کن.")
        self._samples = []; self._enroll_step(1)
    def _enroll_step(self, i):
        lines = ["من می‌خواهم یک فاکتور فروش جدید برای مشتری ثبت کنم", "دستیار هوشمند لطفاً فاکتور بعدی را باز کن", "امروز کار زیادی داریم و باید همه چیز را سریع ثبت کنیم"]
        self.spk_status.setText(f"نمونه {i} از ۳ — بعد از چند لحظه ۵ ثانیه این جمله را بخوان: «{lines[i - 1]}»")
        QTimer.singleShot(2500, lambda: self._record_sample(i))
    def _record_sample(self, i):
        self.spk_status.setText(f"🔴 نمونه {i}: همین الان بخوان…")
        self._rec = PcmRecorder(self); self._rec.done.connect(lambda pcm, err, i=i: self._sample_done(i, pcm, err)); self._rec.start(5)
    def _sample_done(self, i, pcm, err):
        if err: self.spk_status.setText(""); return warn(self, "ضبط", err)
        import assistant_ai as A; kit = VoskKit.get()
        vec = A.speaker_vector(kit.model, kit.spk, pcm, kit.vosk.KaldiRecognizer)
        if vec: self._samples.append(vec)
        if i < 3: return self._enroll_step(i + 1)
        avg = A.average_vectors(self._samples)
        if not avg or len(self._samples) < 2: self.spk_status.setText("صدا کافی نبود؛ دوباره و بلندتر امتحان کن."); return
        set_pref("voiceprint", json.dumps(avg)); self.spk_status.setText("✅ صدای شما ثبت شد. با «تست صدا» آستانه را تنظیم کن."); self.refresh_status()
    def _test_voice(self):
        try:
            kit = VoskKit.get(); enrolled = json.loads(get_pref("voiceprint", "") or "null")
        except Exception as e: return warn(self, "تست صدا", str(e))
        if kit.spk is None or not enrolled: return warn(self, "تست صدا", "ابتدا مدل تشخیص گوینده را دانلود و صدایت را ثبت کن.")
        self.spk_status.setText("🔴 ۴ ثانیه حرف بزن…")
        self._rec = PcmRecorder(self); self._rec.done.connect(self._test_done); self._rec.start(4)
    def _test_done(self, pcm, err):
        if err: self.spk_status.setText(""); return warn(self, "ضبط", err)
        import assistant_ai as A; kit = VoskKit.get()
        vec = A.speaker_vector(kit.model, kit.spk, pcm, kit.vosk.KaldiRecognizer)
        if not vec: self.spk_status.setText("صدا خیلی کوتاه بود."); return
        dist = A.cosine_distance(vec, json.loads(get_pref("voiceprint", "")))
        self.spk_status.setText(f"فاصله‌ی صدا: {dist:.2f} — {'قبول ✓' if dist <= self.spk_thr.value() else 'رد ✗'} (آستانه {self.spk_thr.value():.2f}). "
                                "یک بار با صدای خودت و یک بار با صدای دیگری تست کن و آستانه را بین دو عدد بگذار.")
    # ── save ──
    def save_all(self):
        import assistant_ai as A
        with db() as con:
            con.execute("DELETE FROM voice_alias")
            for r in range(self.alias_t.rowCount()):
                kind = "customer" if self.alias_t.cellWidget(r, 0).currentText() == "مشتری" else "product"
                sp = (self.alias_t.item(r, 1).text() if self.alias_t.item(r, 1) else "").strip(); tg = (self.alias_t.item(r, 2).text() if self.alias_t.item(r, 2) else "").strip()
                if sp and tg: learn_alias(kind, sp, tg, con)
            con.execute("DELETE FROM voice_macro")
            for r in range(self.macro_t.rowCount()):
                v = [(self.macro_t.item(r, c).text() if self.macro_t.item(r, c) else "").strip() for c in range(5)]
                key = " ".join(_tok(v[0]))
                if key and (v[1] or v[3]):
                    con.execute("INSERT OR REPLACE INTO voice_macro(trigger_key,trigger,customer,kind,items,discount,proforma) VALUES(?,?,?,?,?,0,?)",
                                (key, v[0], v[1], v[2], v[3], 1 if v[4] == "بله" else 0))
        set_pref("ai_notes", self.notes_e.toPlainText().strip()[:3000])
        set_pref("ai_enabled", "1" if self.ai_on.isChecked() else "0"); set_pref("gemini_key", self.gem_key.text().strip())
        set_pref("gemini_model", self.gem_model.text().strip()); set_pref("followup", "1" if self.followup.isChecked() else "0"); set_pref("tts", "1" if self.tts_on.isChecked() else "0")
        set_pref("wake_word", self.wake_e.text().strip() or "دستیار"); set_pref("wake_confirm", "1" if self.confirm_c.isChecked() else "0")
        set_pref("spk_enabled", "1" if self.spk_on.isChecked() else "0"); set_pref("spk_threshold", f"{self.spk_thr.value():.2f}")
        if self.pass_e.text().strip():
            import secrets
            salt = secrets.token_hex(8); set_pref("pass_salt", salt); set_pref("pass_hash", A.phrase_hash(self.pass_e.text(), salt))
        set_pref("pass_enabled", "1" if (self.pass_on.isChecked() and get_pref("pass_hash", "")) else "0")
        set_pref("always_on", "1" if self.always.isChecked() else "0")
        self.win.va.stop_always_on()
        if self.always.isChecked(): self.win.va.start_always_on()
        self.accept()

class ProductDelegate(QStyledItemDelegate):
    """Name column editor with a suggestion popup fed by ProductMemory (best matches first)."""
    def __init__(self, parent, suggest_fn):
        super().__init__(parent); self.suggest_fn = suggest_fn
    def createEditor(self, parent, option, index):
        ed = QLineEdit(parent)
        model = QStringListModel(ed)
        comp = QCompleter(model, ed)
        comp.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setMaxVisibleItems(8)
        ed.setCompleter(comp)
        ed.textEdited.connect(lambda t: model.setStringList(self.suggest_fn(t)))
        return ed

UI_QSS = """
QGroupBox#card{background:#ffffff;border:1px solid #d3e0d8;border-radius:10px;margin-top:14px;padding:10px 8px 8px 8px;}
QGroupBox#card::title{subcontrol-origin:margin;subcontrol-position:top right;padding:0 8px;color:#1e563a;font-weight:bold;}
QPushButton#primary{background:#1e563a;color:white;border:none;border-radius:8px;padding:8px 18px;font-weight:bold;}
QPushButton#primary:hover{background:#26704a;}
QLabel#banner{background:#1e563a;color:white;font-size:16px;font-weight:bold;padding:12px 16px;}
"""

def card(title=""):
    f = QGroupBox(title); f.setObjectName("card"); QVBoxLayout(f); return f

def banner_label(title):
    b = QLabel(title); b.setObjectName("banner"); return b

def polish_dialog(dlg, title):
    """چیدمان یک‌دست پنجره‌ها: نوار سبز عنوان و دکمه‌های «ثبت» / «لغو»."""
    lay = dlg.layout()
    if isinstance(lay, QVBoxLayout): lay.insertWidget(0, banner_label(title))
    dlg.setStyleSheet(UI_QSS)
    for bb in dlg.findChildren(QDialogButtonBox):
        for std, text, prim in ((QDialogButtonBox.Save, "✔ ثبت", True), (QDialogButtonBox.Ok, "✔ تأیید", True), (QDialogButtonBox.Cancel, "✕ لغو", False)):
            b = bb.button(std)
            if b: b.setText(text); b.setObjectName("primary") if prim else None

def _pair(a, b):
    """دو فیلد کنار هم در یک ردیف فرم (مثلاً تاریخ و سررسید)."""
    w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.addWidget(a, 1); l.addWidget(b, 1); return w

def make_searchable_combo(combo, placeholder="تایپ کن تا مشتری پیدا شود..."):
    """کمبوی قابل‌جست‌وجو: با تایپ هر بخش از نام (به هر ترتیب) فهرست فیلتر می‌شود.
    اگر متن تایپ‌شده دقیقاً یک مشتری را مشخص کند همان انتخاب می‌شود، وگرنه به انتخاب قبلی برمی‌گردد (انتخاب اشتباه بی‌صدا نمی‌شود)."""
    combo.setEditable(True); combo.setInsertPolicy(QComboBox.NoInsert)
    le = combo.lineEdit(); le.setPlaceholderText(placeholder)
    model = QStringListModel(combo)
    comp = QCompleter(model, combo); comp.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
    comp.setCaseSensitivity(Qt.CaseInsensitive); comp.setMaxVisibleItems(10); combo.setCompleter(comp)
    names = lambda: [combo.itemText(i) for i in range(combo.count())]
    le.textEdited.connect(lambda t: model.setStringList([n for n in names() if match_words(t, n)][:60]))
    def pick(text):
        i = combo.findText(text)
        if i >= 0: combo.setCurrentIndex(i)
    comp.activated[str].connect(pick)
    def snap():
        t = le.text().strip(); cur = combo.currentIndex()
        if t and t != combo.itemText(cur):
            exact = combo.findText(t)
            hits = [i for i in range(combo.count()) if match_words(t, combo.itemText(i))]
            if exact >= 0: combo.setCurrentIndex(exact)
            elif len(hits) == 1: combo.setCurrentIndex(hits[0])
        le.setText(combo.itemText(combo.currentIndex()))
    le.editingFinished.connect(snap)
    return combo

def bbox(flags):
    b = QDialogButtonBox(flags)
    for std, t in ((QDialogButtonBox.Save, "ذخیره"), (QDialogButtonBox.Cancel, "انصراف"), (QDialogButtonBox.Close, "بستن")):
        x = b.button(std)
        if x: x.setText(t)
    return b

def money_spin():
    s = QDoubleSpinBox(); s.setMaximum(1e12); s.setDecimals(0); s.setGroupSeparatorShown(True); return s

# ───────────────────────────── Dialogs ─────────────────────────────
class CustomerDialog(QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent); self.setWindowTitle("اطلاعات مشتری"); self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.name = QLineEdit(data["name"] if data else ""); self.phone = QLineEdit(data["phone"] if data else "")
        self.address = QLineEdit(data["address"] if data else "")
        self.notes = QTextEdit(data["notes"] if data else ""); self.notes.setMaximumHeight(75)
        form.addRow("نام مشتری / فروشگاه *", self.name); form.addRow("تلفن", self.phone)
        form.addRow("آدرس", self.address); form.addRow("توضیحات", self.notes)
        b = bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); form.addRow(b)
        install_voice(self, parent, "customer")
    def values(self):
        return self.name.text().strip(), self.phone.text().strip(), self.address.text().strip(), self.notes.toPlainText().strip()
    def accept(self):
        if not self.name.text().strip(): warn(self, "خطا", "نام مشتری الزامی است."); return
        super().accept()

def _doc_fp(con, iid):
    """اثر انگشت سند (سرفاکتور + اقلام) برای تشخیص ویرایش هم‌زمان توسط کاربر دیگر."""
    import hashlib
    a = [tuple(r) for r in con.execute("SELECT * FROM invoices WHERE id=?", (iid,))]
    b = [tuple(r) for r in con.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY row_no", (iid,))]
    return hashlib.md5(repr((a, b)).encode("utf-8")).hexdigest()

def _next_free_no(con, pf, old):
    key = "next_proforma" if pf else "next_invoice"
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone(); n = int(r[0]) if r and str(r[0]).isdigit() else 1
    pre = old[0] if pf and old and not old[0].isdigit() else ""
    while con.execute("SELECT 1 FROM invoices WHERE invoice_no=?", (pre + str(n),)).fetchone(): n += 1
    return pre + str(n)

class _BarcodeSniffer(QObject):
    """تشخیص بارکدخوان: بارکدخوان کاراکترها را پشت‌سرهم و خیلی سریع تایپ می‌کند (معمولاً با Enter در انتها).
    هر جای پنجره‌ی فاکتور/پیش‌فاکتور که فوکوس باشد (حتی داخل سلول جدول یا کادر جست‌وجو) این الگو شناسایی می‌شود،
    متن تایپ‌شده از همان کادر پاک می‌شود و کالا اضافه می‌شود. اگر بارکدخوان Enter نفرستد، بعد از مکث کوتاه هم تشخیص داده می‌شود.
    تشخیص بر اساس «میانگین» سرعت است، پس تأخیر لحظه‌ای برنامه (باز شدن ویرایشگر سلول) آن را خراب نمی‌کند."""
    CONT_GAP = 0.25      # بیشتر از این مکث = دنباله‌ی جدید
    IDLE_MS = 160        # مکث بعد از آخرین کاراکتر برای بارکدخوان‌های بدون Enter
    IDLE_MIN = 8         # حداقل طول برای تشخیص بدون Enter (EAN-8 و بلندتر)

    def __init__(self, dlg):
        super().__init__(dlg); self.dlg = dlg; self.buf = ""; self.t0 = 0.0; self.t1 = 0.0; self.pre = ""; self.pre_val = None; self.snap = None
        self.idle = QTimer(self); self.idle.setSingleShot(True); self.idle.setInterval(self.IDLE_MS); self.idle.timeout.connect(self._idle)

    @staticmethod
    def _line_edit(w):
        if isinstance(w, QLineEdit): return w
        le = getattr(w, "lineEdit", None)
        try: return le() if callable(le) else None
        except Exception: return None

    @staticmethod
    def _spin_of(w):
        p = w.parent() if w is not None else None
        return p if isinstance(p, (QSpinBox, QDoubleSpinBox)) else (w if isinstance(w, (QSpinBox, QDoubleSpinBox)) else None)

    def _reset(self): self.buf = ""; self.idle.stop()

    def _capture_pre(self):
        d = self.dlg; fw = QApplication.focusWidget(); le = self._line_edit(fw); sp = self._spin_of(fw)
        self.pre_val = sp.value() if sp is not None else None
        self.pre = le.text() if le is not None else ""
        try: self.snap = d._items_snapshot()
        except Exception: self.snap = None

    def _table_editor(self, fw):
        """ویرایشگر باز سلول جدول (اگر فوکوس داخل آن است)."""
        d = self.dlg; vp = d.items.viewport(); w = fw
        if w is None or w is d.items or w is vp or not d.items.isAncestorOf(w): return None
        while w is not None and w.parent() is not vp: w = w.parent()
        return w

    def _consume(self):
        """ارقام بارکد را به هر شکلی که تایپ شده باشند (سلول جدول، کادر، مبلغ) خنثی می‌کند و سپس کالا را اضافه می‌کند:
        ویرایشگر سلول بدون ثبت بسته می‌شود و کل جدول به وضعیت قبل از اسکن برمی‌گردد."""
        d = self.dlg; code = self.buf; self._reset()
        fw = QApplication.focusWidget(); ed = self._table_editor(fw)
        try:
            if ed is not None:
                from PySide6.QtWidgets import QAbstractItemDelegate
                d.items.closeEditor(ed, QAbstractItemDelegate.RevertModelCache)
                d.items.setFocus()
            else:
                sp = self._spin_of(fw); le = self._line_edit(fw)
                if sp is not None and self.pre_val is not None: sp.setValue(self.pre_val)
                elif le is not None: le.setText(self.pre)
            if self.snap is not None: d._items_restore(self.snap)
        except RuntimeError: pass
        QTimer.singleShot(0, lambda c=code: d._barcode_scanned(c))

    def _idle(self):
        if barcodes.is_scan(len(self.buf), self.t0, self.t1) and len(self.buf) >= self.IDLE_MIN: self._consume()
        else: self._reset()

    def eventFilter(self, obj, ev):
        try:
            if ev.type() != QEvent.KeyPress or not ev.spontaneous(): return False
            d = self.dlg
            if not d.isVisible(): return False
            if QApplication.activePopupWidget() is None and not d.isActiveWindow(): return False      # با باز بودن لیست پیشنهاد کالا هم کار می‌کند
            aw = QApplication.activeModalWidget()
            if aw is not None and aw is not d: return False
            now = _time.monotonic(); key = ev.key(); txt = barcodes.norm(ev.text())
            if len(txt) == 1 and (txt.isalnum() or txt in "-./"):
                if not self.buf or now - self.t1 > self.CONT_GAP:
                    self.buf = ""; self.t0 = now; self._capture_pre()
                self.buf += txt; self.t1 = now; self.idle.start(); return False
            if key in (Qt.Key_Return, Qt.Key_Enter) and self.buf:
                if barcodes.is_scan(len(self.buf), self.t0, self.t1) and now - self.t1 <= self.CONT_GAP:
                    self._consume(); return True
                self._reset(); return False
            if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_CapsLock): self._reset()
        except RuntimeError:
            pass
        return False


class StartDocDialog(QDialog):
    """پنجره‌ی کوچکِ شروع: قبل از باز شدن فاکتور/پیش‌فاکتور جدید، مشتری و نوع پرداخت انتخاب می‌شود تا فراموش نشود."""
    def __init__(self, parent, pf=False):
        super().__init__(parent); self.pf = pf; self.setWindowTitle("پیش‌فاکتور جدید" if pf else "فاکتور جدید"); self.setMinimumWidth(480); self.setStyleSheet(UI_QSS)
        f = QFormLayout(self)
        self.customer = QComboBox(); self.customer.setMinimumWidth(240)
        with db() as con:
            for c in con.execute("SELECT id,name FROM customers ORDER BY name"): self.customer.addItem(c["name"], c["id"])
        make_searchable_combo(self.customer)
        want = getattr(parent, "_voice_customer", None) or get_pref("default_customer", "")
        if str(want).isdigit(): self.customer.setCurrentIndex(max(0, self.customer.findData(int(want))))
        nb = QPushButton("＋ مشتری جدید"); nb.setAutoDefault(False); nb.clicked.connect(lambda: InvoiceDialog._add_customer(self))
        row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0); rl.addWidget(self.customer, 1); rl.addWidget(nb)
        f.addRow("مشتری", row)
        self.kind = QComboBox(); self.kind.addItems(["اعتباری", "چکی", "نقدی", "ترکیبی"]); self.kind.setCurrentIndex(max(0, self.kind.findText(get_pref("default_kind", "نقدی"))))
        if not pf: f.addRow("نوع پرداخت", self.kind)
        b = bbox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); b.accepted.connect(self.accept); b.rejected.connect(self.reject); f.addRow(b)
        (self.customer if pf else self.kind).setFocus()
    def accept(self):
        if self.customer.currentData() is None: return warn(self, "مشتری", "مشتری را انتخاب کن.")
        super().accept()
    def result_values(self): return (self.customer.currentData(), None if self.pf else self.kind.currentText())


class InvoiceDialog(QDialog):
    """mode: 'invoice' | 'proforma' | 'convert' (turn a proforma into a real invoice)."""
    def _add_customer(self):
        """افزودن مشتری جدید از داخل فاکتور/پیش‌فاکتور و انتخاب خودکار او."""
        d = CustomerDialog(self)
        if not d.exec(): return
        name, phone, address, notes = d.values()
        with db() as con:
            same = [r["id"] for r in con.execute("SELECT id,name FROM customers") if fold_text(r["name"]) == fold_text(name)]
            if same:
                cid = same[0]; warn(self, "مشتری تکراری", "مشتری‌ای با همین نام از قبل هست؛ همان انتخاب شد.")
            else:
                cid = con.execute("INSERT INTO customers(name,phone,address,notes,created_at) VALUES(?,?,?,?,?)",
                                  (name, phone, address, notes, datetime.now().isoformat(timespec="seconds"))).lastrowid
            rows = con.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
        self.customer.blockSignals(True); self.customer.clear()
        for c in rows: self.customer.addItem(c["name"], c["id"])
        self.customer.blockSignals(False)
        self.customer.setCurrentIndex(max(0, self.customer.findData(cid)))
        p = self.parent()
        if p is not None and hasattr(p, "refresh_all"): p.refresh_all()
    def __init__(self, parent=None, invoice_id=None, mode="invoice", start=None):
        super().__init__(parent); self.mode = mode; self.pf = mode == "proforma"
        self.setWindowTitle({"invoice": "فاکتور جدید" if invoice_id is None else "ویرایش فاکتور",
                             "proforma": "پیش‌فاکتور جدید" if invoice_id is None else "ویرایش پیش‌فاکتور",
                             "convert": "نهایی کردن پیش‌فاکتور (تبدیل به فاکتور)"}[mode])
        self.resize(1250, 850); self.setMinimumSize(860, 520); self.invoice_id = invoice_id; self.saved_id = None; self.print_after = False; self.final_no = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 10); root.setSpacing(8); self.setStyleSheet(UI_QSS)
        self.customer = QComboBox(); self.customer.setMinimumWidth(200)
        with db() as con:
            for c in con.execute("SELECT id,name FROM customers ORDER BY name"): self.customer.addItem(c["name"], c["id"])
        make_searchable_combo(self.customer)
        self.btn_new_cust = QPushButton("＋ مشتری جدید"); self.btn_new_cust.setObjectName("primary"); self.btn_new_cust.setAutoDefault(False); self.btn_new_cust.setDefault(False)
        self.btn_new_cust.clicked.connect(self._add_customer)
        cust_row = QWidget(); _cl = QHBoxLayout(cust_row); _cl.setContentsMargins(0, 0, 0, 0); _cl.addWidget(self.customer, 1); _cl.addWidget(self.btn_new_cust)
        self.cust_bal = QLabel(""); self.cust_bal.setStyleSheet("font-weight:bold;"); self.cust_bal.setWordWrap(True)
        self.no = QLineEdit(); self.no.setReadOnly(True)
        self.inv_date = JalaliDateEdit()
        self.kind = QComboBox(); self.kind.addItems(["اعتباری", "چکی", "نقدی", "ترکیبی"])
        self.due = JalaliDateEdit((date.today() + timedelta(days=30)).isoformat(), allow_empty=True)
        self.kind.currentTextChanged.connect(lambda t: self.due.setEnabled(t in CREDIT_KINDS))
        self._loading = False; self._prev_kind = self.kind.currentText()
        self.kind.currentTextChanged.connect(self._kind_changed)
        self.description = QLineEdit(); self.description.setPlaceholderText("شرح کلی (اختیاری)")
        self.discount = money_spin(); self.initial_paid = money_spin()
        self.discount.valueChanged.connect(lambda _v: self._cash_kind_changed(self.kind.currentText()))
        self.paid_row = with_words(self.initial_paid); discount_row = with_words(self.discount)
        self.customer.currentIndexChanged.connect(self._show_balance)
        # ── جدول اقلام: ردیف | نام | شرح | تعداد | قیمت | تخفیف | جمع | حذف ──
        self.items = QTableWidget(0, 7); self.items.setHorizontalHeaderLabels(["نام کالا", "شرح کالا", "تعداد", "قیمت واحد (تومان)", "جمع کل (تومان)", "تخفیف (تومان)", "حذف"])
        hh = self.items.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch); hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (2, 3, 4, 5): hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.Fixed); self.items.setColumnWidth(6, 46); hh.moveSection(5, 4)      # تخفیف کنار جمع کل
        self.items.setAlternatingRowColors(True); self.items.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.items.setMinimumHeight(170); self.items.verticalHeader().setDefaultSectionSize(34)
        self.items.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.items.setTabKeyNavigation(True)
        self.items.installEventFilter(self)
        self.words = FadeLabel(hold_ms=3000); self.words.setStyleSheet("color:#1e563a;font-weight:bold;font-size:15px;background:transparent;")
        self.words.setMinimumHeight(30)
        self.items.setItemDelegateForColumn(3, PriceDelegate(self.items, self._price_typed))
        self.pm = ProductMemory()
        self.items.setItemDelegateForColumn(0, ProductDelegate(self.items, lambda t: self.pm.suggest(t, self.customer.currentData())))
        # ── بارکدخوان هوشمند: اسکن در هر نقطه‌ی پنجره شناخته می‌شود (بدون کادر جداگانه) ──
        self._sniffer = _BarcodeSniffer(self); QApplication.instance().installEventFilter(self._sniffer)
        self.finished.connect(lambda *_: QApplication.instance().removeEventFilter(self._sniffer))
        row = QHBoxLayout()
        if VOICE_ASSISTANT:
            amic = QPushButton(); self.assistant = Dictation(self, amic, self._assistant, label="🎙 دستیار هوشمند (F9)")
            amic.setToolTip(ASSISTANT_TIP_INVOICE); row.addWidget(amic)
            _f9 = QShortcut(QKeySequence("F9"), self); _f9.activated.connect(self.assistant.toggle)
        row.addStretch()
        self.voice_msg = QLabel(""); self.voice_msg.setWordWrap(True); self.voice_msg.setStyleSheet("color:#355c7d;")
        self.notes = QLineEdit(); self.notes.setPlaceholderText("یادداشت یا توضیحات فاکتور…")
        # ── چیدمان نهایی (راست به چپ): ستون وضعیت و خلاصه | مشخصات و مشتری و اقلام ──
        root.addWidget(banner_label(self.windowTitle()))
        bot = QHBoxLayout(); bot.setContentsMargins(12, 0, 12, 0); root.addLayout(bot)      # دکمه‌های ثبت / پیش‌نمایش / لغو: بالای پنجره، همیشه دیده می‌شوند
        body = QHBoxLayout(); body.setContentsMargins(12, 2, 12, 0); body.setSpacing(12); root.addLayout(body, 1)
        side = QVBoxLayout(); side.setSpacing(10)
        self._kind_card = card("نوع پرداخت"); self._kind_card.layout().addWidget(self.kind)
        self._due_card = card("تاریخ سررسید (شمسی)"); self._due_card.layout().addWidget(self.due)
        sm = card("خلاصه فاکتور"); g = QGridLayout(); g.setVerticalSpacing(8); sm.layout().addLayout(g)
        self.lb_sub = QLabel("0"); self.lb_disc = QLabel("0")
        self.total_label = QLabel("0"); self.total_label.setStyleSheet("background:#1e563a;color:white;font-size:18px;font-weight:bold;padding:10px;border-radius:8px;")
        self._paid_lbl = QLabel("پرداخت اولیه (تومان)")
        for i, (t, w) in enumerate((("جمع کالاها", self.lb_sub), ("تخفیف کل", self.lb_disc), ("تخفیف اضافه‌ی فاکتور", discount_row),
                                    ("مبلغ نهایی (تومان)", self.total_label), (None, self.paid_row), ("مانده حساب مشتری", self.cust_bal))):
            if t is None: g.addWidget(self._paid_lbl, i, 0)
            else: g.addWidget(QLabel(t), i, 0)
            g.addWidget(w, i, 1)
        side.addWidget(self._kind_card); side.addWidget(self._due_card); side.addWidget(sm); side.addStretch()
        side_w = QWidget(); side_w.setLayout(side)
        side_sc = QScrollArea(); side_sc.setWidgetResizable(True); side_sc.setFrameShape(QScrollArea.NoFrame); side_sc.setWidget(side_w)
        side_sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); side_sc.setFixedWidth(362); body.addWidget(side_sc)
        if self.pf:
            for w in (self._kind_card, self._due_card, self.paid_row, self._paid_lbl): w.setVisible(False)
        main = QVBoxLayout(); main.setSpacing(8); body.addLayout(main, 1)
        top = QHBoxLayout(); top.setSpacing(10)
        info = card("مشخصات فاکتور"); ig = QGridLayout(); info.layout().addLayout(ig)
        ig.addWidget(QLabel("شماره فاکتور"), 0, 0); ig.addWidget(self.no, 1, 0); ig.addWidget(QLabel("تاریخ فاکتور"), 0, 1); ig.addWidget(self.inv_date, 1, 1)
        ig.addWidget(self.description, 2, 0, 1, 2)
        cc = card("مشتری"); cc.layout().addWidget(cust_row)
        top.addWidget(info, 1); top.addWidget(cc, 1); main.addLayout(top)
        main.addWidget(self.items, 1); main.addWidget(self.words); main.addLayout(row)
        nb = card("توضیحات"); nb.layout().addWidget(self.notes); main.addWidget(nb); main.addWidget(self.voice_msg)
        ok = QPushButton("✔ " + ("ثبت پیش‌فاکتور" if self.pf else ("نهایی کردن و ثبت فاکتور" if mode == "convert" else "ثبت فاکتور"))); ok.setObjectName("primary")
        cancel = QPushButton("✕ لغو"); pv = QPushButton("👁 ذخیره و پیش‌نمایش چاپ")
        for bt in (ok, cancel, pv): bt.setAutoDefault(False); bt.setDefault(False)
        ok.clicked.connect(lambda *_: self.save_validate()); cancel.clicked.connect(self.reject); pv.clicked.connect(lambda *_: self.save_validate(print_after=True))
        bot.addWidget(ok); bot.addWidget(pv); bot.addWidget(cancel); bot.addStretch()
        self.items.itemChanged.connect(self._on_item_changed)
        self.items.itemChanged.connect(self.recalc); self.discount.valueChanged.connect(self.recalc)
        for _ in range(3): self.add_item_row()
        with db() as con:
            nxt_inv = con.execute("SELECT value FROM settings WHERE key='next_invoice'").fetchone()[0]
            nxt_pf = con.execute("SELECT value FROM settings WHERE key='next_proforma'").fetchone()[0]
        self.no.setText(f"P{nxt_pf}" if self.pf else nxt_inv)
        if invoice_id is not None: self.load_invoice(nxt_inv)
        else:
            dc = get_pref("default_customer", "")
            if dc.isdigit():
                ix = self.customer.findData(int(dc))
                if ix >= 0: self.customer.setCurrentIndex(ix)
            if not self.pf:
                ix = self.kind.findText(get_pref("default_kind", "نقدی"))
                if ix >= 0: self.kind.setCurrentIndex(ix)
            vc = getattr(parent, "_voice_customer", None)
            if vc:
                ix = self.customer.findData(vc)
                if ix >= 0: self.customer.setCurrentIndex(ix)
            if start:                                   # انتخاب‌های پنجره‌ی شروع (مشتری و نوع پرداخت)
                ix = self.customer.findData(start[0])
                if ix >= 0: self.customer.setCurrentIndex(ix)
                if not self.pf and start[1]:
                    ix = self.kind.findText(start[1])
                    if ix >= 0: self.kind.setCurrentIndex(ix)
                parent._voice_customer = None
            pre = getattr(parent, "_voice_prefill", None)
            if pre:
                parent._voice_prefill = None
                if pre.get("customer_id"):
                    ix = self.customer.findData(pre["customer_id"])
                    if ix >= 0: self.customer.setCurrentIndex(ix)
                if pre.get("kind") and not self.pf:
                    ix = self.kind.findText(pre["kind"])
                    if ix >= 0: self.kind.setCurrentIndex(ix)
                for it in pre.get("items") or []:
                    nm = str(it.get("name") or "").strip()
                    if not nm: continue
                    pr = it.get("unit_price"); pr = float(pr) if pr not in (None, "") else None
                    self._add_voice_item(nm, float(it.get("quantity") or 1), pr, False, str(it.get("description") or ""))
                if pre.get("discount"): self.discount.setValue(float(pre["discount"]))
                if pre.get("due_days"): self.due.setIso((date.today() + timedelta(days=int(pre["due_days"]))).isoformat())
        self.recalc(); self._show_balance()
    def _show_balance(self, *_):
        """مانده‌ی کل مشتری انتخاب‌شده (طبق فاکتورها، دریافت‌ها، تخفیف‌ها و مرجوعی‌های ثبت‌شده)."""
        cid = self.customer.currentData()
        if cid is None: self.cust_bal.setText(""); return
        try:
            with db() as con:
                f = customer_figures(con).get(cid)
                inc = self.invoice_id is not None and self.mode == "invoice" and con.execute("SELECT 1 FROM invoices WHERE id=? AND customer_id=?", (self.invoice_id, cid)).fetchone()
        except Exception:
            return
        if not f: self.cust_bal.setText(""); return
        b = f["balance"]
        if abs(b) < 0.5: txt, col = "تسویه (بدون بدهی)", ""
        elif b > 0: txt, col = f"{money(b)} تومان بدهکار", "#c62828"
        else: txt, col = f"{money(-b)} تومان بستانکار", "#1565c0"
        note = "  (شامل همین فاکتور)" if inc else "  (بدون احتساب فاکتور در حال ثبت)"
        self.cust_bal.setText(txt + note); self.cust_bal.setStyleSheet("font-weight:bold;" + (f"color:{col};" if col else ""))
    def eventFilter(self, obj, event):
        if obj is self.items and event.type() == event.Type.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            r, c = self.items.currentRow(), self.items.currentColumn()
            if r < 0:
                self.items.setCurrentCell(0, 0); r, c = 0, 0
            if r >= self.items.rowCount() - 1:
                self.add_item_row()
            nr = min(r + 1, self.items.rowCount() - 1)
            self.items.setCurrentCell(nr, c)
            self.items.scrollToItem(self.items.currentItem(), QAbstractItemView.PositionAtCenter)
            self.items.editItem(self.items.currentItem())
            return True
        return super().eventFilter(obj, event)
    def _vmsg(self, text): self.voice_msg.setText("🎙 " + text)
    def _assistant(self, text):
        va = getattr(self.parent(), "va", None)
        if va is not None and not va.busy and va.ai_enabled(): return va.handle_text(text, DialogHost(self))
        self._assistant_offline(text)
    def _add_voice_item(self, spoken, qty, price, guessed=False, desc="", resolve=True, raw=None):
        """Adds a row from speech: taught aliases / product memory give the name, description and (if not spoken) the price.
        resolve=False uses `spoken` exactly as the final name (guided entry already decided); raw = what was really said."""
        cid = self.customer.currentData()
        name, matched = resolve_product(self.pm, spoken, cid) if resolve else (spoken, False)
        mem = self.pm.lookup(name, cid); from_mem = False
        if not desc and mem: desc = mem["description"]
        if price is None and mem and mem["price"] > 0: price = mem["price"]; from_mem = True
        r = next((i for i in range(self.items.rowCount()) if not (self.items.item(i, 0).text().strip() if self.items.item(i, 0) else "")), None)
        if r is None: self.add_item_row(); r = self.items.rowCount() - 1
        self.items.blockSignals(True)
        for c, val in enumerate([name, desc, fmt_qty(qty), fmt_qty(price or 0), ""]): self.items.setItem(r, c, table_item(val))
        self.items.blockSignals(False)
        self.items.item(r, 0).setData(Qt.UserRole, raw or spoken)
        if from_mem or guessed: self.items.item(r, 3).setBackground(QColor("#fff4c2"))
        self.recalc(); self.items.setCurrentCell(r, 0)
        return r, name, price, from_mem
    def _set_row_price(self, r, price):
        it = table_item(fmt_qty(price)); it.setBackground(QColor("#fff4c2")); self.items.setItem(r, 3, it); self.recalc()
    def _assistant_offline(self, text):
        """Smart voice command inside the invoice: add rows, discount, payment kind, customer, due date, save."""
        cmd = parse_invoice_command(text); act = cmd["action"]
        if act == "entry_mode":
            va = getattr(self.parent(), "va", None)
            if va is not None: return va.start_entry(DialogHost(self))
        if act == "save": return self.save_validate(False)
        if act == "save_print": return self.save_validate(True)
        if act == "item":
            it = cmd["item"]; note = ""
            r, name, price, from_mem = self._add_voice_item(it["name"], it["qty"], it["price"], it["guessed"])
            if from_mem: note = " (قیمت از حافظه)"
            if it["guessed"]: note = " (قیمت را به هزار تومان فرض کردم)"
            tail = f"{money(price)} تومان{note}" if price else "قیمت گفته نشد؛ ۰ گذاشتم"
            return self._vmsg(f"«{text}» ← ردیف {r + 1}: {fmt_qty(it['qty'])} × {name} — {tail}")
        if act == "discount":
            self.discount.setValue(cmd["value"]); return self._vmsg(f"تخفیف: {money(cmd['value'])} تومان")
        if act == "kind" and not self.pf:
            ix = self.kind.findText(cmd["value"])
            if ix >= 0: self.kind.setCurrentIndex(ix)
            return self._vmsg(f"نوع فاکتور: {cmd['value']}")
        if act == "due_days":
            self.due.setIso((date.today() + timedelta(days=cmd["days"])).isoformat())
            return self._vmsg(f"سررسید: {cmd['days']} روز دیگر" + ("" if self.due.isEnabled() else " (فاکتور نقدی است؛ سررسید اعمال نمی‌شود)"))
        if act == "delete_last":
            n = self.items.rowCount()
            if n: self.items.removeRow(n - 1); self.recalc(); return self._vmsg("آخرین ردیف حذف شد.")
            return self._vmsg("ردیفی برای حذف نیست.")
        if act == "customer":
            customers = [(self.customer.itemData(i), self.customer.itemText(i)) for i in range(self.customer.count())]
            res = resolve_customer(cmd["customer"], customers)
            if res["status"] == "ok": pick = res["id"]
            elif res["status"] == "not_found": return self._vmsg(f"مشتری «{cmd['customer']}» پیدا نشد.")
            else:
                close = res["candidates"]
                choice, ok = QInputDialog.getItem(self, "کدام مشتری؟", f"شنیدم: «{cmd['customer']}»", [n for _, n in close], 0, False)
                if not ok: return
                pick = next(c for c, n in close if n == choice); learn_alias("customer", cmd["customer"], choice)
            self.customer.setCurrentIndex(self.customer.findData(pick)); return self._vmsg(f"مشتری: {self.customer.currentText()}")
        self._vmsg(f"متوجه نشدم: «{text}». مثال: «سه عدد لنت ترمز قیمت هشتصد و پنجاه هزار» یا «ذخیره کن» (راهنما: نگه داشتن موس روی دکمه).")
    def _dictated(self, text):
        """Put the recognised text into the selected cell (name / description / number)."""
        r, c = self.items.currentRow(), self.items.currentColumn()
        if r < 0:
            r = self.items.rowCount() - 1
            if r < 0 or (self.items.item(r, 0) and self.items.item(r, 0).text().strip()):
                self.add_item_row(); r = self.items.rowCount() - 1
            c = 0
        if c in (2, 3, 5):
            num = re.sub(r"[^0-9.]", "", norm_digits(text).replace("٫", ".").replace(",", "").replace("،", ""))
            if not num: return warn(self, "تایپ با صدا", "عدد تشخیص داده نشد؛ برای عدد از کیبورد استفاده کن.")
            text = num
        elif c == 4: return
        old = self.items.item(r, c); cur = old.text().strip() if old else ""
        self.items.setItem(r, c, table_item((cur + " " + text).strip() if c == 1 else text))
        self.items.setCurrentCell(r, min(c + 1, 3))
    def _on_item_changed(self, item):
        if item is not None and item.column() == 0: self._autofill(item.row())
    def _autofill(self, r):
        """After a known product name is entered: fill description and the last price (this customer's price first)."""
        name_it = self.items.item(r, 0); name = name_it.text().strip() if name_it else ""
        info_ = self.pm.lookup(name, self.customer.currentData()) if name else None
        if not info_: return
        self.items.blockSignals(True)
        try:
            d_it = self.items.item(r, 1)
            if info_["description"] and (d_it is None or not d_it.text().strip()):
                self.items.setItem(r, 1, table_item(info_["description"]))
            p_it = self.items.item(r, 3)
            try: cur = parse_num(p_it.text()) if p_it and p_it.text().strip() else 0.0
            except Exception: cur = 0.0
            if info_["price"] > 0 and cur == 0:
                it = table_item(fmt_qty(info_["price"])); it.setBackground(QColor("#fff4c2")); self.items.setItem(r, 3, it)
        finally:
            self.items.blockSignals(False)
        self.recalc()
    def _price_typed(self, t):
        try: self.words.show_words(parse_num(t), prefix="قیمت واحد: ")
        except Exception: self.words.show_words(0)
    def add_item_row(self, values=None):
        r = self.items.rowCount(); self.items.blockSignals(True); self.items.insertRow(r)
        for c, val in enumerate(values or ("", "", "1", "0", "", "0")): self.items.setItem(r, c, table_item(val))
        self.items.blockSignals(False)
        btn = QPushButton("🗑"); btn.setFlat(True); btn.setStyleSheet("color:#c62828;border:none;font-size:15px;"); btn.setAutoDefault(False)
        btn.clicked.connect(lambda *_, b=btn: self._del_by_button(b)); self.items.setCellWidget(r, 6, btn)
    def _del_by_button(self, b):
        for r in range(self.items.rowCount()):
            if self.items.cellWidget(r, 6) is b: self.items.removeRow(r); self.recalc(); return
    # ── بارکد ──
    def _define_barcode(self, code):
        with db() as con: names = barcodes.known_names(con)
        names = sorted(set(names) | {r["name"] for r in self.pm.rows if r.get("name")})
        name, ok = QInputDialog.getItem(self, "تعریف بارکد جدید", f"بارکد «{code}» هنوز برای هیچ کالایی ثبت نشده.\nنام کالا را انتخاب کن یا تایپ کن؛ از این به بعد با اسکن همین بارکد، این کالا اضافه می‌شود:", [""] + names, 0, True)
        name = (name or "").strip()
        if not ok or not name: return None
        with db() as con: barcodes.define(con, code, name, "")
        return name
    def _items_snapshot(self):
        return [[(self.items.item(r, c).text() if self.items.item(r, c) is not None else "") for c in range(6)] for r in range(self.items.rowCount())]
    def _items_restore(self, snap):
        """فقط سلول‌هایی که تغییر کرده‌اند به متن قبلی برمی‌گردند (ظاهر سلول‌های دست‌نخورده حفظ می‌شود)."""
        self.items.blockSignals(True)
        try:
            for r, row in enumerate(snap[:self.items.rowCount()]):
                for c, t in enumerate(row):
                    it = self.items.item(r, c)
                    if it is None:
                        if t: self.items.setItem(r, c, table_item(t))
                    elif it.text() != t: it.setText(t)
        finally:
            self.items.blockSignals(False)
        self.recalc()
    def _price_for(self, name):
        """قیمت کالا: قیمت قبلی همین مشتری، وگرنه آخرین قیمت فروش (از حافظه‌ی کالا یا از فاکتورهای ثبت‌شده). خروجی: (قیمت، شرح)."""
        cid = self.customer.currentData(); info_ = self.pm.lookup(name, cid)
        if info_ and info_["price"] > 0: return info_["price"], info_["description"] or ""
        try:
            with db() as con:
                r = con.execute("""SELECT ii.unit_price AS p, ii.description AS d FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id
                                   WHERE ii.product_name=? AND ii.unit_price>0 ORDER BY (i.customer_id=?) DESC, i.invoice_date DESC, i.id DESC LIMIT 1""", (name, cid)).fetchone()
            if r: return float(r["p"]), (r["d"] or "")
        except Exception: pass
        return 0.0, (info_["description"] if info_ else "")
    def _barcode_scanned(self, code):
        """هر اسکن، مستقل از اینکه فوکوس کجاست:
        بارکد ثبت‌شده ← کالا (با آخرین قیمت) اضافه می‌شود؛ همان کالا پشت‌سرهم ← تعدادِ همان ردیف یکی زیاد می‌شود؛ کالای دیگر ← ردیف جدید.
        بارکد ناشناس ← همان لحظه کالا را از فهرست انتخاب یا جدید تایپ می‌کنی و به این بارکد وصل می‌شود."""
        code = barcodes.norm(code)
        with db() as con: row = barcodes.lookup(con, code)
        if row: name = row["product_name"]; desc = row["description"] or ""
        else:
            name = self._define_barcode(code); desc = ""
            if not name: return
        for r in range(self.items.rowCount()):
            it0 = self.items.item(r, 0)
            if it0 is not None and norm_key(it0.text()) == norm_key(name):
                self.items.blockSignals(True); self.items.setItem(r, 2, table_item(fmt_qty(self._num(r, 2) + 1)))
                if self._num(r, 3) <= 0:
                    pr, _d = self._price_for(name)
                    if pr > 0: self.items.setItem(r, 3, table_item(fmt_qty(pr)))
                self.items.blockSignals(False)
                self.recalc(); self.items.setCurrentCell(r, 2); self.items.scrollToItem(self.items.item(r, 0))
                self._vmsg(f"بارکد {code} ← «{name}»: تعداد شد {fmt_qty(self._num(r, 2))}"); return
        price, d2 = self._price_for(name); desc = desc or d2
        r = next((i for i in range(self.items.rowCount()) if not (self.items.item(i, 0).text().strip() if self.items.item(i, 0) else "")), None)
        if r is None: self.add_item_row(); r = self.items.rowCount() - 1
        self.items.blockSignals(True)
        for c, val in ((0, name), (1, desc), (2, "1"), (3, fmt_qty(price)), (5, "0")): self.items.setItem(r, c, table_item(val))
        self.items.blockSignals(False)
        self.recalc(); self.items.setCurrentCell(r, 2 if price > 0 else 3); self.items.scrollToItem(self.items.item(r, 0))
        self._vmsg(f"بارکد {code} ← «{name}» اضافه شد" + ("" if price > 0 else " (قیمتی ثبت نشده؛ قیمت را تایپ کن)"))
    def remove_item_row(self):
        r = self.items.currentRow()
        if r >= 0: self.items.removeRow(r); self.recalc()
    def _num(self, r, c):
        it = self.items.item(r, c)
        try: return parse_num(it.text()) if it else 0.0
        except Exception: return 0.0
    def _totals(self):
        gross = line = 0.0
        for r in range(self.items.rowCount()):
            gross += self._num(r, 2) * self._num(r, 3); line += self._num(r, 5)
        return gross, line
    def recalc(self, *_):
        gross = line = 0.0
        self.items.blockSignals(True)
        for r in range(self.items.rowCount()):
            g = self._num(r, 2) * self._num(r, 3); d = self._num(r, 5); gross += g; line += d
            self.items.setItem(r, 4, table_item(money(max(0.0, g - d))))
        self.items.blockSignals(False)
        extra = float(self.discount.value())
        self.lb_sub.setText(money(gross)); self.lb_disc.setText(money(line + extra)); self.total_label.setText(money(max(0.0, gross - line - extra)))
        self._cash_kind_changed(self.kind.currentText())
    def load_invoice(self, nxt_inv):
        self._loading = True
        try: InvoiceDialog._load_invoice(self, nxt_inv)
        finally: self._loading = False
        if NET_MODE == "client" and getattr(self, "invoice_id", None) is not None:
            with db() as con: self._fp = _doc_fp(con, self.invoice_id)
    def _load_invoice(self, nxt_inv):
        with db() as con:
            inv = con.execute("SELECT * FROM invoices WHERE id=?", (self.invoice_id,)).fetchone()
            if not inv: return
            rows = con.execute("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY row_no", (self.invoice_id,)).fetchall()
        self.customer.setCurrentIndex(self.customer.findData(inv["customer_id"]))
        self.description.setText(inv["description"] or "")
        self.initial_paid.setValue(inv["paid_initial"] or 0); self.notes.setText(inv["notes"] or "")
        if self.mode == "convert":
            self.no.setText(nxt_inv); self.inv_date.setIso(today_iso()); self.kind.setCurrentText("اعتباری")
            self.due.setIso((date.today() + timedelta(days=30)).isoformat())
        else:
            self.no.setText(inv["invoice_no"]); self.inv_date.setIso(inv["invoice_date"])
            if inv["kind"] in CREDIT_KINDS + ("نقدی",): self.kind.setCurrentText(inv["kind"])
            self.due.setIso(inv["due_date"] or ""); self.due.setEnabled(self.kind.currentText() in CREDIT_KINDS)
        self.items.setRowCount(0)
        for it in rows: self.add_item_row((it["product_name"], it["description"], fmt_qty(it["quantity"]), fmt_qty(it["unit_price"]), "", fmt_qty(it["discount"] or 0)))
        if not rows: self.add_item_row()
        self.discount.setValue(max(0.0, float(inv["discount"] or 0) - sum(float(it["discount"] or 0) for it in rows)))   # تخفیف کل = تخفیف ردیف‌ها + تخفیف اضافه
    def _cash_kind_changed(self, kind):
        """نقدی: پرداخت اولیه = مبلغ نهایی (قابل تغییر نیست). غیر نقدی: فیلد آزاد است."""
        if self.pf: return
        if kind != "نقدی":
            self.initial_paid.setEnabled(True); return
        gross, line = self._totals()
        total = max(0.0, gross - line - float(self.discount.value()))
        self.initial_paid.setValue(total)
        self.initial_paid.setEnabled(False)

    def _kind_changed(self, kind):
        """با عوض‌کردن نوع پرداخت، پرداخت اولیه هم هماهنگ می‌شود: نقدی ← کل فاکتور، اعتباری/چکی/ترکیبی ← صفر (مبلغ دلخواه را خودت وارد می‌کنی)."""
        prev = getattr(self, "_prev_kind", None); self._prev_kind = kind
        if self.pf: return
        if not getattr(self, "_loading", False) and kind != "نقدی" and prev == "نقدی":
            self.initial_paid.setValue(0)
        self._cash_kind_changed(kind)

    def _to_proforma(self):
        """تبدیل سند جدید از فاکتور به پیش‌فاکتور (شماره‌ی پیش‌فاکتور گرفته می‌شود)."""
        self.pf = True; self.mode = "proforma"
        with db() as con: nxt = con.execute("SELECT value FROM settings WHERE key='next_proforma'").fetchone()[0]
        self.no.setText(f"P{nxt}")
        try: self.setWindowTitle("پیش‌فاکتور")
        except Exception: pass
    def save_validate(self, print_after=False):
        if self.customer.currentData() is None:
            warn(self, "مشتری تعریف نشده", "ابتدا از بخش مشتریان، حداقل یک مشتری ثبت کن."); return
        idate = need_date(self, self.inv_date, "تاریخ")
        if not idate: return
        due = ""
        if not self.pf and self.kind.currentText() in CREDIT_KINDS:
            due = need_date(self, self.due, "تاریخ سررسید")
            if due is None: return
        clean = []
        for r in range(self.items.rowCount()):
            def val(c): return self.items.item(r, c).text().strip() if self.items.item(r, c) else ""
            name, desc = val(0), val(1)
            try: qty = parse_num(val(2)); price = parse_num(val(3)); ldisc = parse_num(val(5) or "0")
            except Exception: warn(self, "ورودی نامعتبر", "تعداد، قیمت و تخفیف باید عددی باشند."); return
            if name or desc or (qty not in (0, 1)) or price != 0 or ldisc != 0:
                if qty < 0 or price < 0 or ldisc < 0: warn(self, "ورودی نامعتبر", "تعداد، قیمت و تخفیف نمی‌توانند منفی باشند."); return
                if ldisc > qty * price + 0.5: warn(self, "تخفیف ردیف", f"تخفیف ردیف «{name or r + 1}» از مبلغ آن بیشتر است."); return
                clean.append((name, desc, qty, price, ldisc))
        if not clean: warn(self, "خالی", "حداقل یک ردیف کالا وارد کن."); return
        zero = sum(1 for c in clean if c[3] == 0)
        if zero and not self.pf:                      # فاکتور نهایی بدون قیمت ثبت نمی‌شود؛ فقط پیش‌فاکتور می‌تواند کالای بدون قیمت داشته باشد
            if self.invoice_id is not None:
                warn(self, "قیمت صفر", f"قیمت {zero} ردیف صفر است. فاکتور نهایی بدون قیمت ثبت نمی‌شود؛ قیمت همه‌ی کالاها را وارد کن."); return
            if not ask(self, "کالای بدون قیمت", f"قیمت {zero} ردیف صفر است و فاکتور بدون قیمت ثبت نمی‌شود.\nبه‌جای فاکتور، به‌صورت «پیش‌فاکتور» ذخیره شود؟"): return
            self._to_proforma(); due = ""
        kind = "پیش‌فاکتور" if self.pf else self.kind.currentText()
        # نقدی = تسویه کامل خودکار؛ فاکتور نقدی نباید در بدهکاران باقی بماند.
        subtotal = sum(float(qty) * float(price) for _, _, qty, price, _d in clean)
        total_disc = sum(float(c[4]) for c in clean) + float(self.discount.value())      # تخفیف ردیف‌ها + تخفیف اضافه‌ی فاکتور (مجموع در invoices.discount)
        total_due = max(0.0, subtotal - total_disc)
        paid = 0 if self.pf else (total_due if kind == "نقدی" else self.initial_paid.value())
        with db() as con:
            args = (self.customer.currentData(), idate, kind, due, self.description.text().strip(),
                    total_disc, paid, self.notes.text().strip())
            no = self.no.text().strip()
            _before = safety.snapshot(con, "invoice", self.invoice_id) if self.invoice_id is not None else None
            if NET_MODE == "client" and self.invoice_id is not None and getattr(self, "_fp", None) and _doc_fp(con, self.invoice_id) != self._fp:
                warn(self, "ویرایش هم‌زمان", "این سند بعد از باز کردنش توسط کاربر دیگری تغییر کرده است.\nبرای اینکه تغییرات او پاک نشود، پنجره را ببند و سند را دوباره باز کن."); return
            if self.invoice_id is None:
                if con.execute("SELECT 1 FROM invoices WHERE invoice_no=?", (no,)).fetchone():
                    if NET_MODE != "client": warn(self, "شماره تکراری", "این شماره قبلاً استفاده شده است."); return
                    self._renumbered = no; no = _next_free_no(con, self.pf, no)        # شبکه: کاربر دیگری هم‌زمان همین شماره را گرفته؛ شماره‌ی آزاد بعدی
                iid = con.execute("""INSERT INTO invoices(customer_id,invoice_date,kind,due_date,description,discount,paid_initial,notes,invoice_no,created_at,is_proforma)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (*args, no, datetime.now().isoformat(timespec="seconds"), 1 if self.pf else 0)).lastrowid
            elif self.mode == "convert":
                iid = self.invoice_id
                if con.execute("SELECT 1 FROM invoices WHERE invoice_no=? AND id!=?", (no, iid)).fetchone():
                    warn(self, "شماره تکراری", "این شماره قبلاً استفاده شده است."); return
                con.execute("""UPDATE invoices SET customer_id=?,invoice_date=?,kind=?,due_date=?,description=?,discount=?,paid_initial=?,notes=?,
                               invoice_no=?,is_proforma=0 WHERE id=?""", (*args, no, iid))
                con.execute("DELETE FROM invoice_items WHERE invoice_id=?", (iid,))
            else:
                iid = self.invoice_id
                con.execute("""UPDATE invoices SET customer_id=?,invoice_date=?,kind=?,due_date=?,description=?,discount=?,paid_initial=?,notes=? WHERE id=?""", (*args, iid))
                con.execute("DELETE FROM invoice_items WHERE invoice_id=?", (iid,))
            if self.pf and self.invoice_id is None:
                con.execute("UPDATE settings SET value=? WHERE key='next_proforma'", (str(int(no[1:]) + 1 if no[1:].isdigit() else 1),))
            elif not self.pf and (self.invoice_id is None or self.mode == "convert"):
                con.execute("UPDATE settings SET value=? WHERE key='next_invoice'", (str(int(no) + 1 if no.isdigit() else int(datetime.now().timestamp())),))
            for idx, (name, desc, qty, price, ldisc) in enumerate(clean, 1):
                con.execute("INSERT INTO invoice_items(invoice_id,row_no,product_name,description,quantity,unit_price,discount) VALUES(?,?,?,?,?,?,?)", (iid, idx, name, desc, qty, price, ldisc))
                try: learn_item(con, self.customer.currentData(), name, desc, qty, price, idate, self.invoice_id is None or self.mode == "convert")
                except Exception: pass
            try: safety.log_invoice_change(con, AUTH.username, "create" if self.invoice_id is None else ("convert" if self.mode == "convert" else "edit"), iid, _before)
            except Exception: pass
            try:   # learn from corrections: spoken name -> final name the user typed
                for r in range(self.items.rowCount()):
                    it0 = self.items.item(r, 0); sp = it0.data(Qt.UserRole) if it0 else None
                    if sp and it0.text().strip() and norm_key(sp) != norm_key(it0.text()): learn_alias("product", sp, it0.text().strip(), con)
            except Exception: pass
        self.saved_id = iid; self.final_no = no; self.print_after = print_after is True
        if getattr(self, "_renumbered", None): info(self, "شماره‌ی سند", f"شماره‌ی {self._renumbered} هم‌زمان توسط کاربر دیگری ثبت شد؛ سند با شماره‌ی {no} ذخیره شد.")
        self.accept()

class PaymentDialog(QDialog):
    def __init__(self, parent=None, data=None, customer_id=None):
        super().__init__(parent); self.setWindowTitle("ویرایش دریافت" if data else "ثبت دریافت"); self.setMinimumWidth(480)
        self.data = data
        with db() as con:
            customers = con.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
        form = QFormLayout(self)
        self.cbox = QComboBox()
        for c in customers: self.cbox.addItem(c["name"], c["id"])
        make_searchable_combo(self.cbox)
        self.ibox = QComboBox(); self.bal = QLabel(""); self.bal.setStyleSheet("font-weight:bold;")
        self.date = JalaliDateEdit(data["payment_date"] if data else None); self.amount = money_spin(); self.disc = money_spin()
        self.method = QComboBox(); self.method.addItems(PAY_METHODS)
        self.ref = QLineEdit(); self.notes = QLineEdit()
        want = data["customer_id"] if data else customer_id
        if want: self.cbox.setCurrentIndex(max(0, self.cbox.findData(want)))
        self.fill_invoices(data["invoice_id"] if data else None)
        self.cbox.currentIndexChanged.connect(lambda: self.fill_invoices(None))
        if data:
            self.amount.setValue(data["amount"]); self.disc.setValue(float(data["discount"] or 0))
            if self.method.findText(data["method"]) < 0: self.method.addItem(data["method"])
            self.method.setCurrentText(data["method"]); self.ref.setText(data["reference"] or ""); self.notes.setText(data["notes"] or "")
        form.addRow("مشتری", self.cbox); form.addRow("مانده فعلی مشتری", self.bal); form.addRow("فاکتور مرتبط", self.ibox)
        form.addRow("تاریخ دریافت (شمسی) / روش", _pair(self.date, self.method)); form.addRow("مبلغ (تومان)", with_words(self.amount)); form.addRow("تخفیف (تومان)", with_words(self.disc))
        form.addRow("شماره پیگیری", self.ref); form.addRow("توضیحات", self.notes)
        b = bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); form.addRow(b)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject)
        install_voice(self, parent, "payment")
    def fill_invoices(self, select_id):
        cid = self.cbox.currentData(); self.ibox.clear(); self.ibox.addItem("بدون اتصال به فاکتور", None)
        if cid is None: return
        with db() as con:
            invs = invoice_rows(con, cid); bal = customer_figures(con)[cid]["balance"]
        for i in reversed(invs):
            self.ibox.addItem(f"{i['invoice_no']} — {iso_to_j(i['invoice_date'])} — مانده {money(i['remain'])}", i["id"])
        self.bal.setText(f"{money(bal)} تومان")
        if select_id: self.ibox.setCurrentIndex(max(0, self.ibox.findData(select_id)))
    def values(self):
        return (self.cbox.currentData(), self.ibox.currentData(), self.date.iso(), self.amount.value(),
                self.method.currentText(), self.ref.text().strip(), self.notes.text().strip(), self.disc.value())
    def accept(self):
        if self.cbox.currentData() is None: warn(self, "مشتری", "ابتدا مشتری ثبت کن."); return
        if need_date(self, self.date, "تاریخ دریافت") is None: return
        if self.amount.value() < 0 or self.disc.value() < 0 or (self.amount.value() + self.disc.value()) <= 0: warn(self, "مبلغ نامعتبر", "مبلغ دریافت یا تخفیف باید بیشتر از صفر باشد."); return
        super().accept()

class ReturnDialog(QDialog):
    """ثبت / ویرایش مرجوعی کالا؛ جمع مرجوعی از مانده‌ی مشتری (و فاکتور مرتبط) کم می‌شود."""
    def __init__(self, parent=None, rid=None):
        super().__init__(parent); self.rid = rid; self.saved_id = None
        self.setWindowTitle("ویرایش مرجوعی" if rid else "ثبت مرجوعی"); self.resize(900, 560)
        with db() as con:
            customers = con.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
            h, its = retdb.load_return(con, rid) if rid else (None, [])
        root = QVBoxLayout(self); form = QFormLayout()
        self.cbox = QComboBox()
        for c in customers: self.cbox.addItem(c["name"], c["id"])
        make_searchable_combo(self.cbox)
        if h: self.cbox.setCurrentIndex(max(0, self.cbox.findData(h["customer_id"])))
        self.ibox = QComboBox(); self.date = JalaliDateEdit(h["return_date"] if h else None); self.notes = QLineEdit(h["notes"] if h else "")
        self.fill_invoices(h["invoice_id"] if h else None)
        self.cbox.currentIndexChanged.connect(lambda _i: self.fill_invoices(None))
        form.addRow("مشتری", self.cbox); form.addRow("فاکتور مرتبط (اختیاری)", self.ibox)
        form.addRow("تاریخ مرجوعی (شمسی)", self.date); form.addRow("توضیحات / علت مرجوعی", self.notes)
        root.addLayout(form)
        self.items = QTableWidget(0, 4); self.items.setHorizontalHeaderLabels(["نام کالا", "شرح", "تعداد", "قیمت واحد (تومان)"])
        self.items.horizontalHeader().setStretchLastSection(True); self.items.setColumnWidth(0, 300); self.items.setColumnWidth(1, 220)
        root.addWidget(self.items)
        for it in its: self.add_row(it["product_name"], it["description"], fmt_qty(it["quantity"]), fmt_qty(it["unit_price"]))
        if not its: self.add_row()
        row = QHBoxLayout()
        for text, fn in (("＋ ردیف", lambda: self.add_row()), ("حذف ردیف", self.del_row), ("پر کردن از اقلام فاکتور", self.fill_from_invoice)):
            b = QPushButton(text); b.clicked.connect(fn); row.addWidget(b)
        self.total = QLabel(""); self.total.setStyleSheet("font-weight:bold;"); row.addStretch(); row.addWidget(self.total); root.addLayout(row)
        self.items.itemChanged.connect(self.recalc); self.recalc()
        bb = bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); root.addWidget(bb)
    def fill_invoices(self, select_id):
        cid = self.cbox.currentData(); self.ibox.clear(); self.ibox.addItem("بدون اتصال به فاکتور", None)
        if cid is None: return
        with db() as con: invs = invoice_rows(con, cid)
        for i in reversed(invs): self.ibox.addItem(f"{i['invoice_no']} — {iso_to_j(i['invoice_date'])} — مبلغ {money(i['total'])}", i["id"])
        if select_id: self.ibox.setCurrentIndex(max(0, self.ibox.findData(select_id)))
    def add_row(self, name="", desc="", qty="1", price=""):
        r = self.items.rowCount(); self.items.insertRow(r)
        for c, v in enumerate((name, desc, qty, price)): self.items.setItem(r, c, QTableWidgetItem(str(v if v is not None else "")))
    def del_row(self):
        r = self.items.currentRow()
        if r >= 0: self.items.removeRow(r); self.recalc()
    def fill_from_invoice(self):
        iid = self.ibox.currentData()
        if not iid: return warn(self, "فاکتور", "ابتدا فاکتور مرتبط را انتخاب کن.")
        with db() as con: its = con.execute("SELECT product_name,description,quantity,unit_price FROM invoice_items WHERE invoice_id=? ORDER BY row_no", (iid,)).fetchall()
        self.items.setRowCount(0)
        for it in its: self.add_row(it["product_name"], it["description"], fmt_qty(it["quantity"]), fmt_qty(it["unit_price"]))
        info(self, "اقلام فاکتور", "اقلام فاکتور آورده شد. تعداد را روی مقدار برگشتی اصلاح کن و ردیف‌های برنگشته را حذف کن.")
    def _rows(self):
        out = []
        for r in range(self.items.rowCount()):
            def val(c): return self.items.item(r, c).text().strip() if self.items.item(r, c) else ""
            if not (val(0) or val(1) or val(3)): continue
            out.append((val(0), val(1), parse_num(val(2) or "0"), parse_num(val(3) or "0")))
        return out
    def recalc(self, *_):
        try: self.total.setText(f"جمع مرجوعی: {money(retdb.items_total(self._rows()))} تومان")
        except Exception: self.total.setText("")
    def accept(self):
        if self.cbox.currentData() is None: return warn(self, "مشتری", "ابتدا مشتری ثبت کن.")
        idate = need_date(self, self.date, "تاریخ مرجوعی")
        if not idate: return
        try: items = self._rows()
        except Exception: return warn(self, "ورودی نامعتبر", "تعداد و قیمت باید عددی باشند.")
        if not items: return warn(self, "خالی", "حداقل یک ردیف کالا وارد کن.")
        for n, _d, q, p in items:
            if not n: return warn(self, "نام کالا", "نام کالا در همه‌ی ردیف‌ها باید پر باشد.")
            if q <= 0 or p < 0: return warn(self, "ورودی نامعتبر", "تعداد باید بیشتر از صفر و قیمت نباید منفی باشد.")
        if retdb.items_total(items) <= 0 and not ask(self, "مبلغ صفر", "جمع مرجوعی صفر است. با همین حالت ذخیره شود؟"): return
        with db() as con:
            self.saved_id, _no = retdb.save_return(con, self.rid, self.cbox.currentData(), self.ibox.currentData(), idate, self.notes.text().strip(), items)
        super().accept()

class ChequeDialog(QDialog):
    def __init__(self, parent=None, data=None):
        super().__init__(parent); self.setWindowTitle("ویرایش چک" if data else "ثبت چک"); self.setMinimumWidth(480)
        with db() as con: customers = con.execute("SELECT id,name FROM customers ORDER BY name").fetchall()
        form = QFormLayout(self)
        self.cb = QComboBox()
        for c in customers: self.cb.addItem(c["name"], c["id"])
        make_searchable_combo(self.cb)
        self.ib = QComboBox()
        self.no = QLineEdit(); self.bank = QLineEdit(); self.issuer = QLineEdit(); self.amount = money_spin()
        self.due = JalaliDateEdit(data["due_date"] if data else (date.today() + timedelta(days=30)).isoformat())
        self.status = QComboBox(); self.status.addItems(CHQ_STATUSES); self.notes = QLineEdit()
        if data:
            self.cb.setCurrentIndex(max(0, self.cb.findData(data["customer_id"])))
            self.no.setText(data["cheque_no"] or ""); self.bank.setText(data["bank"] or ""); self.issuer.setText(data["issuer"] or "")
            self.amount.setValue(data["amount"]); self.status.setCurrentText(data["status"]); self.notes.setText(data["notes"] or "")
        self.fill_invoices(data["invoice_id"] if data else None)
        self.cb.currentIndexChanged.connect(lambda: self.fill_invoices(None))
        _hint = QLabel("با ثبت چک، مبلغ آن از حساب مشتری کم می‌شود (مگر وضعیت «برگشتی» یا «مستردشده» باشد)."); _hint.setStyleSheet("color:#1565c0;")
        form.addRow(_hint)
        for label, w in [("مشتری", self.cb), ("فاکتور مرتبط", self.ib), ("شماره چک", self.no), ("بانک", self.bank), ("صادرکننده", self.issuer),
                         ("مبلغ (تومان)", with_words(self.amount)), ("سررسید (شمسی) / وضعیت", _pair(self.due, self.status)), ("توضیحات", self.notes)]:
            form.addRow(label, w)
        b = bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); form.addRow(b)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject)
        install_voice(self, parent, "cheque")
    def fill_invoices(self, select_id):
        cid = self.cb.currentData(); self.ib.clear(); self.ib.addItem("بدون اتصال به فاکتور", None)
        if cid is None: return
        with db() as con: invs = invoice_rows(con, cid)
        for i in reversed(invs): self.ib.addItem(f"{i['invoice_no']} — {iso_to_j(i['invoice_date'])}", i["id"])
        if select_id: self.ib.setCurrentIndex(max(0, self.ib.findData(select_id)))
    def values(self):
        return (self.cb.currentData(), self.ib.currentData(), self.no.text().strip(), self.bank.text().strip(), self.issuer.text().strip(),
                self.amount.value(), self.due.iso(), self.status.currentText(), self.notes.text().strip())
    def accept(self):
        if self.cb.currentData() is None: warn(self, "مشتری", "ابتدا مشتری ثبت کن."); return
        if self.amount.value() <= 0: warn(self, "مبلغ نامعتبر", "مبلغ چک باید بیشتر از صفر باشد."); return
        if not need_date(self, self.due, "تاریخ سررسید"): return
        super().accept()

PAPER_SPINS = [("font_size", "اندازه فونت متن فاکتور", 6, 18), ("table_font_size", "اندازه فونت جدول فاکتور", 5, 18),
               ("title_size", "اندازه عنوان", 8, 30), ("cell_pad", "فاصله داخل خانه‌های جدول", 0, 12),
               ("margin", "حاشیه صفحه (میلی‌متر)", 0, 40), ("logo_h", "ارتفاع لوگو", 20, 150),
               ("totals_width", "عرض جدول جمع‌ها (درصد)", 25, 100), ("col_row", "عرض ستون ردیف (درصد)", 3, 15),
               ("col_qty", "عرض ستون تعداد (درصد)", 4, 20), ("col_price", "عرض ستون قیمت (درصد)", 8, 30),
               ("col_amount", "عرض ستون مبلغ (درصد)", 8, 30), ("rep_font_size", "اندازه فونت گزارش‌ها", 5, 16),
               ("rep_pad", "فاصله خانه‌های گزارش‌ها", 0, 12)]

class InvoiceStyleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("تنظیمات چاپ: ظاهر، اندازه و چیدمان"); self.resize(1280, 800)
        self.raw = load_raw_style(); g = self.raw["g"]; self._doc = None; self.cur = g["paper"]; self._ready = False
        root = QHBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFixedWidth(470)
        side = QWidget(); sl = QVBoxLayout(side); scroll.setWidget(side)
        # global
        gb = QGroupBox("مشخصات و ظاهر کلی (مشترک بین A4 و A5)"); form = QFormLayout(gb)
        self.title = QLineEdit(g["title"]); self.pftitle = QLineEdit(g["proforma_title"]); self.pfnote = QLineEdit(g["proforma_note"])
        self.shop = QLineEdit(g["shop_name"]); self.phone = QLineEdit(g["shop_phone"]); self.addr = QLineEdit(g["shop_address"])
        self.footer = QLineEdit(g["footer"]); self.logo = QLineEdit(g["logo_path"]); self.logo.setReadOnly(True)
        self.receipt_width = QComboBox(); self.receipt_width.addItem("۵۸ میلی‌متر", 58); self.receipt_width.addItem("۸۰ میلی‌متر", 80)
        self.receipt_width.setCurrentIndex(0 if int(g.get("receipt_width",80)) == 58 else 1)
        self.receipt_font = QSpinBox(); self.receipt_font.setRange(6,16); self.receipt_font.setValue(int(g.get("receipt_font_size",9)))
        self.receipt_margin_top = QSpinBox(); self.receipt_margin_top.setRange(0,15); self.receipt_margin_top.setValue(int(g.get("receipt_margin_top",0)))
        self.receipt_margin_bottom = QSpinBox(); self.receipt_margin_bottom.setRange(0,20); self.receipt_margin_bottom.setValue(int(g.get("receipt_margin_bottom",2)))
        self.receipt_margin_side = QSpinBox(); self.receipt_margin_side.setRange(0,10); self.receipt_margin_side.setValue(int(g.get("receipt_margin_side",0)))
        self.receipt_direct = QCheckBox("چاپ مستقیم رول (ESC/POS) — یک رول کامل و سپس برش"); self.receipt_direct.setChecked(bool(g.get("receipt_direct",True)))
        self.receipt_printer = QComboBox(); self.receipt_printer.addItem("هر بار بپرس", "")
        for _p in QPrinterInfo.availablePrinters(): self.receipt_printer.addItem(_p.printerName(), _p.printerName())
        _i = self.receipt_printer.findData(str(g.get("receipt_printer", ""))); self.receipt_printer.setCurrentIndex(max(0, _i))
        self.receipt_logo = QCheckBox("نمایش لوگو روی فیش"); self.receipt_logo.setChecked(bool(g.get("receipt_show_logo",False)))
        lb = QPushButton("انتخاب"); lb.clicked.connect(self.pick_logo); lc = QPushButton("حذف"); lc.clicked.connect(lambda: (self.logo.setText(""), self.refresh()))
        lrow = QHBoxLayout(); lrow.addWidget(self.logo); lrow.addWidget(lb); lrow.addWidget(lc)
        self.accent = g["accent"]; self.accent_btn = QPushButton("انتخاب رنگ"); self.accent_btn.clicked.connect(self.pick_color)
        self.font = QFontComboBox(); self.font.setCurrentFont(QFont(g["font"]))
        self.layout_c = QComboBox()
        for _k, _t in INVOICE_TEMPLATES: self.layout_c.addItem(_t, _k)
        self.layout_c.setCurrentIndex(max(0, self.layout_c.findData(g["layout"])))
        self.unit = QLineEdit(g["unit"]); self.sell = QLineEdit(g["seller_label"]); self.buy = QLineEdit(g["buyer_label"])
        self.inv_paper = QComboBox(); self.rep_paper = QComboBox()
        for c in (self.inv_paper, self.rep_paper): c.addItem("A4", "A4"); c.addItem("A5", "A5")
        self.inv_paper.setCurrentIndex(0 if g["paper"] == "A4" else 1); self.rep_paper.setCurrentIndex(0 if g["report_paper"] == "A4" else 1)
        for lab, w in [("عنوان فاکتور", self.title), ("عنوان پیش‌فاکتور", self.pftitle), ("یادداشت پایین پیش‌فاکتور", self.pfnote),
                       ("نام فروشگاه", self.shop), ("تلفن فروشگاه", self.phone), ("آدرس فروشگاه", self.addr), ("لوگو", lrow),
                       ("رنگ اصلی", self.accent_btn), ("فونت", self.font), ("قالب فاکتور و پیش‌فاکتور", self.layout_c), ("واحد پول", self.unit),
                       ("برچسب امضای فروشنده", self.sell), ("برچسب امضای خریدار", self.buy), ("متن پایین فاکتور", self.footer),
                       ("چاپگر پیش‌فرض فیش", self.receipt_printer), ("عرض فیش پرینتر", self.receipt_width), ("فونت فیش", self.receipt_font),
                       ("حاشیه بالای فیش (میلی‌متر)", self.receipt_margin_top), ("حاشیه پایین فیش (میلی‌متر)", self.receipt_margin_bottom),
                       ("حاشیه چپ و راست فیش (میلی‌متر)", self.receipt_margin_side), ("روش چاپ رول", self.receipt_direct), ("لوگوی فیش", self.receipt_logo),
                       ("چاپ فاکتور و پیش‌فاکتور روی کاغذ", self.inv_paper), ("چاپ گزارش‌ها روی کاغذ", self.rep_paper)]:
            form.addRow(lab, w)
        sl.addWidget(gb)
        # per paper
        pb = QGroupBox("اندازه و چیدمان — جداگانه برای هر سایز کاغذ"); pf = QFormLayout(pb)
        self.edit_paper = QComboBox(); self.edit_paper.addItem("ویرایش تنظیمات کاغذ A4", "A4"); self.edit_paper.addItem("ویرایش تنظیمات کاغذ A5", "A5")
        self.edit_paper.setCurrentIndex(0 if self.cur == "A4" else 1); pf.addRow(self.edit_paper)
        self.spins = {}
        for key, lab, lo, hi in PAPER_SPINS:
            s = QSpinBox(); s.setRange(lo, hi); self.spins[key] = s; pf.addRow(lab, s)
        self.orient = QComboBox(); self.orient.addItem("عمودی", "portrait"); self.orient.addItem("افقی", "landscape")
        self.rep_orient = QComboBox(); self.rep_orient.addItem("خودکار (افقی برای گزارش‌های پرستون)", "auto"); self.rep_orient.addItem("عمودی", "portrait"); self.rep_orient.addItem("افقی", "landscape")
        pf.addRow("جهت کاغذ فاکتور", self.orient); pf.addRow("جهت کاغذ گزارش‌ها", self.rep_orient)
        self.checks = {}
        cbox = QGroupBox("نمایش یا حذف بخش‌های فاکتور"); cl = QVBoxLayout(cbox)
        for k, lab in SHOW_KEYS:
            cb = QCheckBox(lab); self.checks[k] = cb; cl.addWidget(cb)
        pf.addRow(cbox); sl.addWidget(pb)
        self.rows = QSpinBox(); self.rows.setRange(1, 60); self.rows.setValue(12)
        sb = QGroupBox("پیش‌نمایش"); sf = QFormLayout(sb); sf.addRow("تعداد ردیف کالای نمونه", self.rows)
        self.pf_check = QCheckBox("نمایش به‌صورت پیش‌فاکتور"); sf.addRow(self.pf_check); sl.addWidget(sb); sl.addStretch()
        self.apply_paper(self.cur)
        for w in (self.title, self.pftitle, self.pfnote, self.shop, self.phone, self.addr, self.footer, self.unit, self.sell, self.buy): w.textChanged.connect(self.refresh)
        for w in (self.layout_c, self.orient, self.rep_orient): w.currentIndexChanged.connect(self.refresh)
        self.font.currentFontChanged.connect(self.refresh); self.rows.valueChanged.connect(self.refresh); self.pf_check.toggled.connect(self.refresh)
        for s in self.spins.values(): s.valueChanged.connect(self.refresh)
        for cb in self.checks.values(): cb.toggled.connect(self.refresh)
        self.edit_paper.currentIndexChanged.connect(self.switch_paper)
        right = QVBoxLayout(); right.addWidget(QLabel("پیش‌نمایش با داده‌های نمونه (برای دیدن تعداد صفحه و چیدمان واقعی، «پیش‌نمایش دقیق چاپ» را بزن)."))
        self.preview = QTextBrowser(); self.preview.setStyleSheet("background:white;"); right.addWidget(self.preview)
        b = bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        exact = b.addButton("پیش‌نمایش دقیق چاپ", QDialogButtonBox.ActionRole); exact.clicked.connect(self.exact_preview)
        receipt = b.addButton("نمونه فیش پرینتر", QDialogButtonBox.ActionRole); receipt.clicked.connect(self.receipt_preview)
        reset = b.addButton("بازگشت به پیش‌فرض", QDialogButtonBox.ResetRole); reset.clicked.connect(self.reset_defaults)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); right.addWidget(b)
        root.addLayout(right, 1); root.addWidget(scroll)
        self._ready = True; self.refresh()
    def pick_logo(self):
        p, _ = QFileDialog.getOpenFileName(self, "انتخاب لوگو", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if p: self.logo.setText(p); self.refresh()
    def pick_color(self):
        c = QColorDialog.getColor(QColor(self.accent), self, "رنگ اصلی")
        if c.isValid(): self.accent = c.name(); self.refresh()
    def collect_paper(self):
        d = {k: s.value() for k, s in self.spins.items()}
        d.update(orientation=self.orient.currentData(), rep_orient=self.rep_orient.currentData())
        d.update({k: cb.isChecked() for k, cb in self.checks.items()}); return d
    def apply_paper(self, name):
        p = self.raw["paper"][name]; wl = list(self.spins.values()) + list(self.checks.values()) + [self.orient, self.rep_orient]
        for w in wl: w.blockSignals(True)
        for k, s in self.spins.items(): s.setValue(int(p[k]))
        self.orient.setCurrentIndex(0 if p["orientation"] == "portrait" else 1)
        self.rep_orient.setCurrentIndex(max(0, self.rep_orient.findData(p["rep_orient"])))
        for k, cb in self.checks.items(): cb.setChecked(bool(p[k]))
        for w in wl: w.blockSignals(False)
    def switch_paper(self):
        self.raw["paper"][self.cur].update(self.collect_paper()); self.cur = self.edit_paper.currentData()
        self.apply_paper(self.cur); self.refresh()
    def current_raw(self):
        raw = {"g": dict(self.raw["g"]), "paper": {k: dict(v) for k, v in self.raw["paper"].items()}}
        raw["g"].update(title=self.title.text().strip() or "فاکتور فروش", proforma_title=self.pftitle.text().strip() or "پیش‌فاکتور",
                        proforma_note=self.pfnote.text().strip(), shop_name=self.shop.text().strip(), shop_phone=self.phone.text().strip(),
                        shop_address=self.addr.text().strip(), footer=self.footer.text().strip(), logo_path=self.logo.text(),
                        accent=self.accent, font=self.font.currentFont().family(), layout=self.layout_c.currentData(),
                        unit=self.unit.text().strip() or "تومان", seller_label=self.sell.text().strip(), buyer_label=self.buy.text().strip(),
                        paper=self.inv_paper.currentData(), report_paper=self.rep_paper.currentData(),
                        receipt_width=self.receipt_width.currentData(), receipt_font_size=self.receipt_font.value(),
                        receipt_margin=0, receipt_margin_top=self.receipt_margin_top.value(), receipt_margin_bottom=self.receipt_margin_bottom.value(),
                        receipt_margin_side=self.receipt_margin_side.value(), receipt_direct=self.receipt_direct.isChecked(), receipt_printer=self.receipt_printer.currentData() or "", receipt_show_logo=self.receipt_logo.isChecked())
        raw["paper"][self.cur].update(self.collect_paper()); return raw
    def flat(self):
        return flatten_style(self.current_raw(), "invoice", paper=self.cur)
    def doc(self):
        st = self.flat(); return st, make_document(build_invoice_html(sample_invoice_data(self.rows.value(), self.pf_check.isChecked()), st), st)
    def refresh(self, *_):
        if not self._ready: return
        st, self._doc = self.doc()
        self.accent_btn.setStyleSheet(f"background:{st['accent']};color:white;"); self.preview.setDocument(self._doc)
    def exact_preview(self):
        st, doc = self.doc(); show_preview(self, doc, make_printer(st), "پیش‌نمایش دقیق چاپ")
    def receipt_preview(self):
        raw = self.current_raw(); st = flatten_style(raw, "invoice", paper=self.cur)
        doc, gst = make_receipt_document(sample_invoice_data(self.rows.value(), self.pf_check.isChecked()), raw["g"])
        show_preview(self, doc, receipt_printer_for_document(doc, gst), "پیش‌نمایش فیش پرینتر")
    def reset_defaults(self):
        if ask(self, "بازگشت به پیش‌فرض", "همه تنظیمات چاپ به حالت اولیه برگردد؟"):
            save_raw_style(default_raw_style()); self.reject()
    def accept(self):
        save_raw_style(self.current_raw()); super().accept()

# ───────────────────────────── Report panel ─────────────────────────────
def filter_columns(rep, hidden):
    if not hidden: return rep
    keep = [i for i in range(len(rep["headers"])) if i not in hidden]; remap = {o: n for n, o in enumerate(keep)}
    r = dict(rep)
    r["headers"] = [rep["headers"][i] for i in keep]; r["rows"] = [[row[i] for i in keep] for row in rep["rows"]]
    r["money_cols"] = {remap[c] for c in rep["money_cols"] if c in remap}; r["qty_cols"] = {remap[c] for c in rep["qty_cols"] if c in remap}
    r["landscape"] = len(keep) >= 8
    return r

_JDATE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")

def sort_key(v):
    """Sort key that understands numbers (also '1,200,000' and Persian digits), Jalali dates and text. Empty values sort last."""
    if v is None or str(v).strip() == "": return (2, 0)
    if isinstance(v, (int, float)): return (0, float(v))
    t = norm_digits(str(v)).strip(); m_ = _JDATE.match(t)
    if m_: return (0, int(m_.group(1)) * 10000 + int(m_.group(2)) * 100 + int(m_.group(3)))
    try: return (0, float(t.replace(",", "").replace("٬", "")))
    except ValueError: return (1, norm_key(t))

def sort_report(base, col, asc):
    """Returns a copy of the report sorted by column `col`. Reports made of groups (rows + a bold sub-total row) are sorted
    group by group using the bold row, so a customer's invoices never get separated from his total; a bold footer stays last."""
    rows, bold, cids, n = base["rows"], base["bold"], base.get("cids"), len(base["rows"])
    tail = n
    while tail - 1 in bold: tail -= 1
    interior = [b for b in bold if b < tail]
    footer = []
    if not bold: groups = [[i] for i in range(n)]
    elif not interior:
        groups = [[i] for i in range(tail)]; footer = list(range(tail, n))
    elif 0 in bold:
        groups = []
        for i in range(n):
            if i in bold or not groups: groups.append([i])
            else: groups[-1].append(i)
    else:
        groups, cur = [], []
        for i in range(n):
            cur.append(i)
            if i in bold: groups.append(cur); cur = []
        if cur: groups.append(cur)
    def gkey(g): return sort_key(rows[next((i for i in g if i in bold), g[0])][col])
    keyed = [(gkey(g), g) for g in groups]
    full = [kg for kg in keyed if kg[0][0] != 2]; empty = [kg for kg in keyed if kg[0][0] == 2]
    full.sort(key=lambda kg: kg[0], reverse=not asc)
    order = [i for _, g in full + empty for i in g] + footer
    new = dict(base)
    new["rows"] = [rows[i] for i in order]; new["bold"] = {k for k, i in enumerate(order) if i in bold}
    if cids: new["cids"] = [cids[i] for i in order]
    return new

class ReportPanel(QWidget):
    def __init__(self, parent=None, statement_cb=None):
        super().__init__(parent); self.rep = None; self.view = None; self.hidden = set(); self.statement_cb = statement_cb
        lay = QVBoxLayout(self)
        self.head = QLabel(""); self.head.setStyleSheet("font-size:15px;font-weight:bold;color:#286b4a;"); lay.addWidget(self.head)
        self.sub = QLabel(""); lay.addWidget(self.sub)
        self.table = QTableWidget(0, 0); self.table.setAlternatingRowColors(True); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_statement); lay.addWidget(self.table)
        self.base = None; self.sort = None          # sort = (original column, ascending)
        hdr = self.table.horizontalHeader(); hdr.setSectionsClickable(True); hdr.sectionClicked.connect(self._header_clicked)
        hdr.setToolTip("برای مرتب‌سازی روی عنوان ستون کلیک کن (کم به زیاد ← زیاد به کم ← ترتیب اصلی)")
        self.totals = QLabel(""); self.totals.setStyleSheet("font-weight:bold;background:#e4eee7;padding:8px;border-radius:5px;"); self.totals.setWordWrap(True)
        lay.addWidget(self.totals)
        bar = QHBoxLayout()
        self.b_print = QPushButton("مشاهده / چاپ"); self.b_pdf = QPushButton("خروجی PDF"); self.b_share = QPushButton("اشتراک"); self.b_xls = QPushButton("خروجی Excel")
        self.b_cols = QPushButton("انتخاب ستون‌ها"); self.b_set = QPushButton("تنظیمات اندازه و چیدمان"); self.b_stmt = QPushButton("صورت‌حساب مشتری انتخاب‌شده")
        self.b_print.clicked.connect(self.do_print); self.b_pdf.clicked.connect(self.do_pdf); self.b_xls.clicked.connect(self.do_excel); self.b_share.clicked.connect(self.do_share)
        self.b_cols.clicked.connect(self.pick_columns); self.b_set.clicked.connect(self.open_settings); self.b_stmt.clicked.connect(self.open_statement)
        for b in (self.b_print, self.b_pdf, self.b_share, self.b_xls, self.b_cols, self.b_set, self.b_stmt): bar.addWidget(b)
        bar.addStretch(); lay.addLayout(bar)
        for b in (self.b_print, self.b_pdf, self.b_share, self.b_xls, self.b_cols, self.b_stmt): b.setEnabled(False)
    def set_report(self, rep):
        self.base = rep; self.rep = rep; self.sort = None; self.hidden = set(); self.render()
    def _header_clicked(self, vcol):
        if not self.base or self.base.get("nosort"): return
        keep = [i for i in range(len(self.base["headers"])) if i not in self.hidden]
        if not (0 <= vcol < len(keep)): return
        col = keep[vcol]
        if self.sort and self.sort[0] == col: self.sort = (col, False) if self.sort[1] else None
        else: self.sort = (col, True)
        self.render()
    def render(self):
        self.rep = sort_report(self.base, *self.sort) if (self.sort and self.base) else self.base
        rep = self.view = filter_columns(self.rep, self.hidden)
        keep = [i for i in range(len(self.base["headers"])) if i not in self.hidden]
        hdr = self.table.horizontalHeader()
        if self.sort and self.sort[0] in keep:
            hdr.setSortIndicatorShown(True); hdr.setSortIndicator(keep.index(self.sort[0]), Qt.AscendingOrder if self.sort[1] else Qt.DescendingOrder)
        else: hdr.setSortIndicatorShown(False)
        self.head.setText(rep["title"]); self.sub.setText(rep.get("subtitle", ""))
        t = self.table; t.setRowCount(0); t.setColumnCount(len(rep["headers"])); t.setHorizontalHeaderLabels(rep["headers"])
        f = QFont(); f.setBold(True)
        for r, row in enumerate(rep["rows"]):
            t.insertRow(r)
            for c, v in enumerate(row):
                it = table_item(cell_text(rep, c, v))
                if r in rep["bold"]: it.setFont(f); it.setBackground(QColor("#e4eee7"))
                if rep.get("debtor") and ("مانده" in rep["headers"][c] or "بدهی" in rep["headers"][c]) and isinstance(v, (int, float)) and v > 0.5: it.setForeground(QColor("#c62828"))
                t.setItem(r, c, it)
        t.resizeColumnsToContents(); t.horizontalHeader().setStretchLastSection(True)
        self.totals.setText("   |   ".join(f"{l}: {total_text(v)}" for l, v in rep["totals"])); self.totals.setVisible(bool(rep["totals"]))
        for b in (self.b_print, self.b_pdf, self.b_share, self.b_xls, self.b_cols): b.setEnabled(True)
        self.b_stmt.setVisible(bool(rep.get("cids")) and self.statement_cb is not None); self.b_stmt.setEnabled(True)
    def pick_columns(self):
        dlg = QDialog(self); dlg.setWindowTitle("ستون‌های گزارش (برای نمایش، چاپ، PDF و Excel)"); lay = QVBoxLayout(dlg)
        cbs = []
        for i, h in enumerate(self.rep["headers"]):
            cb = QCheckBox(h); cb.setChecked(i not in self.hidden); cbs.append(cb); lay.addWidget(cb)
        b = bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); b.accepted.connect(dlg.accept); b.rejected.connect(dlg.reject); lay.addWidget(b)
        if dlg.exec():
            hidden = {i for i, cb in enumerate(cbs) if not cb.isChecked()}
            if len(hidden) >= len(cbs): return warn(self, "ستون‌ها", "حداقل یک ستون باید باقی بماند.")
            self.hidden = hidden; self.render()
    def open_settings(self):
        InvoiceStyleDialog(self).exec()
    def _doc(self):
        st = load_style("report"); return st, make_document(build_report_html(self.view, st), st)
    def do_print(self):
        st, doc = self._doc(); show_preview(self, doc, make_printer(st, self.view["landscape"]), extra=[("اشتراک (ایتا / روبیکا)", self.do_share)])
    def do_share(self):
        st, doc = self._doc(); name = self.view["title"]; land = self.view["landscape"]
        ShareDialog(self, lambda: create_doc_pdf(doc, st, name, land)).exec()
    def do_pdf(self):
        st, doc = self._doc(); save_pdf(self, doc, st, f"{self.view['title']}.pdf", self.view["landscape"])
    def do_excel(self):
        path, _ = QFileDialog.getSaveFileName(self, "ذخیره Excel", f"{self.view['title']}.xlsx", "Excel (*.xlsx)")
        if not path: return
        try: export_report_excel(self.view, path); info(self, "انجام شد", "فایل Excel ذخیره شد.")
        except Exception as e: warn(self, "خطا", str(e))
    def open_statement(self, *_):
        cids = self.rep.get("cids") if self.rep else None; r = self.table.currentRow()
        if cids and self.statement_cb and 0 <= r < len(cids): self.statement_cb(cids[r])

MESSENGERS = {
    "eitaa": ("ایتا", ("eitaa", "ایتا"), "https://web.eitaa.com/"),
    "rubika": ("روبیکا", ("rubika", "روبیکا"), "https://web.rubika.ir/"),
}

def find_messenger(platform):
    """Look for the installed desktop app: Start-Menu / Desktop shortcuts first, then the usual install folders."""
    words = [w.lower() for w in MESSENGERS[platform][1]]
    hit = lambda name: any(w in name.lower() for w in words)
    env = os.environ
    lnk_roots = [os.path.join(env.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
                 os.path.join(env.get("ProgramData", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
                 str(Path.home() / "Desktop"), os.path.join(env.get("PUBLIC", ""), "Desktop")]
    for root in lnk_roots:
        if not root or not os.path.isdir(root): continue
        for dp, dn, fn in os.walk(root):
            if dp[len(root):].count(os.sep) > 2: dn[:] = []; continue
            for f in sorted(fn):
                if f.lower().endswith(".lnk") and hit(f) and "uninstall" not in f.lower():
                    return os.path.join(dp, f)
    exe_roots = [env.get("APPDATA", ""), env.get("LOCALAPPDATA", ""), os.path.join(env.get("LOCALAPPDATA", ""), "Programs"),
                 env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", "")]
    for root in exe_roots:
        if not root or not os.path.isdir(root): continue
        try: entries = sorted(os.listdir(root))
        except OSError: continue
        for e in entries:
            top = os.path.join(root, e)
            if not (hit(e) and os.path.isdir(top)): continue
            for dp, dn, fn in os.walk(top):
                if dp[len(top):].count(os.sep) > 2: dn[:] = []; continue
                for f in sorted(fn):
                    low = f.lower()
                    if low.endswith(".exe") and hit(f) and not low.startswith(("unins", "update", "crash")):
                        return os.path.join(dp, f)
    return ""

def messenger_target(parent, platform, force_pick=False):
    """Saved path of the messenger app; auto-detected the first time, otherwise the user picks it once."""
    key = "messenger_" + platform
    cur = get_pref(key, "")
    if cur and os.path.exists(cur) and not force_pick: return cur
    if not force_pick:
        found = find_messenger(platform)
        if found: set_pref(key, found); return found
    path, _ = QFileDialog.getOpenFileName(parent, f"انتخاب برنامه‌ی {MESSENGERS[platform][0]} (فایل exe یا میانبر)", str(Path.home()),
                                          "Programs (*.exe *.lnk);;All files (*)")
    if path: set_pref(key, path)
    return path or ""

def open_messenger(parent, platform):
    """Open the desktop app. Returns True if the app was started, False if the web version was opened instead."""
    target = messenger_target(parent, platform)
    if target and QDesktopServices.openUrl(QUrl.fromLocalFile(target)): return True
    QDesktopServices.openUrl(QUrl(MESSENGERS[platform][2])); return False

def create_invoice_pdf(iid):
    """PDF copy of the invoice (same paper size, orientation and margins as normal printing)."""
    st, doc = FactorDesk._invoice_doc_static(iid)
    with db() as con:
        no = con.execute("SELECT invoice_no FROM invoices WHERE id=?", (iid,)).fetchone()[0]
    share_dir = APP_DIR / "SharedInvoices"; share_dir.mkdir(parents=True, exist_ok=True)
    path = share_dir / f"فاکتور-{no}.pdf"
    doc.print_(make_printer(st, pdf_path=str(path)))
    if not path.exists() or path.stat().st_size < 100:
        raise RuntimeError("ساخت فایل PDF فاکتور انجام نشد.")
    return path

def create_doc_pdf(doc, st, name, landscape=False):
    """PDF of any QTextDocument (reports, statements ...) in the shared-files folder."""
    safe = re.sub(r'[\\/:*?"<>|]+', "-", str(name)).strip() or "document"
    share_dir = APP_DIR / "SharedInvoices"; share_dir.mkdir(parents=True, exist_ok=True)
    path = share_dir / f"{safe}.pdf"
    doc.print_(make_printer(st, landscape, pdf_path=str(path)))
    if not path.exists() or path.stat().st_size < 100:
        raise RuntimeError("ساخت فایل PDF انجام نشد.")
    return path

class ShareDialog(QDialog):
    """Share any generated PDF: copies the file, opens Eitaa / Rubika desktop; user presses Ctrl+V in a chat."""
    def __init__(self, parent, make_pdf):
        super().__init__(parent); self.make_pdf = make_pdf
        self.setWindowTitle("اشتراک‌گذاری"); self.resize(460, 240)
        lay = QVBoxLayout(self)
        lab = QLabel("یک PDF ساخته و کپی می‌شود و برنامه‌ی ایتا یا روبیکا روی ویندوز باز می‌شود؛ "
                     "در چتِ دلخواه فقط Ctrl+V بزن تا فایل ضمیمه شود.")
        lab.setWordWrap(True); lay.addWidget(lab)
        b1 = QPushButton("ارسال با ایتا"); b2 = QPushButton("ارسال با روبیکا")
        b3 = QPushButton("نمایش فایل PDF در پوشه"); b4 = QPushButton("تعیین یا تغییر مسیر برنامه‌ی ایتا / روبیکا…")
        close = QPushButton("بستن")
        for b in (b1, b2, b3, b4, close): lay.addWidget(b)
        b1.clicked.connect(lambda: self.share("eitaa")); b2.clicked.connect(lambda: self.share("rubika"))
        b3.clicked.connect(self.reveal); b4.clicked.connect(self.pick_app); close.clicked.connect(self.reject)
    def share(self, platform):
        name = MESSENGERS[platform][0]
        try:
            path = self.make_pdf(); copy_file_to_clipboard(path); started = open_messenger(self, platform)
        except Exception as e:
            QMessageBox.warning(self, "خطا در اشتراک‌گذاری", str(e)); return
        if started:
            QMessageBox.information(self, "آماده شد", f"فایل PDF کپی شد و برنامه‌ی {name} باز شد.\nدر {name} چت مورد نظر را باز کن و Ctrl+V بزن.\n\nمسیر فایل:\n{path}")
        else:
            QMessageBox.information(self, "برنامه پیدا نشد", f"برنامه‌ی {name} روی این کامپیوتر پیدا نشد یا باز نشد، پس نسخه‌ی وب باز شد.\nفایل PDF کپی شده است (Ctrl+V) و در این مسیر هم هست:\n{path}\n\nبا دکمه‌ی «تعیین یا تغییر مسیر برنامه» می‌توانی برنامه‌ی نصب‌شده را یک‌بار معرفی کنی.")
    def reveal(self):
        try: reveal_in_folder(self.make_pdf())
        except Exception as e: QMessageBox.warning(self, "خطا", str(e))
    def pick_app(self):
        names = [MESSENGERS[k][0] for k in ("eitaa", "rubika")]
        choice, ok = QInputDialog.getItem(self, "تعیین مسیر برنامه", "کدام برنامه؟", names, 0, False)
        if not ok: return
        platform = "eitaa" if choice == names[0] else "rubika"
        path = messenger_target(self, platform, force_pick=True)
        if path: info(self, "ذخیره شد", f"مسیر برنامه‌ی {choice} ذخیره شد:\n{path}")

def copy_file_to_clipboard(path):
    """Put the file itself on the clipboard (like Explorer's Copy) so Ctrl+V in a chat attaches it."""
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))])
    QApplication.clipboard().setMimeData(mime)

def reveal_in_folder(path):
    if os.name == "nt":
        import subprocess
        subprocess.Popen(["explorer", f"/select,{path}"])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

# ───────────────────────────── Main window ─────────────────────────────
# ───────────── ورود، دسترسی و گزارش فعالیت ─────────────
AUTH = auth.Session(None)      # کاربر واردشده؛ بعد از ورود در main() جایگزین می‌شود
PERM_TABS = {"گزارش‌ها و بدهکاران": "reports", "پیامک": "sms", "پشتیبان و تنظیمات": "settings", "خرید": "purchases", "تأمین‌کنندگان": "purchases"}

def _auth_H():
    return SimpleNamespace(db=db, get_pref=get_pref, set_pref=set_pref, match=match_words,
                           when=lambda iso: f"{iso_to_j(str(iso)[:10])}  {str(iso)[11:16]}")

def audit(action, detail=""):
    auth.audit(_auth_H(), AUTH.username, action, detail)

_DEL_INFO = {"delete_invoice": ("inv_table", "invoices", "حذف فاکتور"), "delete_proforma": ("pf_table", "invoices", "حذف پیش‌فاکتور"),
             "delete_customer": ("cust_table", "customers", "حذف مشتری"), "delete_payment": ("pay_table", "payments", "حذف دریافت"), "delete_return": ("ret_table", "returns", "حذف مرجوعی"),
             "delete_cheque": ("chq_table", "cheques", "حذف چک")}

def ask_reason(parent, title="دلیل حذف", label="دلیل حذف را بنویس (الزامی؛ در تاریخچه ثبت می‌شود):"):
    """دلیل را می‌گیرد (الزامی)؛ None یعنی کاربر انصراف داد."""
    while True:
        text, ok = QInputDialog.getText(parent, title, label)
        if not ok: return None
        if text.strip(): return text.strip()
        QMessageBox.warning(parent, title, "نوشتن دلیل الزامی است.")

def ask_ok_cancel(parent, title, text, ok_text="تأیید", cancel_text="انصراف"):
    """پرسش با دو دکمه‌ی «تأیید» و «انصراف»؛ True یعنی تأیید."""
    m = QMessageBox(parent); m.setWindowTitle(title); m.setText(text); m.setIcon(QMessageBox.Icon.Question)
    b_ok = m.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole); m.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    m.setDefaultButton(b_ok); m.exec()
    return m.clickedButton() is b_ok

def _log_print(ok, iid, err=""):
    try:
        with db() as con:
            r = con.execute("SELECT invoice_no FROM invoices WHERE id=?", (iid,)).fetchone()
            safety.log_print(con, AUTH.username, ok, iid, f"فیش {r[0]}" if r else "", err)
    except Exception: pass

def _guard(perm):
    """دکوراتور: بدون دسترسی `perm` اجرا نمی‌شود؛ حذف‌های موفق در گزارش فعالیت ثبت می‌شوند."""
    def deco(fn):
        params = list(inspect.signature(fn).parameters.values())
        var = any(p.kind == p.VAR_POSITIONAL for p in params); npos = len(params) - 1
        @functools.wraps(fn)
        def w(self, *a, **k):
            if not AUTH.can(perm) or (perm == "delete" and not AUTH.is_admin()):
                return warn(self, "دسترسی ندارید", f"کاربر «{AUTH.display}» اجازه‌ی این کار را ندارد." + (" حذف فقط توسط مدیر ممکن است." if perm == "delete" else " با مدیر هماهنگ کن."))
            args = a if var else a[:max(npos, 0)]
            info_ = _DEL_INFO.get(fn.__name__); before = detail = None
            if info_:
                try:
                    tbl = getattr(self, info_[0]); r = tbl.currentRow()
                    detail = " | ".join(tbl.item(r, c).text() for c in (1, 2, 3) if r >= 0 and tbl.item(r, c))
                    with db() as con: before = con.execute(f"SELECT COUNT(*) FROM {info_[1]}").fetchone()[0]
                except Exception: before = None
            res = fn(self, *args)
            if before is not None:
                try:
                    with db() as con: after = con.execute(f"SELECT COUNT(*) FROM {info_[1]}").fetchone()[0]
                    if after < before: audit(info_[2], detail or "")
                except Exception: pass
            return res
        return w
    return deco

class _ActivityFilter(QObject):
    """آخرین زمان کار با ماوس/کیبورد را نگه می‌دارد (برای قفل خودکار)."""
    def __init__(self, win): super().__init__(win); self.win = win
    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel): self.win._last_act = _time.monotonic()
        return False


def _with_polish(cls):
    orig = cls.__init__
    def init(self, *a, **k):
        orig(self, *a, **k); polish_dialog(self, self.windowTitle())
    cls.__init__ = init
for _c in (PaymentDialog, ChequeDialog, ReturnDialog, CustomerDialog): _with_polish(_c)

class FactorDesk(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("فاکتور یار | مدیریت فاکتور، مشتری و چک"); self.resize(1300, 800)
        self.tabs = QTabWidget(); self.setCentralWidget(self.tabs)
        self.build_invoices_tab(); self.build_proformas_tab(); self.build_customers_tab(); self.build_payments_tab(); self.build_returns_tab(); self.build_cheques_tab(); self.build_purchase_tabs()
        self.build_reports_tab(); self.build_sms_tab(); self.build_settings_tab()
        self.va = None
        if VOICE_ASSISTANT:
            self.assist_btn = QPushButton(); self.va = VoiceAssistant(self); self.assistant = Dictation(self, self.assist_btn, self.voice_command_smart, label="🎙 دستیار صوتی (F9)")
            self.assist_btn.setToolTip(ASSISTANT_TIP_MAIN); self.tabs.setCornerWidget(self.assist_btn, Qt.TopLeftCorner)
            _f9 = QShortcut(QKeySequence("F9"), self); _f9.activated.connect(self.assistant.toggle)
        self.setStatusBar(QStatusBar()); self.statusBar().showMessage("متصل به سرور فروشگاه؛ اطلاعات روی سرور ذخیره می‌شود" if NET_MODE == "client" else f"ذخیره آفلاین اطلاعات در: {DB_PATH}")
        if VOICE_ASSISTANT: self.listen_lbl = QLabel(""); self.statusBar().addPermanentWidget(self.listen_lbl)   # after the status bar is final
        self._locked = False; self._last_act = _time.monotonic(); self._last_bk = _time.monotonic()
        self.user_btn = QToolButton(); self.user_btn.setPopupMode(QToolButton.InstantPopup)
        _m = QMenu(self.user_btn); _m.addAction("🔒 قفل برنامه / تغییر کاربر (Ctrl+L)", self.lock_app); _m.addAction("تغییر رمز من", self.change_my_password)
        self.user_btn.setMenu(_m); self.statusBar().addPermanentWidget(self.user_btn)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.lock_app)
        self.tabs.currentChanged.connect(self._tab_guard); self.apply_permissions()
        QApplication.instance().installEventFilter(_ActivityFilter(self))
        self._bg_timer = QTimer(self); self._bg_timer.timeout.connect(self._background_tick); self._bg_timer.start(30_000)
        self.refresh_all()
        if VOICE_ASSISTANT and get_pref("always_on", "0") == "1": QTimer.singleShot(1500, self.va.start_always_on)
    # ── کاربر، دسترسی، قفل ──
    def apply_permissions(self):
        for i in range(self.tabs.count()):
            perm = PERM_TABS.get(self.tabs.tabText(i)); self.tabs.setTabVisible(i, AUTH.can(perm) if perm else True)
        if not self.tabs.isTabVisible(self.tabs.currentIndex()): self.tabs.setCurrentIndex(0)
        self.user_btn.setText(f"👤 {AUTH.display or 'کاربر'}")
    def _tab_guard(self, idx):
        perm = PERM_TABS.get(self.tabs.tabText(idx))
        if perm and not AUTH.can(perm):
            self.tabs.setCurrentIndex(0); warn(self, "دسترسی ندارید", "این بخش برای کاربر فعلی باز نیست.")
    def lock_app(self, *_):
        global AUTH
        if self._locked: return
        self._locked = True; audit("قفل برنامه"); self.tabs.hide()
        d = auth.LoginDialog(_auth_H(), AUTH.username, locked=True, parent=self)
        if d.exec() and d.user:
            AUTH = auth.Session(d.user); set_pref("last_user", d.user["username"])
            self._locked = False; self._last_act = _time.monotonic(); self.apply_permissions(); self.tabs.show(); self.refresh_all()
        else:
            self._locked = False; self.tabs.show(); self.close()
    def change_my_password(self, *_):
        if AUTH.user and auth.ChangePasswordDialog(_auth_H(), AUTH.user, self).exec(): info(self, "رمز", "رمز عوض شد.")
    def open_users(self, *_):
        if not AUTH.is_admin(): return warn(self, "دسترسی ندارید", "فقط مدیر می‌تواند کاربران را مدیریت کند.")
        auth.UsersDialog(_auth_H(), AUTH.user or {"id": 0, "username": ""}, self).exec()
    def _background_tick(self):
        if self._locked: return
        try: lock_min = int(float(get_pref("auto_lock_min", "0") or 0))
        except Exception: lock_min = 0
        if lock_min > 0 and _time.monotonic() - self._last_act > lock_min * 60: return self.lock_app()
        try: every = int(float(get_pref("backup_every_min", "30") or 30))
        except Exception: every = 30
        if every > 0 and _time.monotonic() - self._last_bk >= every * 60:
            self._last_bk = _time.monotonic()
            try: auto_backup(ignore_pref=True)
            except Exception: pass
    def _maint_H(self):
        return SimpleNamespace(db=db, db_path=DB_PATH, backup_dir=BACKUP_DIR, sqlite_copy=_sqlite_copy, get_pref=get_pref, set_pref=set_pref,
                               auto_backup=auto_backup, iso_to_j=iso_to_j, invoice_rows=invoice_rows, log=audit)
    def open_backup_center(self, *_):
        if NET_MODE == "client": return info(self, "فقط روی سرور", "پشتیبان‌گیری زمان‌بندی‌شده و بازیابی فقط روی کامپیوتر سرور (برنامه‌ی «فاکتور یار سرور») انجام می‌شود.\nبرای گرفتن یک نسخه‌ی دستی از همین‌جا از دکمه‌ی «ذخیره پشتیبان» (مخصوص مدیر) استفاده کن.")
        d = maintenance.BackupDialog(self._maint_H(), self); d.exec()
        if d.restored: QApplication.instance().quit()
    def open_db_check(self, *_):
        if NET_MODE == "client": return info(self, "فقط روی سرور", "بررسی و تعمیر فایل دیتابیس فقط روی کامپیوتر سرور انجام می‌شود.")
        maintenance.CheckDialog(self._maint_H(), self).exec()
    def make_table(self, headers):
        t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers)
        t.setAlternatingRowColors(True); t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.horizontalHeader().setStretchLastSection(True)
        t.verticalHeader().setVisible(False); return t
    def bar(self, layout, widgets):
        bar = QHBoxLayout()
        for w in widgets: bar.addWidget(w)
        layout.addLayout(bar)
    def btn(self, text, fn):
        b = QPushButton(text); b.clicked.connect(fn); return b
    # ── tabs ──
    def build_invoices_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.inv_search = QLineEdit(); self.inv_search.setPlaceholderText("جست‌وجو بر اساس شماره فاکتور یا نام مشتری (در همه‌ی روزها)...")
        self.inv_search.textChanged.connect(self.refresh_invoices)
        self.inv_year = QComboBox(); self.inv_year.addItem("همه سال‌ها", 0)
        self.inv_month = QComboBox(); self.inv_month.addItem("همه ماه‌ها", 0)
        for _i, _m in enumerate(J_MONTHS, 1): self.inv_month.addItem(_m, _i)
        self.inv_day = QComboBox(); self.inv_day.addItem("همه روزها", 0)
        for _d in range(1, 32): self.inv_day.addItem(str(_d), _d)
        _ty, _tm, _td = [int(x) for x in iso_to_j(today_iso()).split("/")]
        self._inv_year_init = _ty; self.inv_month.setCurrentIndex(_tm); self.inv_day.setCurrentIndex(_td)   # پیش‌فرض: فقط فاکتورهای امروز
        for _c in (self.inv_year, self.inv_month, self.inv_day): _c.currentIndexChanged.connect(self.refresh_invoices)
        _reset = self.btn("همه فاکتورها", lambda: [c.setCurrentIndex(0) for c in (self.inv_year, self.inv_month, self.inv_day)])
        _today_btn = self.btn("امروز", self.inv_go_today)
        self.overdue_lbl = QLabel(""); self.overdue_lbl.setStyleSheet("color:#c62828;font-weight:bold;")
        self.bar(l, [QLabel("طبقه‌بندی:"), QLabel("سال"), self.inv_year, QLabel("ماه"), self.inv_month, QLabel("روز"), self.inv_day, _today_btn, _reset, self.overdue_lbl])
        self.bar(l, [self.inv_search, self.btn("＋ فاکتور جدید", self.new_invoice), self.btn("ویرایش", self.edit_invoice),
                     self.btn("مشاهده / چاپ فاکتور", self.preview_invoice), self.btn("چاپ فیش پرینتر", self.print_receipt),
                     self.btn("PDF فاکتور", self.invoice_pdf), self.btn("اشتراک فاکتور", self.share_invoice), self.btn("📨 پیامک فاکتور", self.sms_invoice),
                     self.btn("تنظیمات چاپ", self.edit_style), self.btn("خروجی Excel", self.export_invoices_excel), self.btn("↩ برگشت به پیش‌فاکتور", self.revert_to_proforma), self.btn("حذف", self.delete_invoice)])
        self.inv_table = self.make_table(["شناسه", "شماره", "تاریخ", "مشتری", "نوع", "مبلغ کل", "پرداخت اولیه", "دریافت‌های بعدی", "مانده فاکتور", "سررسید", "ساعت ثبت"])
        self.inv_table.setColumnHidden(0, True); self.inv_table.doubleClicked.connect(self.edit_invoice); l.addWidget(self.inv_table)
        self.inv_table.horizontalHeader().moveSection(9, 3)      # سررسید کنار تاریخ (فقط ترتیب نمایش)
        self.tabs.addTab(w, "فاکتورها")
    def build_customers_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.cust_search = QLineEdit(); self.cust_search.setPlaceholderText("جست‌وجوی نام، تلفن یا آدرس مشتری...")
        self.cust_search.textChanged.connect(self.refresh_customers)
        self.bar(l, [self.cust_search, self.btn("＋ مشتری جدید", self.new_customer), self.btn("ویرایش", self.edit_customer),
                     self.btn("صورت‌حساب مشتری", self.customer_report), self.btn("📨 پیامک مانده", self.sms_customer), self.btn("حذف مشتری", self.delete_customer)])
        self.cust_table = self.make_table(["شناسه", "نام مشتری", "تلفن", "آدرس", "تعداد فاکتور", "جمع فاکتورها", "دریافتی‌ها", "مانده بدهی"])
        self.cust_table.setColumnHidden(0, True); self.cust_table.doubleClicked.connect(self.customer_report); l.addWidget(self.cust_table)
        self.tabs.addTab(w, "مشتریان")
    def build_payments_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.pay_search = QLineEdit(); self.pay_search.setPlaceholderText("جست‌وجو در دریافت‌ها...")
        self.pay_search.textChanged.connect(self.refresh_payments)
        self.bar(l, [self.pay_search, self.btn("＋ ثبت دریافت", self.new_payment), self.btn("ویرایش", self.edit_payment),
                     self.btn("📨 پیامک رسید", self.sms_payment), self.btn("حذف", self.delete_payment), self.btn("خروجی Excel", lambda: self.export_table(self.pay_table, "دریافت‌ها"))])
        self.pay_table = self.make_table(["شناسه", "تاریخ", "مشتری", "شماره فاکتور", "مبلغ", "روش", "شماره پیگیری", "توضیحات", "تخفیف", "ساعت ثبت"])
        self.pay_table.setColumnHidden(0, True); self.pay_table.doubleClicked.connect(self.edit_payment); l.addWidget(self.pay_table)
        self.tabs.addTab(w, "دریافت‌ها")
    def build_purchase_tabs(self):
        """تب‌های «خرید» و «تأمین‌کنندگان» (منطق: purchases.py ؛ رابط: purchase_ui.py)."""
        import purchase_ui
        from types import SimpleNamespace as _NS
        purchase_ui.bind(_NS(db=db, JalaliDateEdit=JalaliDateEdit, need_date=need_date, make_searchable_combo=make_searchable_combo, bbox=bbox, money=money, iso_to_j=iso_to_j,
                             parse_num=parse_num, fmt_qty=fmt_qty, warn=warn, info=info, ask=ask, ask_reason=ask_reason, table_item=table_item, money_spin=money_spin,
                             with_words=with_words, today_iso=today_iso, audit=audit, can=lambda p: AUTH.can(p), BarcodeSniffer=_BarcodeSniffer, export_table=self.export_table,
                             show_preview=show_preview, norm_key=norm_key, now_iso=lambda: datetime.now().isoformat(timespec="seconds")))
        try:
            with db() as con: purchases.ensure_tables(con)          # اگر سرور قدیمی‌تر بود، جدول‌های خرید همین‌جا ساخته می‌شود (فقط CREATE IF NOT EXISTS)
            self.purchases_panel = purchase_ui.PurchasesPanel(); self.suppliers_panel = purchase_ui.SuppliersPanel()
            self.purchases_panel.on_change = self.suppliers_panel.refresh; self.suppliers_panel.on_change = self.purchases_panel.refresh
            self.tabs.addTab(self.purchases_panel, "خرید"); self.tabs.addTab(self.suppliers_panel, "تأمین‌کنندگان")
        except Exception as e:
            warn(self, "بخش خرید", f"بخش خرید بارگذاری نشد:\n{e}\nبرنامه‌ی سرور و کلاینت باید هر دو از یک نسخه باشند؛ سرور را هم به آخرین نسخه به‌روز کن.")
    def build_returns_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.ret_search = QLineEdit(); self.ret_search.setPlaceholderText("جست‌وجو در مرجوعی‌ها...")
        self.ret_search.textChanged.connect(self.refresh_returns)
        self.bar(l, [self.ret_search, self.btn("＋ ثبت مرجوعی", self.new_return), self.btn("ویرایش", self.edit_return), self.btn("📨 پیامک مرجوعی", self.sms_return),
                     self.btn("حذف", self.delete_return), self.btn("خروجی Excel", lambda: self.export_table(self.ret_table, "مرجوعی‌ها"))])
        self.ret_table = self.make_table(["شناسه", "شماره", "تاریخ", "مشتری", "فاکتور مرتبط", "جمع مرجوعی", "توضیحات", "ساعت ثبت"])
        self.ret_table.setColumnHidden(0, True); self.ret_table.doubleClicked.connect(self.edit_return); l.addWidget(self.ret_table)
        self.tabs.addTab(w, "مرجوعی")
    def build_cheques_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.chq_search = QLineEdit(); self.chq_search.setPlaceholderText("جست‌وجوی شماره چک، مشتری یا بانک...")
        self.chq_search.textChanged.connect(self.refresh_cheques)
        self.bar(l, [self.chq_search, self.btn("＋ ثبت چک", self.new_cheque), self.btn("ویرایش", self.edit_cheque),
                     self.btn("تغییر وضعیت", self.change_cheque_status), self.btn("📨 پیامک چک", self.sms_cheque), self.btn("حذف", self.delete_cheque),
                     self.btn("خروجی Excel", lambda: self.export_table(self.chq_table, "چک‌ها"))])
        self.chq_table = self.make_table(["شناسه", "مشتری", "فاکتور", "شماره چک", "بانک", "صادرکننده", "مبلغ", "سررسید", "وضعیت", "توضیحات", "تاریخ و ساعت ثبت"])
        self.chq_table.setColumnHidden(0, True); self.chq_table.doubleClicked.connect(self.edit_cheque); l.addWidget(self.chq_table)
        self.tabs.addTab(w, "چک‌ها")
    def build_reports_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.rep_type = QComboBox()
        for key, label, _ in REPORTS: self.rep_type.addItem(label, key)
        self.rep_cust = make_searchable_combo(QComboBox()); self.rep_from = JalaliDateEdit(allow_empty=True); self.rep_to = JalaliDateEdit(allow_empty=True)
        self.rep_type.currentIndexChanged.connect(self.rep_type_changed)
        row = QHBoxLayout()
        for lab, wd in [("نوع گزارش:", self.rep_type), ("مشتری:", self.rep_cust), ("از تاریخ:", self.rep_from), ("تا تاریخ:", self.rep_to)]:
            row.addWidget(QLabel(lab)); row.addWidget(wd)
        row.addWidget(self.btn("نمایش گزارش", self.run_report)); l.addLayout(row)
        self.rep_panel = ReportPanel(statement_cb=self.show_statement); l.addWidget(self.rep_panel)
        self.tabs.addTab(w, "گزارش‌ها و بدهکاران")
    def build_sms_tab(self):
        H = SimpleNamespace(db=db, customer_figures=customer_figures, invoice_rows=invoice_rows, money=money, strip_bidi=strip_bidi,
                            iso_to_j=iso_to_j, today_j=today_j, today_iso=today_iso, get_pref=get_pref, set_pref=set_pref, load_style=load_style, match=match_words)
        self.tabs.addTab(barcode_print.BarcodePanel(db, self), "چاپ بارکد")
        self.sms = sms_panel.SmsPanel(H); self.tabs.addTab(self.sms, "پیامک")
        self.tabs.currentChanged.connect(lambda _i: self.sms.refresh() if self.tabs.currentWidget() is self.sms else None)
    def open_kardex(self, *_):
        H = SimpleNamespace(db=db, norm_key=norm_key, parse_num=parse_num, match=match_words, DateEdit=JalaliDateEdit, iso_to_j=iso_to_j,
                            strip_bidi=strip_bidi, money=money)
        kardex.KardexDialog(H, self).exec()
    def sms_invoice(self, *_):
        iid = self.selected_id(self.inv_table)
        if not iid: return warn(self, "انتخاب فاکتور", "ابتدا یک فاکتور انتخاب کن.")
        self.tabs.setCurrentWidget(self.sms); self.sms.focus(sms_panel.KIND_INVOICE, iid)
    def _sms_doc(self, table, kind, what):
        rid = self.selected_id(table)
        if not rid: return warn(self, f"انتخاب {what}", f"ابتدا یک {what} انتخاب کن.")
        self.tabs.setCurrentWidget(self.sms); self.sms.focus(kind, rid)
    def sms_payment(self, *_): self._sms_doc(self.pay_table, sms_panel.KIND_PAYMENT, "دریافت")
    def sms_cheque(self, *_): self._sms_doc(self.chq_table, sms_panel.KIND_CHEQUE, "چک")
    def sms_return(self, *_): self._sms_doc(self.ret_table, sms_panel.KIND_RETURN, "مرجوعی")
    def sms_customer(self, *_):
        cid = self.selected_id(self.cust_table)
        if not cid: return warn(self, "انتخاب مشتری", "ابتدا یک مشتری انتخاب کن.")
        self.tabs.setCurrentWidget(self.sms); self.sms.focus(sms_panel.KIND_TOTAL, cid)
    def build_settings_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        l.addWidget(QLabel(f"مسیر پایگاه داده:\n{DB_PATH}"))
        self.bar(l, [self.btn("تنظیمات چاپ و فیش پرینتر", self.edit_style), self.btn("پشتیبان‌گیری", self.backup_db), self.btn("بازیابی پشتیبان", self.restore_db), self.btn("🗑 سطل بازیافت و تاریخچه", self.open_recycle_bin)])
        self.chk_backup = QCheckBox("پشتیبان‌گیری خودکار با هر بار بستن برنامه"); self.chk_backup.setChecked(get_pref("backup_on_exit", "1") == "1")
        self.chk_backup.toggled.connect(lambda v: set_pref("backup_on_exit", "1" if v else "0"))
        self.keep_spin = QSpinBox(); self.keep_spin.setRange(3, 365)
        try: self.keep_spin.setValue(int(float(get_pref("backup_keep", "30") or 30)))
        except Exception: self.keep_spin.setValue(30)
        self.keep_spin.valueChanged.connect(lambda v: set_pref("backup_keep", v))
        brow = QHBoxLayout(); brow.addWidget(self.chk_backup); brow.addWidget(QLabel("تعداد نسخه‌های نگهداری‌شده:")); brow.addWidget(self.keep_spin)
        brow.addWidget(self.btn("باز کردن پوشه پشتیبان‌ها", self.open_backup_dir)); brow.addStretch(); l.addLayout(brow)
        l.addWidget(QLabel(f"پشتیبان‌های خودکار در این پوشه ذخیره می‌شوند:\n{BACKUP_DIR}"))
        self.def_cust = make_searchable_combo(QComboBox()); self.def_kind = QComboBox(); self.def_kind.addItems(["نقدی", "اعتباری", "چکی", "ترکیبی"])
        self.def_kind.setCurrentText(get_pref("default_kind", "نقدی"))
        self.def_kind.currentTextChanged.connect(lambda t: set_pref("default_kind", t))
        self.def_cust.currentIndexChanged.connect(lambda _i: set_pref("default_customer", self.def_cust.currentData() or ""))
        drow = QHBoxLayout(); drow.addWidget(QLabel("مشتری پیش‌فرض فاکتور جدید:")); drow.addWidget(self.def_cust)
        drow.addWidget(QLabel("نوع پرداخت پیش‌فرض:")); drow.addWidget(self.def_kind); drow.addStretch(); l.addLayout(drow)
        self.stt_lang = QComboBox()
        for _t, _v in (("فارسی", "fa-IR"), ("English", "en-US"), ("العربية", "ar-SA"), ("Türkçe", "tr-TR"), ("Azərbaycanca", "az-AZ")): self.stt_lang.addItem(_t, _v)
        self.stt_lang.setCurrentIndex(max(0, self.stt_lang.findData(get_pref("stt_lang", "fa-IR"))))
        self.stt_lang.currentIndexChanged.connect(lambda _i: set_pref("stt_lang", self.stt_lang.currentData()))
        self.stt_max = QSpinBox(); self.stt_max.setRange(5, 60); self.stt_max.setSuffix(" ثانیه")
        try: self.stt_max.setValue(int(float(get_pref("stt_max", "30") or 30)))
        except Exception: self.stt_max.setValue(30)
        self.stt_max.valueChanged.connect(lambda v: set_pref("stt_max", v))
        self.stt_mic = QComboBox(); self.stt_mic.addItem("میکروفون پیش‌فرض ویندوز", "")
        try:
            from PySide6.QtMultimedia import QMediaDevices
            for _d in QMediaDevices.audioInputs(): self.stt_mic.addItem(_d.description(), _d.description())
        except Exception: pass
        self.stt_mic.setCurrentIndex(max(0, self.stt_mic.findData(get_pref("stt_mic", ""))))
        self.stt_mic.currentIndexChanged.connect(lambda _i: set_pref("stt_mic", self.stt_mic.currentData() or ""))
        self.stt_key = QLineEdit(get_pref("google_key", "")); self.stt_key.setPlaceholderText("کلید گوگل (اختیاری؛ خالی = کلید عمومی)")
        self.stt_key.editingFinished.connect(lambda: set_pref("google_key", self.stt_key.text().strip()))
        srow = QHBoxLayout(); srow.addWidget(QLabel("تایپ با صدا (گوگل) — زبان:")); srow.addWidget(self.stt_lang)
        srow.addWidget(QLabel("حداکثر ضبط:")); srow.addWidget(self.stt_max); srow.addStretch(); l.addLayout(srow)
        srow2 = QHBoxLayout(); srow2.addWidget(QLabel("میکروفون:")); srow2.addWidget(self.stt_mic); srow2.addWidget(QLabel("کلید:")); srow2.addWidget(self.stt_key); l.addLayout(srow2)
        self.stt_auto = QCheckBox("توقف خودکار ضبط بعد از سکوت (دستیار و تایپ با صدا)"); self.stt_auto.setChecked(get_pref("stt_auto", "1") == "1")
        self.stt_auto.toggled.connect(lambda v: set_pref("stt_auto", "1" if v else "0")); l.addWidget(self.stt_auto)
        if VOICE_ASSISTANT: self.bar(l, [self.btn("🧠 آموزش و تنظیمات دستیار هوشمند (هوش مصنوعی، گوش‌دادن همیشگی، قفل صدا)…", self.open_assistant_settings)])
        self.bar(l, [self.btn("📦 کاردکس کالا (گردش و موجودی کالاها)", self.open_kardex), self.btn("👥 کاربران و دسترسی‌ها", self.open_users)])
        self.bar(l, [self.btn("☁ پشتیبان ابری و بازیابی", self.open_backup_center), self.btn("🩺 بررسی و تعمیر دیتابیس", self.open_db_check)])
        self.mem_label = QLabel(""); l.addWidget(self.mem_label)
        self.bar(l, [self.btn("بازسازی حافظه کالاها از فاکتورهای قبلی", self.rebuild_memory), self.btn("پاک‌کردن حافظه کالاها", self.clear_memory)])
        self.update_mem_label()
        l.addWidget(QLabel("توصیه: علاوه بر پشتیبان خودکار، گاهی یک نسخه را روی فلش یا فضای ابری هم نگه دار.")); l.addStretch()
        self.tabs.addTab(w, "پشتیبان و تنظیمات")
    # ── helpers ──
    def selected_id(self, table):
        r = table.currentRow()
        if r < 0: return None
        it = table.item(r, 0); return int(it.text()) if it else None
    def refresh_default_customer_combo(self):
        if not hasattr(self, "def_cust"): return
        cur = get_pref("default_customer", "")
        self.def_cust.blockSignals(True); self.def_cust.clear(); self.def_cust.addItem("بدون پیش‌فرض", None)
        with db() as con:
            for c in con.execute("SELECT id,name FROM customers ORDER BY name"): self.def_cust.addItem(c["name"], c["id"])
        self.def_cust.setCurrentIndex(max(0, self.def_cust.findData(int(cur)) if cur.isdigit() else 0)); self.def_cust.blockSignals(False)
    def refresh_all(self):
        self.refresh_default_customer_combo(); self.refresh_invoices(); self.refresh_proformas(); self.refresh_customers(); self.refresh_payments(); self.refresh_returns(); self.refresh_cheques(); self.refresh_report_customers()
        if hasattr(self, "sms"): self.sms.refresh()
    def refresh_report_customers(self):
        cur = self.rep_cust.currentData(); self.rep_cust.clear(); self.rep_cust.addItem("همه مشتریان", None)
        with db() as con:
            for c in con.execute("SELECT id,name FROM customers ORDER BY name"): self.rep_cust.addItem(c["name"], c["id"])
        self.rep_cust.setCurrentIndex(max(0, self.rep_cust.findData(cur)))
        self.rep_type_changed()
    def rep_type_changed(self, *_):
        flags = REPORTS[self.rep_type.currentIndex()][2]
        self.rep_cust.setEnabled(flags["cust"]); self.rep_from.setEnabled(flags["dates"]); self.rep_to.setEnabled(flags["dates"])
    def run_report(self):
        key = self.rep_type.currentData(); flags = REPORTS[self.rep_type.currentIndex()][2]
        cid = self.rep_cust.currentData() if flags["cust"] else None
        d1 = self.rep_from.iso() if flags["dates"] else ""; d2 = self.rep_to.iso() if flags["dates"] else ""
        if d1 is None or d2 is None: return warn(self, "تاریخ نامعتبر", "تاریخ را شمسی و درست وارد کن (مثال: ۱۴۰۵/۰۷/۲۰) یا خالی بگذار.")
        if key == "statement" and not cid: return warn(self, "انتخاب مشتری", "برای صورت‌حساب، یک مشتری انتخاب کن.")
        self.rep_panel.set_report(build_report(key, cid, d1, d2))
    def show_statement(self, cid):
        dlg = QDialog(self); dlg.setWindowTitle("صورت‌حساب مشتری"); dlg.resize(1050, 680); lay = QVBoxLayout(dlg)
        panel = ReportPanel(); panel.set_report(build_report("statement", cid)); lay.addWidget(panel); dlg.exec()
    # ── refresh tables ──
    def refresh_invoices(self):
        q = self.inv_search.text().strip() if hasattr(self, "inv_search") else ""
        with db() as con: invs = invoice_rows(con)
        today = today_iso()
        is_over = lambda i: i["remain"] > 0.5 and bool(i["due_date"]) and i["due_date"] < today
        if hasattr(self, "overdue_lbl"):
            n_over = sum(1 for i in invs if is_over(i))
            self.overdue_lbl.setText(f"⚠ {n_over} فاکتور سررسید گذشته" if n_over else "")
        invs = [i for i in invs if match_words(q, i["invoice_no"], i["cname"], i["description"])]
        invs.sort(key=lambda i: (i["invoice_date"], i["id"]), reverse=True)
        if hasattr(self, "inv_year"):
            def _jparts(i):
                try: return [int(x) for x in iso_to_j(i["invoice_date"]).split("/")]
                except Exception: return [0, 0, 0]
            _ty, _tm, _td = [int(x) for x in iso_to_j(today_iso()).split("/")]
            years = sorted({_jparts(i)[0] for i in invs if _jparts(i)[0]} | {_ty}, reverse=True)
            cur = getattr(self, "_inv_year_init", None) or (self.inv_year.currentData() or 0)
            self._inv_year_init = None
            self.inv_year.blockSignals(True); self.inv_year.clear(); self.inv_year.addItem("همه سال‌ها", 0)
            for y in years: self.inv_year.addItem(str(y), y)
            self.inv_year.setCurrentIndex(max(0, self.inv_year.findData(cur))); self.inv_year.blockSignals(False)
            fy, fm, fd = self.inv_year.currentData() or 0, self.inv_month.currentData() or 0, self.inv_day.currentData() or 0
            _sel_today = (fy, fm, fd) == (_ty, _tm, _td)
            if not q:      # با جست‌وجو همه‌ی روزها دیده می‌شود؛ بدون جست‌وجو فقط روز انتخاب‌شده (فاکتورِ ثبت‌شده‌ی امروز هم در «امروز» می‌آید)
                invs = [i for i in invs if ((not fy or _jparts(i)[0] == fy) and (not fm or _jparts(i)[1] == fm) and (not fd or _jparts(i)[2] == fd))
                        or (_sel_today and str(i.get("created_at") or "")[:10] == today)]
        self.inv_table.setRowCount(0)
        for i in invs:
            r = self.inv_table.rowCount(); self.inv_table.insertRow(r)
            vals = [i["id"], i["invoice_no"], show_date(i["invoice_date"]), i["cname"], i["kind"], money(i["total"]), money(i["paid_initial"]),
                    money(i["linked"]), money(i["remain"]), show_date(i["due_date"]), show_time(i.get("created_at"))]
            for c, v in enumerate(vals):
                it = table_item(v); self.inv_table.setItem(r, c, it)
                if c != 8: it.setForeground(QColor("#1b7f3b"))                       # فاکتور نهایی = سبز
            if i["remain"] > 0.5:                                                        # مانده‌ی بدهکار = قرمز؛ مانده‌ی صفر = بی‌رنگ
                bold = QFont(); bold.setBold(True); it = self.inv_table.item(r, 8); it.setForeground(QColor("#c62828")); it.setFont(bold)
            if is_over(i):
                bold = QFont(); bold.setBold(True); it = self.inv_table.item(r, 9); it.setForeground(QColor("#c62828")); it.setFont(bold)
    def refresh_customers(self):
        q = self.cust_search.text().strip() if hasattr(self, "cust_search") else ""
        with db() as con: figs = list(customer_figures(con).values())
        self.cust_table.setRowCount(0)
        for f in figs:
            if q and not match_words(q, f["name"], f["phone"], f["address"]): continue
            r = self.cust_table.rowCount(); self.cust_table.insertRow(r)
            for j, v in enumerate([f["id"], f["name"], f["phone"], f["address"], f["n"], money(f["total"]), money(f["received"]), money(f["balance"])]):
                it = table_item(v); self.cust_table.setItem(r, j, it)
                if j == 7 and f["balance"] > 0.5: bold = QFont(); bold.setBold(True); it.setForeground(QColor("#c62828")); it.setFont(bold)      # بدهکار = قرمز، صفر = بی‌رنگ
    def refresh_payments(self):
        q = self.pay_search.text().strip() if hasattr(self, "pay_search") else ""
        with db() as con:
            rows = con.execute("""SELECT p.*,c.name customer_name,i.invoice_no FROM payments p JOIN customers c ON c.id=p.customer_id
                LEFT JOIN invoices i ON i.id=p.invoice_id
                ORDER BY p.payment_date DESC,p.id DESC""").fetchall()
        rows = [x for x in rows if match_words(q, x["customer_name"], x["reference"], x["invoice_no"], x["notes"], x["method"])]
        self.pay_table.setRowCount(0)
        for x in rows:
            r = self.pay_table.rowCount(); self.pay_table.insertRow(r)
            for j, v in enumerate([x["id"], show_date(x["payment_date"]), x["customer_name"], x["invoice_no"] or "", money(x["amount"]), x["method"], x["reference"], x["notes"], money(x["discount"] or 0), show_time(x["created_at"])]):
                self.pay_table.setItem(r, j, table_item(v))
    def refresh_returns(self):
        q = self.ret_search.text().strip() if hasattr(self, "ret_search") else ""
        with db() as con:
            rows = con.execute("""SELECT r.*, c.name customer_name, i.invoice_no,
                COALESCE((SELECT SUM(quantity*unit_price) FROM return_items WHERE return_id=r.id),0) total
                FROM returns r JOIN customers c ON c.id=r.customer_id LEFT JOIN invoices i ON i.id=r.invoice_id
                ORDER BY r.return_date DESC, r.id DESC""").fetchall()
        rows = [x for x in rows if match_words(q, x["customer_name"], x["return_no"], x["invoice_no"], x["notes"])]
        self.ret_table.setRowCount(0)
        for x in rows:
            r = self.ret_table.rowCount(); self.ret_table.insertRow(r)
            for j, v in enumerate([x["id"], x["return_no"], show_date(x["return_date"]), x["customer_name"], x["invoice_no"] or "", money(x["total"]), x["notes"], show_time(x["created_at"])]):
                self.ret_table.setItem(r, j, table_item(v))
    def new_return(self):
        with db() as con:
            if not con.execute("SELECT 1 FROM customers LIMIT 1").fetchone(): return warn(self, "ابتدا مشتری بساز", "اول مشتری ثبت کن.")
        d = ReturnDialog(self)
        if d.exec(): self.refresh_all()
    def edit_return(self, *_):
        rid = self.selected_id(self.ret_table)
        if not rid: return warn(self, "انتخاب مرجوعی", "ابتدا یک مرجوعی انتخاب کن.")
        d = ReturnDialog(self, rid)
        if d.exec(): self.refresh_all()
    def delete_return(self):
        rid = self.selected_id(self.ret_table)
        if not rid: return warn(self, "انتخاب مرجوعی", "ابتدا یک مرجوعی انتخاب کن.")
        if ask(self, "تأیید حذف", "این مرجوعی حذف شود؟ مانده حساب مشتری دوباره محاسبه می‌شود. (در سطل بازیافت می‌ماند)"):
            rsn = ask_reason(self)
            if rsn is None: return
            with db() as con:
                safety.archive_delete(con, AUTH.username, "return", rid, rsn)
                con.execute("DELETE FROM return_items WHERE return_id=?", (rid,)); con.execute("DELETE FROM returns WHERE id=?", (rid,))
            self.refresh_all()
    def refresh_cheques(self):
        q = self.chq_search.text().strip() if hasattr(self, "chq_search") else ""
        with db() as con:
            rows = con.execute("""SELECT ch.*,c.name customer_name,i.invoice_no FROM cheques ch JOIN customers c ON c.id=ch.customer_id
                LEFT JOIN invoices i ON i.id=ch.invoice_id ORDER BY ch.due_date""").fetchall()
        rows = [x for x in rows if match_words(q, x["customer_name"], x["cheque_no"], x["bank"], x["issuer"], x["invoice_no"], x["notes"])]
        self.chq_table.setRowCount(0)
        for x in rows:
            r = self.chq_table.rowCount(); self.chq_table.insertRow(r)
            for j, v in enumerate([x["id"], x["customer_name"], x["invoice_no"] or "", x["cheque_no"], x["bank"], x["issuer"], money(x["amount"]),
                                   show_date(x["due_date"]), x["status"], x["notes"], (x["created_at"] or "")[:10].replace("-", "/") + " " + show_time(x["created_at"])]):
                self.chq_table.setItem(r, j, table_item(v))
            if x["due_date"] < today_iso() and x["status"] not in CHQ_CLOSED:
                bold = QFont(); bold.setBold(True)
                for j in (7, 8):
                    it = self.chq_table.item(r, j); it.setForeground(QColor("#c62828")); it.setFont(bold)
    # ── customers ──
    def new_customer(self):
        d = CustomerDialog(self)
        if d.exec():
            with db() as con: con.execute("INSERT INTO customers(name,phone,address,notes,created_at) VALUES(?,?,?,?,?)", (*d.values(), datetime.now().isoformat(timespec="seconds")))
            self.refresh_all()
    def edit_customer(self):
        cid = self.selected_id(self.cust_table)
        if not cid: return warn(self, "انتخاب مشتری", "ابتدا یک مشتری انتخاب کن.")
        with db() as con: data = con.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        d = CustomerDialog(self, data)
        if d.exec():
            with db() as con: con.execute("UPDATE customers SET name=?,phone=?,address=?,notes=? WHERE id=?", (*d.values(), cid))
            self.refresh_all()
    def delete_customer(self):
        cid = self.selected_id(self.cust_table)
        if not cid: return warn(self, "انتخاب مشتری", "ابتدا یک مشتری انتخاب کن.")
        with db() as con:
            related = con.execute("SELECT (SELECT COUNT(*) FROM invoices WHERE customer_id=?)+(SELECT COUNT(*) FROM payments WHERE customer_id=?)+(SELECT COUNT(*) FROM cheques WHERE customer_id=?)+(SELECT COUNT(*) FROM returns WHERE customer_id=?)", (cid, cid, cid, cid)).fetchone()[0]
            if related: return warn(self, "امکان حذف نیست", "این مشتری فاکتور، دریافت یا چک مرتبط دارد. برای حفظ سوابق، حذف آن مجاز نیست.")
            if ask(self, "تأیید", "مشتری حذف شود؟ (در سطل بازیافت می‌ماند)"):
                rsn = ask_reason(self)
                if rsn is None: return
                safety.archive_delete(con, AUTH.username, "customer", cid, rsn)
                con.execute("DELETE FROM customers WHERE id=?", (cid,))
        self.refresh_all()
    def customer_report(self, *_):
        cid = self.selected_id(self.cust_table)
        if not cid: return warn(self, "انتخاب مشتری", "ابتدا یک مشتری انتخاب کن.")
        self.show_statement(cid)
    # ── invoices ──
    def _voice_customer_pick(self, guess, allow_create=True):
        """Customer id for a spoken name; None = no name spoken; -1 = cancelled / not found."""
        if not guess: return None
        with db() as con: customers = [(r["id"], r["name"]) for r in con.execute("SELECT id,name FROM customers")]
        res = resolve_customer(guess, customers)
        if res["status"] == "ok": return res["id"]
        close = [(1.0, c, n) for c, n in res.get("candidates", [])]
        NEW = f"➕ مشتری جدید: «{guess}»"
        options = [r[2] for r in close] + ([NEW] if allow_create else [])
        if not options: warn(self, "مشتری پیدا نشد", f"مشتری «{guess}» پیدا نشد."); return -1
        choice, ok = QInputDialog.getItem(self, "کدام مشتری؟", f"شنیدم: «{guess}»", options, 0, False)
        if not ok: return -1
        if choice == NEW:
            with db() as con:
                cid = con.execute("INSERT INTO customers(name,created_at) VALUES(?,?)", (guess, datetime.now().isoformat(timespec="seconds"))).lastrowid
            self.refresh_all(); return cid
        cid2 = next(r[1] for r in close if r[2] == choice); learn_alias("customer", guess, choice); return cid2
    def show_balance(self, cid):
        with db() as con: f = customer_figures(con)[cid]
        kind = "بدهکار" if f["balance"] > 0.5 else "بستانکار" if f["balance"] < -0.5 else "تسویه"
        extra = f"\nسررسید گذشته: {money(f['overdue'])} تومان" if f["overdue"] > 0.5 else ""
        info(self, "مانده حساب", f"«{f['name']}»: {kind} — {money(abs(f['balance']))} تومان\nتعداد فاکتور: {f['n']}{extra}")
    def voice_command_smart(self, text):
        if not self.va.busy and self.va.ai_enabled(): self.va.handle_text(text, MainHost(self))
        else: self.voice_command(text)
    def update_listen_label(self):
        word = (get_pref("wake_word", "دستیار") or "دستیار").split("،")[0].strip()
        try:
            self.listen_lbl.setText(f"🎧 گوش می‌دهد — بگو «{word}»" if self.va.listening() else "")
        except RuntimeError:       # label was deleted together with an old status bar: recreate it
            self.listen_lbl = QLabel(""); self.statusBar().addPermanentWidget(self.listen_lbl)
            self.listen_lbl.setText(f"🎧 گوش می‌دهد — بگو «{word}»" if self.va.listening() else "")
    def open_assistant_settings(self): AssistantSettingsDialog(self).exec()
    def voice_command(self, text):
        """Main voice assistant: open invoices / proformas for a customer, balances, statements, go to a tab."""
        cmd = parse_main_command(text); act = cmd["action"]
        self.statusBar().showMessage(f"🎙 «{text}»", 10000)
        if act == "goto":
            names = dict(invoices="فاکتورها", proformas="پیش فاکتورها", customers="مشتریان", payments="دریافت ها",
                         cheques="چک ها", reports="گزارش ها و بدهکاران", settings="پشتیبان و تنظیمات")
            for i in range(self.tabs.count()):
                if norm_key(self.tabs.tabText(i)) == names[cmd["tab"]]: self.tabs.setCurrentIndex(i); break
            return
        if act in ("new_invoice", "new_proforma"):
            cid = self._voice_customer_pick(cmd["customer"], True)
            if cid == -1: return
            self._voice_customer = cid
            self.new_invoice() if act == "new_invoice" else self.new_proforma()
            self._voice_customer = None; return
        if act in ("balance", "statement"):
            if not cmd["customer"]: return info(self, "نام مشتری", "نام مشتری را هم بگو؛ مثلاً «مانده حساب علی رضایی».")
            cid = self._voice_customer_pick(cmd["customer"], False)
            if cid in (None, -1): return
            if act == "statement": return self.show_statement(cid)
            return self.show_balance(cid)
        info(self, "دستیار صوتی", f"متوجه نشدم: «{text}»\n\n" + ASSISTANT_TIP_MAIN)
    def inv_go_today(self):
        y, m, d = [int(x) for x in iso_to_j(today_iso()).split("/")]
        for c, v in ((self.inv_year, y), (self.inv_month, m), (self.inv_day, d)):
            c.blockSignals(True); c.setCurrentIndex(max(0, c.findData(v))); c.blockSignals(False)
        self.refresh_invoices()
    def _offer_sms(self, iid):
        """بعد از ثبت فاکتور می‌پرسد پیامک فاکتور برای مشتری ارسال شود؟ (تأیید / انصراف). اگر پنل پیامک آماده نیست (دسترسی، کلید API، موبایل) سؤالی نمی‌پرسد."""
        try:
            if not AUTH.can("sms") or not hasattr(self, "sms") or not get_pref("sms_api_key", "").strip(): return
            with db() as con:
                r = con.execute("SELECT c.phone FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=? AND COALESCE(i.is_proforma,0)=0", (iid,)).fetchone()
            if not r or not sms_panel.norm_mobile(r["phone"] or ""): return
            if ask_ok_cancel(self, "ارسال پیامک", "فاکتور ثبت شد.\nپیامک فاکتور برای مشتری ارسال شود؟", "تأیید و ارسال", "انصراف"):
                self.sms.quick_invoice(iid)
        except Exception:
            pass
    def revert_to_proforma(self):
        iid = self.selected_id(self.inv_table)
        if not iid: return warn(self, "انتخاب فاکتور", "ابتدا یک فاکتور انتخاب کن.")
        with db() as con: r = con.execute("SELECT invoice_no FROM invoices WHERE id=? AND COALESCE(is_proforma,0)=0", (iid,)).fetchone()
        if not r: return warn(self, "فاکتور", "این مورد فاکتور نیست.")
        if not ask(self, "برگشت به پیش‌فاکتور", f"فاکتور {r['invoice_no']} به پیش‌فاکتور برگردد؟\nاز حساب مشتری و کاردکس خارج می‌شود و با شماره‌ی جدید در تب «پیش‌فاکتورها» می‌ماند. (در تاریخچه ثبت می‌شود)"): return
        rsn = ask_reason(self, "برگشت به پیش‌فاکتور", "دلیل برگشت را بنویس (الزامی؛ در تاریخچه ثبت می‌شود):")
        if rsn is None: return
        try:
            with db() as con: old, new = safety.invoice_to_proforma(con, AUTH.username, iid, rsn)
        except ValueError as e:
            return warn(self, "امکان‌پذیر نیست", str(e))
        audit("برگشت فاکتور به پیش‌فاکتور", f"{old} ← {new}")
        self.refresh_all(); info(self, "انجام شد", f"فاکتور {old} به پیش‌فاکتور {new} تبدیل شد.")
    def new_invoice(self):
        with db() as con:
            if not con.execute("SELECT 1 FROM customers LIMIT 1").fetchone(): return warn(self, "ابتدا مشتری بساز", "اول در بخش مشتریان، مشتری را ثبت کن.")
        sd = StartDocDialog(self, False)
        if not sd.exec(): return
        d = InvoiceDialog(self, start=sd.result_values())
        if d.exec():
            self.refresh_all()
            if d.print_after and d.saved_id: self.preview_invoice(iid=d.saved_id)
            if d.saved_id: self._offer_sms(d.saved_id)
    def edit_invoice(self, *_):
        iid = self.selected_id(self.inv_table)
        if not iid: return warn(self, "انتخاب فاکتور", "ابتدا یک فاکتور انتخاب کن.")
        d = InvoiceDialog(self, iid)
        if d.exec():
            self.refresh_all()
            if d.print_after: self.preview_invoice(iid=iid)
    def delete_invoice(self):
        iid = self.selected_id(self.inv_table)
        if not iid: return warn(self, "انتخاب فاکتور", "ابتدا یک فاکتور انتخاب کن.")
        with db() as con:
            links = con.execute("SELECT (SELECT COUNT(*) FROM payments WHERE invoice_id=?)+(SELECT COUNT(*) FROM cheques WHERE invoice_id=?)+(SELECT COUNT(*) FROM returns WHERE invoice_id=?)", (iid, iid, iid)).fetchone()[0]
            if links: return warn(self, "فاکتور مرتبط است", "برای این فاکتور دریافت، چک یا مرجوعی ثبت شده است. ابتدا آن‌ها را ویرایش یا حذف کن؛ سپس فاکتور قابل حذف است.")
            if ask(self, "تأیید حذف", "فاکتور و اقلام آن حذف شوند؟ (در سطل بازیافت می‌ماند)"):
                rsn = ask_reason(self)
                if rsn is None: return
                safety.archive_delete(con, AUTH.username, "invoice", iid, rsn)
                con.execute("DELETE FROM invoice_items WHERE invoice_id=?", (iid,)); con.execute("DELETE FROM invoices WHERE id=?", (iid,))
        self.refresh_all()
    @staticmethod
    def _invoice_doc_static(iid):
        st = load_style("invoice"); return st, make_document(build_invoice_html(load_invoice_data(iid), st), st)
    def _invoice_doc(self, iid):
        st = load_style("invoice"); return st, make_document(build_invoice_html(load_invoice_data(iid), st), st)
    def show_invoice_preview(self, iid):
        st, doc = self._invoice_doc(iid)
        show_preview(self, doc, make_printer(st), "پیش‌نمایش و چاپ",
                     extra=[("چاپ روی فیش پرینتر", lambda: self.print_receipt(iid)),
                            ("اشتراک (ایتا / روبیکا)", lambda: ShareDialog(self, lambda: create_invoice_pdf(iid)).exec())])
    def print_receipt(self, iid=None):
        iid = iid or self.selected_id(self.inv_table)
        if not iid: return warn(self, "انتخاب فاکتور", "ابتدا یک فاکتور انتخاب کن.")
        raw = load_raw_style(); st = flatten_style(raw, "invoice")
        data = load_invoice_data(iid)
        if raw["g"].get("receipt_direct", True):
            try:
                pname = choose_receipt_printer(self, raw["g"])
                if not pname: return
                _key = f"inv{iid}"
                if safety.recently_printed(_key) and not ask(self, "چاپ تکراری", "فیش این فاکتور همین چند لحظه پیش چاپ شد. دوباره چاپ شود؟"): return
                try:
                    with safety.print_guard(_key): print_receipt_direct(self, data, raw["g"], pname)
                except safety.PrintBusy: raise
                except Exception as _e:
                    _log_print(False, iid, str(_e)); raise
                _log_print(True, iid)
                QMessageBox.information(self, "چاپ فیش", f"فاکتور به صف چاپگر «{pname}» ارسال شد.\nاگر چیزی چاپ نشد، صف چاپ ویندوز و انتخاب چاپگر را بررسی کن.")
            except Exception as e:
                QMessageBox.warning(self, "خطا در چاپ مستقیم رول", str(e))
        else:
            doc, gst = make_receipt_document(data, raw["g"])
            show_preview(self, doc, receipt_printer_for_document(doc, gst), "فیش پرینتر")
    def share_invoice(self, *_, table=None):
        iid = self.selected_id(table or self.inv_table)
        if not iid: return warn(self, "انتخاب", "ابتدا یک مورد را انتخاب کن.")
        ShareDialog(self, lambda: create_invoice_pdf(iid)).exec()
    def share_proforma(self): self.share_invoice(table=self.pf_table)
    def print_receipt_proforma(self):
        pid = self.selected_id(self.pf_table)
        if not pid: return warn(self, "انتخاب", "ابتدا یک پیش‌فاکتور انتخاب کن.")
        self.print_receipt(pid)
    def preview_invoice(self, *_, iid=None):
        iid = iid or self.selected_id(self.inv_table)
        if not iid: return warn(self, "انتخاب فاکتور", "ابتدا یک فاکتور انتخاب کن.")
        self.show_invoice_preview(iid)
    def invoice_pdf(self, table=None):
        iid = self.selected_id(table or self.inv_table)
        if not iid: return warn(self, "انتخاب", "ابتدا یک مورد را انتخاب کن.")
        st, doc = self._invoice_doc(iid)
        with db() as con: no = con.execute("SELECT invoice_no FROM invoices WHERE id=?", (iid,)).fetchone()[0]
        save_pdf(self, doc, st, f"فاکتور-{no}.pdf")
    def proforma_pdf(self): self.invoice_pdf(self.pf_table)
    # ── proformas ──
    def build_proformas_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.pf_search = QLineEdit(); self.pf_search.setPlaceholderText("جست‌وجو بر اساس شماره یا نام مشتری...")
        self.pf_search.textChanged.connect(self.refresh_proformas)
        self.bar(l, [self.pf_search, self.btn("＋ پیش‌فاکتور جدید", self.new_proforma), self.btn("ویرایش / تکمیل قیمت‌ها", self.edit_proforma),
                     self.btn("نهایی کردن (تبدیل به فاکتور)", self.finalize_proforma), self.btn("مشاهده / چاپ", self.preview_proforma),
                     self.btn("چاپ فیش پرینتر", self.print_receipt_proforma), self.btn("PDF", self.proforma_pdf),
                     self.btn("اشتراک", self.share_proforma), self.btn("حذف", self.delete_proforma)])
        self.pf_table = self.make_table(["شناسه", "شماره", "تاریخ", "مشتری", "تعداد اقلام", "اقلام بدون قیمت", "جمع فعلی", "شرح", "ساعت ثبت"])
        self.pf_table.setColumnHidden(0, True); self.pf_table.doubleClicked.connect(self.edit_proforma); l.addWidget(self.pf_table)
        l.addWidget(QLabel("با «نهایی کردن»، پیش‌فاکتور با شماره فاکتور بعدی به فاکتور تبدیل می‌شود و از این فهرست حذف می‌شود."))
        self.tabs.addTab(w, "پیش‌فاکتورها")
    def refresh_proformas(self):
        q = self.pf_search.text().strip() if hasattr(self, "pf_search") else ""
        with db() as con:
            rows = invoice_rows(con, proforma=1)
            cnt = {r["invoice_id"]: (r["n"], r["z"] or 0) for r in con.execute(
                "SELECT invoice_id, COUNT(*) n, SUM(CASE WHEN unit_price=0 THEN 1 ELSE 0 END) z FROM invoice_items GROUP BY invoice_id")}
        rows = [r for r in rows if match_words(q, r["invoice_no"], r["cname"], r["description"])]; rows.sort(key=lambda r: (r["invoice_date"], r["id"]), reverse=True)
        self.pf_table.setRowCount(0)
        for i in rows:
            n, z = cnt.get(i["id"], (0, 0)); r = self.pf_table.rowCount(); self.pf_table.insertRow(r)
            for c, v in enumerate([i["id"], i["invoice_no"], show_date(i["invoice_date"]), i["cname"], n, z, money(i["total"]), i["description"] or "", show_time(i.get("created_at"))]):
                self.pf_table.setItem(r, c, table_item(v))
            for c in range(self.pf_table.columnCount()): self.pf_table.item(r, c).setForeground(QColor("#1565c0"))        # پیش‌فاکتور = آبی
    def new_proforma(self):
        with db() as con:
            if not con.execute("SELECT 1 FROM customers LIMIT 1").fetchone(): return warn(self, "ابتدا مشتری بساز", "اول در بخش مشتریان، مشتری را ثبت کن.")
        sd = StartDocDialog(self, True)
        if not sd.exec(): return
        d = InvoiceDialog(self, mode="proforma", start=sd.result_values())
        if d.exec():
            self.refresh_all()
            if d.print_after and d.saved_id: self.show_invoice_preview(d.saved_id)
    def edit_proforma(self, *_):
        pid = self.selected_id(self.pf_table)
        if not pid: return warn(self, "انتخاب", "ابتدا یک پیش‌فاکتور انتخاب کن.")
        d = InvoiceDialog(self, pid, mode="proforma")
        if d.exec():
            self.refresh_all()
            if d.print_after: self.show_invoice_preview(pid)
    def finalize_proforma(self):
        pid = self.selected_id(self.pf_table)
        if not pid: return warn(self, "انتخاب", "ابتدا یک پیش‌فاکتور انتخاب کن.")
        d = InvoiceDialog(self, pid, mode="convert")
        if d.exec():
            self.refresh_all(); info(self, "انجام شد", f"پیش‌فاکتور به فاکتور شماره {d.final_no} تبدیل شد و از پیش‌فاکتورها حذف شد.")
            if d.print_after: self.show_invoice_preview(pid)
            self._offer_sms(pid)
    def preview_proforma(self):
        pid = self.selected_id(self.pf_table)
        if not pid: return warn(self, "انتخاب", "ابتدا یک پیش‌فاکتور انتخاب کن.")
        self.show_invoice_preview(pid)
    def delete_proforma(self):
        pid = self.selected_id(self.pf_table)
        if not pid: return warn(self, "انتخاب", "ابتدا یک پیش‌فاکتور انتخاب کن.")
        if ask(self, "تأیید حذف", "پیش‌فاکتور حذف شود؟ (در سطل بازیافت می‌ماند)"):
            rsn = ask_reason(self)
            if rsn is None: return
            with db() as con:
                if con.execute("SELECT 1 FROM invoices WHERE id=? AND is_proforma=1", (pid,)).fetchone():
                    safety.archive_delete(con, AUTH.username, "invoice", pid, rsn)
                con.execute("DELETE FROM invoice_items WHERE invoice_id=?", (pid,)); con.execute("DELETE FROM invoices WHERE id=? AND is_proforma=1", (pid,))
            self.refresh_all()
    def edit_style(self):
        InvoiceStyleDialog(self).exec()
    # ── payments ──
    def new_payment(self):
        with db() as con:
            if not con.execute("SELECT 1 FROM customers LIMIT 1").fetchone(): return warn(self, "ابتدا مشتری بساز", "اول مشتری ثبت کن.")
        d = PaymentDialog(self)
        if d.exec():
            with db() as con: con.execute("INSERT INTO payments(customer_id,invoice_id,payment_date,amount,method,reference,notes,discount) VALUES(?,?,?,?,?,?,?,?)", d.values())
            self.refresh_all()
    def edit_payment(self, *_):
        pid = self.selected_id(self.pay_table)
        if not pid: return warn(self, "انتخاب دریافت", "ابتدا یک دریافت انتخاب کن.")
        with db() as con: data = con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
        if data["cheque_id"]: return warn(self, "دریافتِ چک", "این دریافت از روی یک چک ساخته شده است. برای تغییر، چک را در تب «چک‌ها» ویرایش کن.")
        d = PaymentDialog(self, data)
        if d.exec():
            with db() as con: con.execute("UPDATE payments SET customer_id=?,invoice_id=?,payment_date=?,amount=?,method=?,reference=?,notes=?,discount=? WHERE id=?", (*d.values(), pid))
            self.refresh_all()
    def delete_payment(self):
        pid = self.selected_id(self.pay_table)
        if not pid: return warn(self, "انتخاب دریافت", "ابتدا یک دریافت انتخاب کن.")
        with db() as con: _cp = con.execute("SELECT cheque_id FROM payments WHERE id=?", (pid,)).fetchone()
        if _cp and _cp["cheque_id"]: return warn(self, "دریافتِ چک", "این دریافت از روی یک چک ساخته شده است. برای حذف، وضعیت چک را «برگشتی/مستردشده» کن یا خودِ چک را حذف کن.")
        if ask(self, "تأیید حذف", "این دریافت حذف شود؟ مانده حساب مشتری دوباره محاسبه می‌شود. (در سطل بازیافت می‌ماند)"):
            rsn = ask_reason(self)
            if rsn is None: return
            with db() as con:
                safety.archive_delete(con, AUTH.username, "payment", pid, rsn)
                con.execute("DELETE FROM payments WHERE id=?", (pid,))
            self.refresh_all()
    # ── cheques ──
    def _cheque_note(self, res):
        if res == "removed": info(self, "چک از حساب برگشت", "این چک برگشتی/مستردشده است؛ مبلغ آن دوباره به بدهی مشتری برگشت.")
    def new_cheque(self):
        with db() as con:
            if not con.execute("SELECT 1 FROM customers LIMIT 1").fetchone(): return warn(self, "ابتدا مشتری بساز", "اول مشتری ثبت کن.")
        d = ChequeDialog(self)
        if d.exec():
            v = d.values()
            with db() as con:
                cid = con.execute("INSERT INTO cheques(customer_id,invoice_id,cheque_no,bank,issuer,amount,due_date,status,notes) VALUES(?,?,?,?,?,?,?,?,?)", v).lastrowid
                res = chq_acct.sync(con, AUTH.username, cid, today_iso())
            self.refresh_all(); self._cheque_note(res)
    def edit_cheque(self, *_):
        cid = self.selected_id(self.chq_table)
        if not cid: return warn(self, "انتخاب چک", "ابتدا یک چک انتخاب کن.")
        with db() as con: data = con.execute("SELECT * FROM cheques WHERE id=?", (cid,)).fetchone()
        d = ChequeDialog(self, data)
        if d.exec():
            v = d.values()
            with db() as con:
                con.execute("UPDATE cheques SET customer_id=?,invoice_id=?,cheque_no=?,bank=?,issuer=?,amount=?,due_date=?,status=?,notes=? WHERE id=?", (*v, cid))
                res = chq_acct.sync(con, AUTH.username, cid, today_iso())
            self.refresh_all(); self._cheque_note(res)
    def change_cheque_status(self):
        cid = self.selected_id(self.chq_table)
        if not cid: return warn(self, "انتخاب چک", "ابتدا یک چک انتخاب کن.")
        with db() as con: row = con.execute("SELECT status FROM cheques WHERE id=?", (cid,)).fetchone()
        val, ok = QInputDialog.getItem(self, "وضعیت چک", "وضعیت جدید را انتخاب کن:", CHQ_STATUSES,
                                       CHQ_STATUSES.index(row["status"]) if row["status"] in CHQ_STATUSES else 0, False)
        if ok:
            with db() as con:
                con.execute("UPDATE cheques SET status=? WHERE id=?", (val, cid))
                res = chq_acct.sync(con, AUTH.username, cid, today_iso())
            self.refresh_all(); self._cheque_note(res)
    def delete_cheque(self):
        cid = self.selected_id(self.chq_table)
        if not cid: return warn(self, "انتخاب چک", "ابتدا یک چک انتخاب کن.")
        with db() as con: linked = con.execute("SELECT COUNT(*) FROM payments WHERE cheque_id=?", (cid,)).fetchone()[0]
        msg = "چک حذف شود؟" + ("\nدریافتِ ثبت‌شده برای وصول این چک هم حذف می‌شود." if linked else "")
        if ask(self, "تأیید حذف", msg + " (در سطل بازیافت می‌ماند)"):
            rsn = ask_reason(self)
            if rsn is None: return
            with db() as con:
                safety.archive_delete(con, AUTH.username, "cheque", cid, rsn)
                con.execute("DELETE FROM payments WHERE cheque_id=?", (cid,)); con.execute("DELETE FROM cheques WHERE id=?", (cid,))
            self.refresh_all()
    # ── exports / backup ──
    def export_invoices_excel(self):
        path, _ = QFileDialog.getSaveFileName(self, "ذخیره گزارش Excel", "فاکتورها.xlsx", "Excel (*.xlsx)")
        if not path: return
        try:
            with db() as con: rep = rep_invoices_detail(con, None, "", "")
            export_report_excel(rep, path); info(self, "انجام شد", "فایل Excel ذخیره شد.")
        except Exception as e: warn(self, "خطا در خروجی", str(e))
    def export_table(self, table, title):
        path, _ = QFileDialog.getSaveFileName(self, f"خروجی {title}", f"{title}.xlsx", "Excel (*.xlsx)")
        if not path: return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            wb = Workbook(); ws = wb.active; ws.title = title[:31]; ws.sheet_view.rightToLeft = True
            vis = [c for c in range(table.columnCount()) if not table.isColumnHidden(c)]
            ws.append([table.horizontalHeaderItem(c).text() for c in vis])
            for r in range(table.rowCount()):
                ws.append([strip_bidi(table.item(r, c).text()) if table.item(r, c) else "" for c in vis])
            for cell in ws[1]: cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="286B4A")
            for col in ws.columns: ws.column_dimensions[col[0].column_letter].width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 32)
            wb.save(path); info(self, "انجام شد", "فایل ذخیره شد.")
        except Exception as e: warn(self, "خطا", str(e))
    def closeEvent(self, event):
        try: self.va.stop_always_on()
        except Exception: pass
        try: auto_backup()
        except Exception: pass
        super().closeEvent(event)
    def open_backup_dir(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(BACKUP_DIR)))
    def update_mem_label(self):
        try:
            with db() as con: n = con.execute("SELECT COUNT(*) FROM product_memory").fetchone()[0]
        except Exception: n = 0
        self.mem_label.setText(f"حافظه کالاها: {n} کالا یاد گرفته شده (برای پیشنهاد هنگام تایپ نام کالا در فاکتور).")
    def rebuild_memory(self):
        try:
            with db() as con: rebuild_product_memory(con)
            self.update_mem_label(); info(self, "حافظه کالاها", "حافظه از روی فاکتورهای قبلی دوباره ساخته شد.")
        except Exception as e: warn(self, "خطا", str(e))
    def clear_memory(self):
        if not ask(self, "پاک‌کردن حافظه", "همه‌ی پیشنهادهای یادگرفته‌شده پاک شود؟ (فاکتورها دست نمی‌خورند)"): return
        with db() as con: con.execute("DELETE FROM product_memory"); con.execute("DELETE FROM product_customer_price")
        self.update_mem_label()
    def open_recycle_bin(self):
        if not AUTH.is_admin(): return warn(self, "دسترسی ندارید", "فقط مدیر به سطل بازیافت و تاریخچه دسترسی دارد.")
        d = recycle_ui.RecycleDialog(db, AUTH.username, lambda iso: f"{iso_to_j(str(iso)[:10])}  {str(iso)[11:16]}", self); d.exec()
        if d.changed: self.refresh_all()
    def backup_db(self):
        path, _ = QFileDialog.getSaveFileName(self, "ذخیره پشتیبان", f"FactorDesk-backup-{today_iso()}.db", "Database (*.db)")
        if path:
            try:
                if NET_MODE == "client": netdb.REMOTE.download_backup(path)       # فقط مدیر؛ نسخه‌ی هم‌سازگار از خود سرور
                else: _sqlite_copy(DB_PATH, path)
                info(self, "پشتیبان", "نسخه پشتیبان ذخیره شد.")
            except Exception as e: warn(self, "خطا", str(e))
    def restore_db(self): self.open_backup_center()

for _n, _p in (("delete_customer", "delete"), ("delete_invoice", "delete"), ("delete_proforma", "delete"), ("delete_payment", "delete"), ("delete_return", "delete"), ("revert_to_proforma", "delete"), ("edit_return", "edit"), ("delete_cheque", "delete"),
               ("edit_invoice", "edit"), ("edit_proforma", "edit"), ("sms_invoice", "sms"), ("sms_customer", "sms"), ("restore_db", "settings"),
               ("backup_db", "settings"), ("open_recycle_bin", "settings"), ("open_kardex", "settings"), ("edit_style", "settings"), ("open_backup_center", "settings"), ("open_db_check", "settings")):
    setattr(FactorDesk, _n, _guard(_p)(getattr(FactorDesk, _n)))

APP_STYLE = """
    QWidget { background:#f7f9f7; color:#24342b; }
    QMainWindow { background:#f7f9f7; }
    QTabWidget::pane { border:1px solid #d4ded7; background:white; }
    QTabBar::tab { background:#e7eee9; padding:10px 18px; margin:2px; border-radius:5px; }
    QTabBar::tab:selected { background:#286b4a; color:white; }
    QPushButton, QToolButton { background:#286b4a; color:white; border:0; padding:8px 12px; border-radius:5px; }
    QPushButton:hover, QToolButton:hover { background:#1e563a; }
    QPushButton:disabled { background:#b7c6bc; }
    QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background:white; border:1px solid #cbd8cf; border-radius:4px; padding:6px; }
    QTableWidget { background:white; alternate-background-color:#f0f5f1; gridline-color:#dce5df; selection-background-color:#d0e5d7; selection-color:#17271e; }
    QHeaderView::section { background:#e2ebe5; padding:7px; border:0; border-bottom:1px solid #cbd8cf; font-weight:bold; }
    QGroupBox { border:1px solid #d2ddd5; border-radius:6px; margin-top:8px; padding-top:8px; font-weight:bold; }
    QGroupBox::title { subcontrol-origin:margin; right:10px; padding:0 5px; }
"""

def _install_error_log():
    """Without a console window errors would be invisible: write them to error.log and tell the user."""
    log = APP_DIR / "error.log"
    if sys.stdout is None: sys.stdout = open(os.devnull, "w")
    if sys.stderr is None: sys.stderr = open(os.devnull, "w")
    def hook(et, ev, tb):
        import traceback
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(datetime.now().isoformat() + "\n" + "".join(traceback.format_exception(et, ev, tb)) + "\n")
        except Exception: pass
        try: QMessageBox.warning(None, "خطای برنامه", f"{ev}\n\nجزئیات در فایل زیر ذخیره شد:\n{log}")
        except Exception: pass
    sys.excepthook = hook

def _startup_db_check():
    """هنگام باز شدن: سلامت فایل دیتابیس را می‌سنجد؛ اگر خراب بود، با تأیید کاربر از آخرین پشتیبان سالم بازیابی می‌کند."""
    if not DB_PATH.exists(): return
    ok, msg = safety.quick_check(DB_PATH)
    if ok: return
    good = None
    for b in sorted(BACKUP_DIR.glob("factordesk-*.db"), reverse=True):
        if maintenance.validate_backup(b)[0]: good = b; break
    if good is None:
        QMessageBox.critical(None, "فایل دیتابیس آسیب دیده است", f"{msg}\n\nپشتیبان سالمی پیدا نشد. برنامه بسته می‌شود تا چیزی خراب‌تر نشود.\nفایل {DB_PATH} را دست نزن و کپی بگیر.")
        sys.exit(1)
    if QMessageBox.question(None, "فایل دیتابیس آسیب دیده است", f"{msg}\n\nاز آخرین پشتیبان سالم ({good.name}) بازیابی شود؟\nفایل خراب هم کنار پشتیبان‌ها نگه داشته می‌شود.") != QMessageBox.Yes:
        sys.exit(1)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True); tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    for suf in ("", "-wal", "-shm"):
        f = Path(str(DB_PATH) + suf)
        if f.exists():
            try: shutil.copy2(f, BACKUP_DIR / f"corrupt-{tag}.db{suf}")
            except Exception: pass
            try: f.unlink()
            except Exception: pass
    _sqlite_copy(good, DB_PATH)

def _reconcile_cheques():
    """چک‌های ثبت‌شده‌ی قبلی را خودِ برنامه از داده‌ها تشخیص می‌دهد و در حساب مشتری اعمال می‌کند (بدون دو بار کم‌کردن)."""
    try:
        with db() as con: linked, created, total = chq_acct.reconcile(con, "سیستم", today_iso())
    except Exception:
        return
    if created or linked:
        msg = []
        if created: msg.append(f"{created} چک ثبت‌شده‌ی قبلی (جمعاً {money(total)} تومان) که هنوز از حساب مشتری کم نشده بود، خودکار در حساب اعمال شد.")
        if linked: msg.append(f"{linked} چک با «دریافت»ِ دستیِ هم‌مبلغ هم‌خوان شد و دوباره کم نشد.")
        QMessageBox.information(None, "به‌روزرسانی حساب چک‌ها", "\n".join(msg))

def main():
    app = QApplication(sys.argv)
    _install_error_log()
    app.setApplicationName(APP_NAME); app.setLayoutDirection(Qt.RightToLeft)
    app.setFont(QFont("Tahoma", 10)); app.setStyleSheet(APP_STYLE)
    if NET_MODE == "client":
        import net_ui
        secure.available = lambda: False                       # کلید پیامک روی سرور مشترک است؛ DPAPI مخصوص یک کامپیوتر است
        if not net_ui.client_setup(APP_DIR): sys.exit(0)
        auth.verify_login = net_ui.remote_verify_login; auth.count_users = net_ui.remote_count_users
    else:
        _startup_db_check()
        init_db()
        _reconcile_cheques()
        migrate_secrets()
    global AUTH
    _u = auth.login_flow(_auth_H())
    if not _u: sys.exit(0)
    AUTH = auth.Session(_u)
    win = FactorDesk(); fit_window(win)
    if NET_MODE == "client": net_ui.attach_status(win, APP_DIR)
    ag = win.screen().availableGeometry() if win.screen() else None
    if ag is not None and (win.width() >= ag.width() * 0.9 or win.height() >= ag.height() * 0.9): win.showMaximized()   # صفحه‌ی کوچک: تمام‌صفحه
    else: win.show(); QTimer.singleShot(0, lambda: keep_on_screen(win))
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
