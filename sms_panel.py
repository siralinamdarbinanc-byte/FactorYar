# -*- coding: utf-8 -*-
"""پنل پیامک فاکتور یار (سرویس کاوه‌نگار) — ارسال مانده حساب و فاکتور مشتریان.

بدون نیاز به نصب کتابخانه‌ی اضافه: همان API رسمی کاوه‌نگار (REST) که کتابخانه‌ی kavenegar-python هم
صدا می‌زند، مستقیم با urllib فراخوانی می‌شود (در ساخت exe هم مشکلی پیش نمی‌آید).
"""
import re, json, time, threading, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTextEdit, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QTabWidget, QSplitter, QMessageBox, QInputDialog,
    QProgressDialog, QFormLayout, QApplication, QDialogButtonBox,
)

from ui_fit import QDialog

API_BASE = "https://api.kavenegar.com/v1"
RED, AMBER, GREEN = QBrush(QColor("#c62828")), QBrush(QColor("#b26a00")), QBrush(QColor("#2e7d32"))
KIND_TOTAL, KIND_DETAIL, KIND_INVOICE = "total", "detail", "invoice"
KIND_PAYMENT, KIND_CHEQUE, KIND_RETURN = "payment", "cheque", "return"
KINDS = [(KIND_TOTAL, "مانده حساب — کلی"),
         (KIND_DETAIL, "مانده حساب — جزئی (با فهرست فاکتورهای باز)"),
         (KIND_INVOICE, "فاکتور"),
         (KIND_PAYMENT, "پرداخت وجه (رسید دریافت)"),
         (KIND_CHEQUE, "ثبت چک"),
         (KIND_RETURN, "مرجوعی کالا")]
DOC_KINDS = (KIND_INVOICE, KIND_PAYMENT, KIND_CHEQUE, KIND_RETURN)     # پیامی که درباره‌ی یک سند مشخص است (نه مانده‌ی کل مشتری)
KIND_LABEL = {k: t for k, t in KINDS}
KIND_LABEL["test"] = "آزمایشی"

# متغیرهایی که می‌شود داخل متن پیام گذاشت
VARS_COMMON = [
    ("name", "نام مشتری"), ("balance", "مانده بدهی کل"), ("total", "جمع کل فاکتورها"),
    ("received", "جمع دریافتی‌ها"), ("overdue", "مبلغ سررسید گذشته"), ("open_chq", "چک‌های در جریان"),
    ("invoice_count", "تعداد فاکتورهای باز"), ("open_invoices", "فهرست فاکتورهای باز"),
    ("date", "تاریخ امروز (شمسی)"), ("shop", "نام فروشگاه (امضا)"), ("unit", "واحد پول"),
]
VARS_INVOICE = [
    ("invoice_no", "شماره فاکتور"), ("invoice_date", "تاریخ فاکتور"), ("due_date", "سررسید فاکتور"),
    ("invoice_total", "مبلغ فاکتور"), ("invoice_paid", "پرداخت‌شده‌ی فاکتور"), ("invoice_remain", "مانده‌ی فاکتور"),
]

VARS_PAYMENT = [
    ("payment_amount", "مبلغ پرداختی"), ("payment_discount", "تخفیف"), ("payment_date", "تاریخ پرداخت"),
    ("payment_method", "روش پرداخت"), ("payment_ref", "شماره پیگیری"), ("payment_invoice", "فاکتور مرتبط"),
]
VARS_CHEQUE = [
    ("cheque_no", "شماره چک"), ("cheque_amount", "مبلغ چک"), ("cheque_due", "سررسید چک"),
    ("cheque_bank", "بانک"), ("cheque_issuer", "صادرکننده"), ("cheque_status", "وضعیت چک"), ("cheque_invoice", "فاکتور مرتبط"),
]
VARS_RETURN = [
    ("return_no", "شماره مرجوعی"), ("return_date", "تاریخ مرجوعی"), ("return_total", "مبلغ مرجوعی"),
    ("return_invoice", "فاکتور مرتبط"), ("return_items", "فهرست اقلام"),
]
VARS_BY_KIND = {KIND_INVOICE: VARS_INVOICE, KIND_PAYMENT: VARS_PAYMENT, KIND_CHEQUE: VARS_CHEQUE, KIND_RETURN: VARS_RETURN}

DEFAULT_TEMPLATES = [
    (KIND_TOTAL, "ساده", "{name} عزیز، مانده حساب شما تا تاریخ {date} مبلغ {balance} {unit} است.\n{shop}", 1),
    (KIND_TOTAL, "یادآوری تسویه", "{name} گرامی، یادآوری: مانده بدهی شما {balance} {unit} می‌باشد. خواهشمندیم نسبت به تسویه اقدام فرمایید.\n{shop}", 0),
    (KIND_TOTAL, "با سررسید گذشته", "{name} عزیز، مانده حساب شما {balance} {unit} است که {overdue} {unit} آن سررسید گذشته. لطفاً در اسرع وقت تسویه فرمایید.\n{shop}", 0),
    (KIND_DETAIL, "با فهرست فاکتورها", "{name} عزیز، مانده حساب شما {balance} {unit} است.\nفاکتورهای باز:\n{open_invoices}\n{shop}", 1),
    (KIND_DETAIL, "فهرست + سررسید گذشته", "{name} گرامی، مانده شما {balance} {unit} (سررسید گذشته: {overdue}).\n{open_invoices}\nلطفاً تسویه فرمایید.\n{shop}", 0),
    (KIND_INVOICE, "اطلاع فاکتور", "{name} عزیز، فاکتور شماره {invoice_no} به مبلغ {invoice_total} {unit} در تاریخ {invoice_date} صادر شد. مانده این فاکتور: {invoice_remain} {unit}.\n{shop}", 1),
    (KIND_INVOICE, "تشکر از خرید", "{name} عزیز، از خرید شما سپاسگزاریم. فاکتور {invoice_no} به مبلغ {invoice_total} {unit}.\n{shop}", 0),
    (KIND_INVOICE, "یادآوری فاکتور باز", "{name} گرامی، فاکتور {invoice_no} ({invoice_date}) هنوز {invoice_remain} {unit} مانده دارد. سررسید: {due_date}.\n{shop}", 0),
]

# قالب‌های آماده‌ی پیام برای سندهای تازه (پرداخت وجه، چک، مرجوعی) — برای دیتابیس‌های قبلی هم یک‌بار اضافه می‌شود
DEFAULT_TEMPLATES_V2 = [
    (KIND_PAYMENT, "رسید دریافت", "{name} عزیز، مبلغ {payment_amount} {unit} در تاریخ {payment_date} از شما دریافت شد. مانده حساب شما: {balance} {unit}.\n{shop}", 1),
    (KIND_PAYMENT, "رسید با تخفیف", "{name} عزیز، مبلغ {payment_amount} {unit} (+ تخفیف {payment_discount} {unit}) در تاریخ {payment_date} ثبت شد. مانده حساب: {balance} {unit}.\n{shop}", 0),
    (KIND_CHEQUE, "ثبت چک", "{name} عزیز، چک شماره {cheque_no} بانک {cheque_bank} به مبلغ {cheque_amount} {unit} با سررسید {cheque_due} ثبت شد.\n{shop}", 1),
    (KIND_CHEQUE, "یادآوری سررسید چک", "{name} گرامی، یادآوری: چک شماره {cheque_no} به مبلغ {cheque_amount} {unit} سررسید {cheque_due} دارد.\n{shop}", 0),
    (KIND_RETURN, "ثبت مرجوعی", "{name} عزیز، مرجوعی شماره {return_no} به مبلغ {return_total} {unit} در تاریخ {return_date} ثبت و از حساب شما کسر شد. مانده حساب: {balance} {unit}.\n{shop}", 1),
    (KIND_RETURN, "مرجوعی با اقلام", "{name} عزیز، کالاهای مرجوعی در تاریخ {return_date}:\n{return_items}\nمبلغ کسر شده از حساب: {return_total} {unit}.\n{shop}", 0),
]
LOOKUP_TEMPLATES_V2 = [
    (KIND_PAYMENT, "الگوی کاوه‌نگار — رسید دریافت",
     "یدک مارکت زینلی\n%token عزیز، مبلغ %token2 تومان در تاریخ %token10 از شما دریافت شد.\nمانده حساب شما: %token20 تومان.",
     {"token": "name", "token2": "payment_amount", "token10": "payment_date", "token20": "balance"}),
    (KIND_CHEQUE, "الگوی کاوه‌نگار — ثبت چک",
     "یدک مارکت زینلی\n%token عزیز، چک شماره %token2 به مبلغ %token3 تومان با سررسید %token10 ثبت شد.\nبانک: %token20",
     {"token": "name", "token2": "cheque_no", "token3": "cheque_amount", "token10": "cheque_due", "token20": "cheque_bank"}),
    (KIND_RETURN, "الگوی کاوه‌نگار — ثبت مرجوعی",
     "یدک مارکت زینلی\n%token عزیز، مرجوعی شماره %token2 به مبلغ %token3 تومان در تاریخ %token10 ثبت شد.\nمانده حساب شما: %token20 تومان.",
     {"token": "name", "token2": "return_no", "token3": "return_total", "token10": "return_date", "token20": "balance"}),
]

TOKEN_KEYS = ["token", "token2", "token3", "token10", "token20"]
LOOKUP_TEMPLATES = [
    (KIND_TOTAL, "الگوی کاوه‌نگار — یادآوری مانده",
     "یدک مارکت زینلی\n%token گرامی، یادآوری: مانده بدهی شما %token2 تومان می‌باشد.\nخواهشمندیم نسبت به تسویه حساب اقدام فرمایید.",
     {"token": "name", "token2": "balance"}),
    (KIND_INVOICE, "الگوی کاوه‌نگار — اطلاع فاکتور",
     "یدک مارکت زینلی\n%token عزیز، فاکتور شماره %token2 به مبلغ %token3 تومان در تاریخ %token10 صادر شد.\nمانده این فاکتور: %token20 تومان.",
     {"token": "name", "token2": "invoice_no", "token3": "invoice_total", "token10": "invoice_date", "token20": "invoice_remain"}),
]


# ───────────────────────────── دیتابیس ─────────────────────────────
def ensure_tables(con):
    """جدول‌های پیامک را می‌سازد و بار اول قالب‌های آماده را می‌گذارد (با اتصال دیتابیس برنامه صدا زده می‌شود)."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS sms_templates(
      id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL,
      body TEXT NOT NULL, is_default INTEGER DEFAULT 0,
      mode TEXT DEFAULT 'free', kn_template TEXT DEFAULT '', tokens TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS sms_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, customer_id INTEGER,
      ref_id INTEGER DEFAULT 0, kind TEXT NOT NULL, phone TEXT DEFAULT '', message TEXT DEFAULT '',
      ok INTEGER DEFAULT 0, status_text TEXT DEFAULT '', message_id TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_sms_log_ref ON sms_log(kind, ref_id);
    """)
    cols = [r[1] for r in con.execute("PRAGMA table_info(sms_templates)")]
    for c, ddl in (("mode", "TEXT DEFAULT 'free'"), ("kn_template", "TEXT DEFAULT ''"), ("tokens", "TEXT DEFAULT ''")):
        if c not in cols: con.execute(f"ALTER TABLE sms_templates ADD COLUMN {c} {ddl}")
    if not con.execute("SELECT 1 FROM settings WHERE key='pref_sms_seeded_lookup'").fetchone():
        for kind, title, body, mp in LOOKUP_TEMPLATES:
            con.execute("INSERT INTO sms_templates(kind,title,body,is_default,mode,kn_template,tokens) VALUES(?,?,?,0,'lookup','',?)",
                        (kind, title, body, json.dumps(mp, ensure_ascii=False)))
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pref_sms_seeded_lookup','1')")
    if not con.execute("SELECT 1 FROM settings WHERE key='pref_sms_seeded_v2'").fetchone():
        for kind, title, body, dflt in DEFAULT_TEMPLATES_V2:
            con.execute("INSERT INTO sms_templates(kind,title,body,is_default) VALUES(?,?,?,?)", (kind, title, body, dflt))
        for kind, title, body, mp in LOOKUP_TEMPLATES_V2:
            con.execute("INSERT INTO sms_templates(kind,title,body,is_default,mode,kn_template,tokens) VALUES(?,?,?,0,'lookup','',?)",
                        (kind, title, body, json.dumps(mp, ensure_ascii=False)))
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pref_sms_seeded_v2','1')")
    if not con.execute("SELECT 1 FROM settings WHERE key='pref_sms_seeded'").fetchone():
        for kind, title, body, dflt in DEFAULT_TEMPLATES:
            con.execute("INSERT INTO sms_templates(kind,title,body,is_default) VALUES(?,?,?,?)", (kind, title, body, dflt))
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pref_sms_seeded','1')")


# ───────────────────────────── ابزارهای متن و شماره ─────────────────────────────
_VAR_RE = re.compile(r"\{([a-z_]+)\}")
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def render(text, ctx):
    """متغیرهای {name} را جایگزین می‌کند؛ متغیر ناشناخته دست‌نخورده می‌ماند (بدون خطا)."""
    return _VAR_RE.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), text or "")


def norm_mobile(raw):
    """اولین شماره موبایل ایران را به شکل 09xxxxxxxxx برمی‌گرداند؛ اگر نبود رشته‌ی خالی."""
    t = re.sub(r"[\s\-\(\)]", "", (raw or "").translate(_DIG))
    for m in re.finditer(r"(?:\+98|0098|98|0)?(9\d{9})(?!\d)", t):
        return "0" + m.group(1)
    return ""


_INVIS = re.compile("[\u200b\u200d\u200e\u200f\u202a-\u202e\u2060-\u2069\ufeff]")   # کاراکترهای نامرئی (نیم‌فاصله \u200c عمداً نیست)


SPACE_CH = "_"      # جایگزین فاصله در token/token2/token3 (کاوه‌نگار فاصله و نیم‌فاصله را در این سه توکن نمی‌پذیرد)


def set_space_char(c):
    global SPACE_CH
    SPACE_CH = c if c else "_"


def clean_token(key, value, strict=False):
    """مقدار توکن الگو را برای کاوه‌نگار امن می‌کند (خطای ۴۳۱ = ساختار توکن صحیح نیست).
    token/token2/token3: بدون فاصله و بدون نیم‌فاصله؛ فقط حرف، عدد و , . / - (فاصله، نیم‌فاصله و علامت‌های دیگر ← SPACE_CH).
    token10/token20: فاصله مجاز. strict=True: فقط حرف و عدد (برای تلاش دوم بعد از ۴۳۱). حداکثر ۱۰۰ کاراکتر."""
    v = _INVIS.sub("", str(value or "").replace("\u00a0", " "))
    v = re.sub(r"[\r\n\t]+", " - ", v).strip()
    if key in ("token", "token2", "token3"):
        v = re.sub(r"[^\w]+" if strict else r"[^\w,./\-]+", SPACE_CH, v)
        v = re.sub(re.escape(SPACE_CH) + "+", SPACE_CH, v).strip(SPACE_CH)
    else:
        v = re.sub(r"[^\w ]+" if strict else r"[^\w ,./\-:\u200c]+", " ", v)
        v = re.sub(" +", " ", v).strip()
    return v[:100]


def build_tokens(mapping, ctx, strict=False):
    out = {}
    for k in TOKEN_KEYS:
        val = clean_token(k, ctx.get(mapping.get(k, ""), ""), strict) if mapping.get(k) else ""
        if val: out[k] = val
        elif k == "token": out[k] = "-"
    return out


def render_tokens(text, tokens):
    return re.sub(r"%(token\d*)", lambda m: tokens.get(m.group(1), m.group(0)), text or "")


def sms_parts(text):
    """تعداد بخش (پیامک) تقریبی: فارسی/یونیکد ۷۰ و ۶۷ کاراکتر، انگلیسی ۱۶۰ و ۱۵۳."""
    n = len(text or "")
    if n == 0: return 0
    uni = any(ord(c) > 127 for c in text)
    one, multi = (70, 67) if uni else (160, 153)
    return 1 if n <= one else -(-n // multi)


# ───────────────────────────── کلاینت کاوه‌نگار ─────────────────────────────
class KavenegarError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg); self.code = code


def kn_request(api_key, path, params=None, timeout=20):
    key = (api_key or "").strip()
    if not key: raise KavenegarError("کلید API کاوه‌نگار وارد نشده است.")
    url = f"{API_BASE}/{urllib.parse.quote(key, safe='')}/{path}"
    data = urllib.parse.urlencode(params).encode("utf-8") if params else None
    req = urllib.request.Request(url, data=data, headers={
        "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    http_code = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        http_code = e.code
        body = e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise KavenegarError(f"اتصال به سرور کاوه‌نگار برقرار نشد (اینترنت را بررسی کن): {getattr(e, 'reason', e)}")
    try:
        js = json.loads(body)
    except Exception:
        raise KavenegarError(f"پاسخ نامعتبر از کاوه‌نگار (HTTP {http_code or '?'})", http_code)
    ret = js.get("return") or {}
    if ret.get("status") != 200:
        raise KavenegarError(f"{ret.get('message') or 'خطای نامشخص'} (کد {ret.get('status', http_code)})", ret.get("status", http_code))
    return js.get("entries")


def send_sms(api_key, receptor, message, sender=""):
    params = {"receptor": receptor, "message": message}
    if (sender or "").strip(): params["sender"] = sender.strip()
    ent = kn_request(api_key, "sms/send.json", params)
    if isinstance(ent, list): ent = ent[0] if ent else {}
    return ent or {}


def send_lookup(api_key, receptor, template, tokens):
    """ارسال با الگوی تأییدشده‌ی کاوه‌نگار (verify/lookup)."""
    params = {"receptor": receptor, "template": template, "type": "sms"}; params.update(tokens)   # type: sms یا call
    ent = kn_request(api_key, "verify/lookup.json", params)
    if isinstance(ent, list): ent = ent[0] if ent else {}
    return ent or {}


def send_lookup_safe(api_key, receptor, L):
    """ارسال با الگو؛ اگر کاوه‌نگار خطای ۴۳۱ (ساختار توکن) داد یک بار با توکن‌های ساده‌شده تلاش می‌کند.
    در شکست نهایی، توکن‌های ارسالی در متن خطا می‌آیند تا بشود علت را دید. L = dict(template, tokens, mapping, ctx)."""
    try:
        return send_lookup(api_key, receptor, L["template"], L["tokens"])
    except KavenegarError as ex:
        if ex.code != 431: raise
        t2 = build_tokens(L["mapping"], L["ctx"], strict=True)
        show = lambda t: " ، ".join(f"{k}={v}" for k, v in t.items())
        if t2 == L["tokens"]: raise KavenegarError(f"{ex} | توکن‌ها: {show(L['tokens'])}", ex.code)
        try:
            e = dict(send_lookup(api_key, receptor, L["template"], t2))
            e["statustext"] = (e.get("statustext") or "در صف ارسال") + " (با توکن ساده‌شده)"
            return e
        except KavenegarError as ex2:
            raise KavenegarError(f"{ex2} | توکن‌ها: {show(L['tokens'])}", ex2.code)


def account_info(api_key):
    ent = kn_request(api_key, "account/info.json")
    return ent if isinstance(ent, dict) else (ent[0] if ent else {})


def message_status(api_key, ids):
    ent = kn_request(api_key, "sms/status.json", {"messageid": ",".join(str(i) for i in ids)})
    if isinstance(ent, dict): ent = [ent]
    return {str(e.get("messageid")): e.get("statustext", "") for e in (ent or [])}


class LookupDialog(QDialog):
    """تعریف یک الگوی کاوه‌نگار: نام الگو + متن (برای پیش‌نمایش) + اینکه هر %token چه مقداری بگیرد."""
    def __init__(self, parent, vars_list, title="", kn="", body="", mapping=None):
        super().__init__(parent); self.setWindowTitle("الگوی کاوه‌نگار (Verification)"); self.resize(580, 560)
        l = QVBoxLayout(self); form = QFormLayout()
        self.title = QLineEdit(title); self.kn = QLineEdit(kn)
        self.kn.setPlaceholderText("نام الگو دقیقاً مثل console.kavenegar.com ← verification")
        form.addRow("عنوان در برنامه:", self.title); form.addRow("نام الگو در کاوه‌نگار:", self.kn); l.addLayout(form)
        l.addWidget(QLabel("متن الگو (همان متنی که در سایت ثبت کرده‌ای، با %token ها — فقط برای پیش‌نمایش):"))
        self.body = QTextEdit(); self.body.setAcceptRichText(False); self.body.setPlainText(body); l.addWidget(self.body)
        g = QFormLayout(); self.combos = {}; mapping = mapping or {}
        for k in TOKEN_KEYS:
            c = QComboBox(); c.addItem("— خالی —", "")
            for v, t in vars_list: c.addItem(f"{{{v}}} — {t}", v)
            c.setCurrentIndex(max(0, c.findData(mapping.get(k, "")))); self.combos[k] = c; g.addRow(f"%{k} =", c)
        l.addLayout(g)
        l.addWidget(QLabel("نکته: token و token2 و token3 در کاوه‌نگار فاصله و نیم‌فاصله نمی‌پذیرند (برنامه به‌جای آن «_» می‌گذارد). برای نمایش اسم با فاصله، در الگوی سایت اسم را در %token10 یا %token20 بگذار و اینجا همان را به {name} وصل کن."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); l.addWidget(bb)

    def values(self):
        return dict(title=self.title.text().strip(), kn=self.kn.text().strip(), body=self.body.toPlainText().strip(),
                    mapping={k: c.currentData() for k, c in self.combos.items() if c.currentData()})


class _Sig(QObject):
    one = Signal(int, bool, str, str)   # شماره‌ی کار، موفق؟، متن وضعیت، messageid
    done = Signal()


# ───────────────────────────── پنل ─────────────────────────────
class SmsPanel(QWidget):
    """H = مجموعه‌ی توابع کمکی برنامه‌ی اصلی (db, customer_figures, invoice_rows, money, ...)."""

    def __init__(self, H, parent=None):
        super().__init__(parent)
        self.H = H; self.kind = KIND_TOTAL; self.rows = []; self.jobs = []
        self.sig = _Sig(); self.sig.one.connect(self._on_one); self.sig.done.connect(self._on_done)
        self.cancel = threading.Event(); self.progress = None; self.ok_n = 0; self.fail_n = 0
        self._busy = False; self._apply_prefs()
        lay = QVBoxLayout(self); self.sub = QTabWidget(); lay.addWidget(self.sub)
        self._build_send(); self._build_history(); self._build_settings()
        self.refresh()

    # ── تنظیمات کوچک ──
    def _pref(self, k, d=""): return self.H.get_pref("sms_" + k, d)
    def _shop(self):
        s = self._pref("shop", "").strip()
        if s: return s
        try: return (self.H.load_style("invoice").get("shop_name") or "").strip()
        except Exception: return ""
    def _apply_prefs(self): set_space_char(self._pref("space_char", "_").strip() or "_")
    def _unit(self): return self._pref("unit", "تومان").strip() or "تومان"

    # ───────────── تب ارسال ─────────────
    def _build_send(self):
        w = QWidget(); root = QVBoxLayout(w)
        self.kind_combo = QComboBox()
        for k, t in KINDS: self.kind_combo.addItem(t, k)
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        self.only_debtors = QCheckBox("فقط بدهکاران"); self.only_debtors.setChecked(True)
        self.only_debtors.toggled.connect(lambda _v: self.refresh())
        self.period = QComboBox()
        for t, d in (("همه تاریخ‌ها", 0), ("امروز", 1), ("۷ روز اخیر", 7), ("۳۰ روز اخیر", 30)): self.period.addItem(t, d)
        self.period.currentIndexChanged.connect(lambda _i: self.refresh())
        self.only_open = QCheckBox("فقط فاکتورهای مانده‌دار"); self.only_open.toggled.connect(lambda _v: self.refresh())
        self.search = QLineEdit(); self.search.setPlaceholderText("جست‌وجوی نام طرف حساب (هر کلمه، به هر ترتیب)، تلفن یا شماره...")
        self.search.textChanged.connect(lambda _t: self.refresh())
        top = QHBoxLayout()
        for x in (QLabel("نوع پیام:"), self.kind_combo, self.only_debtors, self.period, self.only_open, self.search): top.addWidget(x)
        for t, fn in (("انتخاب همه", lambda: self._check_all(True)), ("هیچ‌کدام", lambda: self._check_all(False))):
            b = QPushButton(t); b.clicked.connect(fn); top.addWidget(b)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal); root.addWidget(split, 1)
        self.table = QTableWidget(0, 1); self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(lambda _i: self._update_count())
        self.table.itemSelectionChanged.connect(self._update_preview)
        split.addWidget(self.table)

        right = QWidget(); r = QVBoxLayout(right)
        trow = QHBoxLayout(); self.tpl = QComboBox(); self.tpl.currentIndexChanged.connect(self._tpl_changed)
        trow.addWidget(QLabel("قالب پیام:")); trow.addWidget(self.tpl, 1); r.addLayout(trow)
        brow = QHBoxLayout()
        for t, fn in (("ذخیره تغییرات", self._tpl_save), ("ذخیره به‌عنوان قالب جدید", self._tpl_save_new),
                      ("پیش‌فرض کن", self._tpl_set_default), ("حذف قالب", self._tpl_delete)):
            b = QPushButton(t); b.clicked.connect(fn); brow.addWidget(b)
        r.addLayout(brow)
        lrow = QHBoxLayout()
        b_new = QPushButton("＋ الگوی کاوه‌نگار جدید"); b_new.clicked.connect(self._lookup_new)
        self.btn_lk_edit = QPushButton("ویرایش الگوی کاوه‌نگار"); self.btn_lk_edit.clicked.connect(self._lookup_edit)
        lrow.addWidget(b_new); lrow.addWidget(self.btn_lk_edit); r.addLayout(lrow)
        self.lk_hint = QLabel(""); self.lk_hint.setWordWrap(True); r.addWidget(self.lk_hint)
        vrow = QHBoxLayout(); self.var_combo = QComboBox(); vrow.addWidget(QLabel("درج متغیر:")); vrow.addWidget(self.var_combo, 1)
        bv = QPushButton("درج"); bv.clicked.connect(self._insert_var); vrow.addWidget(bv); r.addLayout(vrow)
        self.editor = QTextEdit(); self.editor.setAcceptRichText(False); self.editor.textChanged.connect(self._update_preview)
        r.addWidget(self.editor, 2)
        self.counter = QLabel(""); r.addWidget(self.counter)
        r.addWidget(QLabel("پیش‌نمایش برای ردیف انتخاب‌شده:"))
        self.preview = QTextEdit(); self.preview.setReadOnly(True); r.addWidget(self.preview, 2)
        self.sel_lbl = QLabel(""); r.addWidget(self.sel_lbl)
        srow = QHBoxLayout()
        self.btn_send = QPushButton("📨 ارسال به انتخاب‌شده‌ها"); self.btn_send.clicked.connect(self.send_selected)
        self.btn_test = QPushButton("ارسال آزمایشی به شماره‌ی خودم"); self.btn_test.clicked.connect(self.send_test)
        srow.addWidget(self.btn_send); srow.addWidget(self.btn_test); r.addLayout(srow)
        split.addWidget(right); split.setStretchFactor(0, 3); split.setStretchFactor(1, 2)
        self.sub.addTab(w, "ارسال پیامک")
        self._kind_changed()

    def _kind_changed(self, *_):
        self.kind = self.kind_combo.currentData()
        inv = self.kind in DOC_KINDS
        self.only_debtors.setVisible(not inv); self.period.setVisible(inv); self.only_open.setVisible(self.kind == KIND_INVOICE)
        self.var_combo.clear()
        for k, t in self._vars_for_kind(): self.var_combo.addItem(f"{{{k}}} — {t}", k)
        self._load_templates(); self.refresh()

    # ── قالب‌ها ──
    def _load_templates(self, select_id=None):
        with self.H.db() as con:
            rows = con.execute("SELECT id,title,body,is_default,mode,kn_template,tokens FROM sms_templates WHERE kind=? ORDER BY is_default DESC, id", (self.kind,)).fetchall()
        self.tpl.blockSignals(True); self.tpl.clear()
        for r in rows: self.tpl.addItem(("★ " if r["is_default"] else "") + ("🔗 " if r["mode"] == "lookup" else "") + r["title"], (r["id"], r["body"], r["mode"] or "free", r["kn_template"] or "", r["tokens"] or ""))
        idx = 0
        if select_id is not None:
            for i in range(self.tpl.count()):
                if self.tpl.itemData(i)[0] == select_id: idx = i
        self.tpl.setCurrentIndex(idx if rows else -1); self.tpl.blockSignals(False)
        self._tpl_changed()

    def _tpl_changed(self, *_):
        d = self.tpl.currentData()
        self.editor.blockSignals(True); self.editor.setPlainText(d[1] if d else ""); self.editor.blockSignals(False)
        lk = bool(d and d[2] == "lookup"); self.btn_lk_edit.setEnabled(lk)
        if lk: self.lk_hint.setText(f"🔗 ارسال با الگوی کاوه‌نگار: «{d[3]}»" if d[3] else "⚠ نام الگوی کاوه‌نگار هنوز وارد نشده؛ «ویرایش الگوی کاوه‌نگار» را بزن.")
        else: self.lk_hint.setText("")
        self._update_preview()

    def _tpl_save(self):
        d = self.tpl.currentData()
        if not d: return self._tpl_save_new()
        with self.H.db() as con: con.execute("UPDATE sms_templates SET body=? WHERE id=?", (self.editor.toPlainText(), d[0]))
        self._load_templates(d[0]); QMessageBox.information(self, "قالب", "تغییرات قالب ذخیره شد.")

    def _tpl_save_new(self):
        body = self.editor.toPlainText().strip()
        if not body: return QMessageBox.warning(self, "قالب", "متن پیام خالی است.")
        title, ok = QInputDialog.getText(self, "قالب جدید", "نام قالب:")
        if not ok or not title.strip(): return
        with self.H.db() as con:
            nid = con.execute("INSERT INTO sms_templates(kind,title,body,is_default) VALUES(?,?,?,0)", (self.kind, title.strip(), body)).lastrowid
        self._load_templates(nid)

    def _tpl_set_default(self):
        d = self.tpl.currentData()
        if not d: return
        with self.H.db() as con:
            con.execute("UPDATE sms_templates SET is_default=0 WHERE kind=?", (self.kind,))
            con.execute("UPDATE sms_templates SET is_default=1 WHERE id=?", (d[0],))
        self._load_templates(d[0])

    def _tpl_delete(self):
        d = self.tpl.currentData()
        if not d: return
        if QMessageBox.question(self, "حذف قالب", "این قالب حذف شود؟") != QMessageBox.Yes: return
        with self.H.db() as con: con.execute("DELETE FROM sms_templates WHERE id=?", (d[0],))
        self._load_templates()

    def _vars_for_kind(self):
        return VARS_COMMON + VARS_BY_KIND.get(self.kind, [])

    def _lookup_new(self):
        dlg = LookupDialog(self, self._vars_for_kind(), mapping={"token": "name"})
        if not dlg.exec(): return
        v = dlg.values()
        if not v["title"] or not v["body"]: return QMessageBox.warning(self, "الگو", "عنوان و متن الگو را پر کن.")
        with self.H.db() as con:
            nid = con.execute("INSERT INTO sms_templates(kind,title,body,is_default,mode,kn_template,tokens) VALUES(?,?,?,0,'lookup',?,?)",
                              (self.kind, v["title"], v["body"], v["kn"], json.dumps(v["mapping"], ensure_ascii=False))).lastrowid
        self._load_templates(nid)

    def _lookup_edit(self):
        d = self.tpl.currentData()
        if not d or d[2] != "lookup": return
        try: mp = json.loads(d[4] or "{}")
        except Exception: mp = {}
        title = self.tpl.currentText().replace("★ ", "").replace("🔗 ", "")
        dlg = LookupDialog(self, self._vars_for_kind(), title, d[3], self.editor.toPlainText(), mp)
        if not dlg.exec(): return
        v = dlg.values()
        if not v["title"] or not v["body"]: return QMessageBox.warning(self, "الگو", "عنوان و متن الگو را پر کن.")
        with self.H.db() as con:
            con.execute("UPDATE sms_templates SET title=?, body=?, kn_template=?, tokens=? WHERE id=?",
                        (v["title"], v["body"], v["kn"], json.dumps(v["mapping"], ensure_ascii=False), d[0]))
        self._load_templates(d[0])

    def _cur_lookup(self):
        d = self.tpl.currentData()
        if d and d[2] == "lookup":
            try: mp = json.loads(d[4] or "{}")
            except Exception: mp = {}
            return dict(template=d[3], mapping=mp)
        return None

    def _compose(self, ctx):
        """(متن نهایی، None) برای پیامک عادی؛ (متن پیش‌نمایش، {template, tokens}) برای الگوی کاوه‌نگار."""
        text = self.editor.toPlainText(); lk = self._cur_lookup()
        if not lk: return render(text, ctx), None
        tokens = build_tokens(lk["mapping"], ctx)
        return render_tokens(text, tokens), dict(template=lk["template"], tokens=tokens, mapping=lk["mapping"], ctx=ctx)

    def _insert_var(self):
        k = self.var_combo.currentData()
        if k: self.editor.insertPlainText("{" + k + "}"); self.editor.setFocus()

    # ── فهرست گیرندگان ──
    def _build_ctx(self, f, open_invs, inv=None, unl=0.0):
        H = self.H; m = lambda v: H.strip_bidi(H.money(v))
        lines = [f"فاکتور {i['invoice_no']} ({H.iso_to_j(i['invoice_date'])}): {m(i['remain'])}" for i in open_invs[:6]]
        if len(open_invs) > 6: lines.append(f"و {len(open_invs) - 6} فاکتور دیگر")
        if unl > 0.5: lines.append(f"دریافتی بدون اتصال به فاکتور: -{m(unl)}")   # تا جمع فهرست با مانده کل بخواند
        ctx = dict(name=f["name"], balance=m(f["balance"]), total=m(f["total"]), received=m(f["received"]),
                   overdue=m(min(f["overdue"], max(f["balance"], 0))), open_chq=m(f["open_chq"]), invoice_count=str(len(open_invs)),
                   open_invoices="\n".join(lines), date=H.today_j(), shop=self._shop(), unit=self._unit())
        if inv:
            ctx.update(invoice_no=inv["invoice_no"], invoice_date=H.iso_to_j(inv["invoice_date"]),
                       due_date=H.iso_to_j(inv["due_date"]) if inv["due_date"] else "—",
                       invoice_total=m(inv["total"]), invoice_paid=m(inv["paid"]), invoice_remain=m(inv["remain"]))
        return ctx

    def refresh(self):
        if not hasattr(self, "table"): return
        H = self.H; kind = self.kind; q = self.search.text().strip()
        today = H.today_iso()
        with H.db() as con:
            figs = H.customer_figures(con); invs = H.invoice_rows(con)
            last = {r[0]: r[1] for r in con.execute("SELECT ref_id, MAX(created_at) FROM sms_log WHERE kind=? AND ok=1 GROUP BY ref_id", (kind,))}
            unl = {r[0]: float(r[1] or 0) for r in con.execute("SELECT customer_id, SUM(amount+COALESCE(discount,0)) FROM payments WHERE invoice_id IS NULL GROUP BY customer_id")}
            for r in con.execute("SELECT r.customer_id, SUM(ri.quantity*ri.unit_price) FROM returns r JOIN return_items ri ON ri.return_id=r.id WHERE r.invoice_id IS NULL GROUP BY r.customer_id"):
                unl[r[0]] = unl.get(r[0], 0.0) + float(r[1] or 0)
        open_by = {}
        for i in invs:
            if i["remain"] > 0.5: open_by.setdefault(i["customer_id"], []).append(i)
        rows = []
        if kind in (KIND_PAYMENT, KIND_CHEQUE, KIND_RETURN):
            rows, heads = self._doc_rows(kind, figs, open_by, unl, last, q)
        elif kind == KIND_INVOICE:
            days = self.period.currentData() or 0
            cut = (datetime.now() - timedelta(days=days - 1)).date().isoformat() if days else ""
            for inv in reversed(invs):
                if cut and inv["invoice_date"] < cut: continue
                if self.only_open.isChecked() and inv["remain"] <= 0.5: continue
                if q and not self.H.match(q, inv["cname"], inv["cphone"], inv["invoice_no"]): continue
                f = figs[inv["customer_id"]]
                rows.append(dict(id=inv["id"], customer_id=f["id"], name=f["name"], raw=f["phone"], phone=norm_mobile(f["phone"]),
                                 ref=inv["id"], last=last.get(inv["id"], ""),
                                 cells=[inv["invoice_no"], H.iso_to_j(inv["invoice_date"]), H.iso_to_j(inv["due_date"]) if inv["due_date"] else "—", f["name"], f["phone"],
                                        H.money(inv["total"]), H.money(inv["remain"])], pj=5,
                                 ctx=self._build_ctx(f, open_by.get(f["id"], []), inv, unl.get(f["id"], 0.0))))
            heads = ["", "شماره فاکتور", "تاریخ", "سررسید", "مشتری", "تلفن", "مبلغ فاکتور", "مانده فاکتور", "آخرین پیامک"]
        else:
            for f in sorted(figs.values(), key=lambda x: -x["balance"]):
                if self.only_debtors.isChecked() and f["balance"] <= 0.5: continue
                if q and not self.H.match(q, f["name"], f["phone"], f["address"]): continue
                rows.append(dict(id=f["id"], customer_id=f["id"], name=f["name"], raw=f["phone"], phone=norm_mobile(f["phone"]),
                                 ref=f["id"], last=last.get(f["id"], ""),
                                 cells=[f["name"], f["phone"], H.money(f["balance"]), H.money(f["overdue"]), str(len(open_by.get(f["id"], [])))],
                                 ctx=self._build_ctx(f, open_by.get(f["id"], []), None, unl.get(f["id"], 0.0))))
            heads = ["", "مشتری", "تلفن", "مانده بدهی", "سررسید گذشته", "فاکتور باز", "آخرین پیامک"]
        self.rows = rows
        t = self.table; t.blockSignals(True); t.setRowCount(0); t.setColumnCount(len(heads)); t.setHorizontalHeaderLabels(heads)
        for r, rec in enumerate(rows):
            t.insertRow(r)
            chk = QTableWidgetItem(); ok = bool(rec["phone"])
            chk.setFlags((Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable) if ok else (Qt.ItemIsEnabled | Qt.ItemIsSelectable))
            chk.setCheckState(Qt.Unchecked); t.setItem(r, 0, chk)
            cells = list(rec["cells"])
            last_txt = H.iso_to_j(rec["last"][:10]) if rec["last"] else ""
            for j, v in enumerate(cells + [last_txt], 1):
                it = QTableWidgetItem(str(v if v is not None else "")); it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if not ok and j == rec.get("pj", 2):
                    it.setText((it.text() + "  ⚠ شماره موبایل معتبر نیست").strip())
                    it.setForeground(RED)
                if rec["last"] and rec["last"][:10] == today and j == len(cells) + 1: it.setForeground(AMBER)
                t.setItem(r, j, it)
        t.resizeColumnsToContents(); t.setColumnWidth(0, 36); t.blockSignals(False)
        self._update_count(); self._update_preview()

    def _doc_rows(self, kind, figs, open_by, unl, last, q):
        """ردیف‌های پیامک برای پرداخت وجه / چک / مرجوعی. تاریخ و سررسید کنار هم می‌آیند."""
        H = self.H; m = lambda v: H.strip_bidi(H.money(v)); rows = []
        days = self.period.currentData() or 0
        cut = (datetime.now() - timedelta(days=days - 1)).date().isoformat() if days else ""
        def base(f, doc_ctx):
            ctx = self._build_ctx(f, open_by.get(f["id"], []), None, unl.get(f["id"], 0.0)); ctx.update(doc_ctx); return ctx
        with H.db() as con:
            if kind == KIND_PAYMENT:
                src = con.execute("""SELECT p.*, i.invoice_no FROM payments p LEFT JOIN invoices i ON i.id=p.invoice_id
                                     ORDER BY p.payment_date DESC, p.id DESC""").fetchall()
                heads = ["", "شماره پیگیری", "تاریخ پرداخت", "مشتری", "تلفن", "مبلغ", "تخفیف", "روش", "فاکتور", "آخرین پیامک"]
                for p in src:
                    f = figs.get(p["customer_id"])
                    if not f or (cut and p["payment_date"] < cut): continue
                    if q and not H.match(q, f["name"], f["phone"], p["reference"], p["invoice_no"], p["method"]): continue
                    disc = float(p["discount"] or 0)
                    rows.append(dict(id=p["id"], customer_id=f["id"], name=f["name"], raw=f["phone"], phone=norm_mobile(f["phone"]), ref=p["id"],
                                     last=last.get(p["id"], ""), pj=4,
                                     cells=[p["reference"] or "—", H.iso_to_j(p["payment_date"]), f["name"], f["phone"], H.money(p["amount"]),
                                            H.money(disc) if disc else "", p["method"], p["invoice_no"] or ""],
                                     ctx=base(f, dict(payment_amount=m(p["amount"]), payment_discount=m(disc), payment_date=H.iso_to_j(p["payment_date"]),
                                                      payment_method=p["method"] or "", payment_ref=p["reference"] or "—", payment_invoice=p["invoice_no"] or "—"))))
            elif kind == KIND_CHEQUE:
                src = con.execute("""SELECT ch.*, i.invoice_no FROM cheques ch LEFT JOIN invoices i ON i.id=ch.invoice_id
                                     ORDER BY ch.due_date DESC, ch.id DESC""").fetchall()
                heads = ["", "شماره چک", "سررسید", "مشتری", "تلفن", "مبلغ", "بانک", "وضعیت", "آخرین پیامک"]
                for c in src:
                    f = figs.get(c["customer_id"])
                    if not f or (cut and c["due_date"] < cut): continue
                    if q and not H.match(q, f["name"], f["phone"], c["cheque_no"], c["bank"], c["issuer"], c["invoice_no"]): continue
                    rows.append(dict(id=c["id"], customer_id=f["id"], name=f["name"], raw=f["phone"], phone=norm_mobile(f["phone"]), ref=c["id"],
                                     last=last.get(c["id"], ""), pj=4,
                                     cells=[c["cheque_no"] or "—", H.iso_to_j(c["due_date"]), f["name"], f["phone"], H.money(c["amount"]), c["bank"] or "", c["status"]],
                                     ctx=base(f, dict(cheque_no=c["cheque_no"] or "—", cheque_amount=m(c["amount"]), cheque_due=H.iso_to_j(c["due_date"]),
                                                      cheque_bank=c["bank"] or "—", cheque_issuer=c["issuer"] or "—", cheque_status=c["status"] or "", cheque_invoice=c["invoice_no"] or "—"))))
            else:
                src = con.execute("""SELECT r.*, i.invoice_no, COALESCE((SELECT SUM(quantity*unit_price) FROM return_items WHERE return_id=r.id),0) total
                                     FROM returns r LEFT JOIN invoices i ON i.id=r.invoice_id ORDER BY r.return_date DESC, r.id DESC""").fetchall()
                items = {}
                for it in con.execute("SELECT return_id, product_name, quantity FROM return_items ORDER BY return_id, row_no"):
                    q_ = float(it["quantity"] or 0); items.setdefault(it["return_id"], []).append(f"{it['product_name']} × {int(q_) if q_ == int(q_) else q_}")
                heads = ["", "شماره مرجوعی", "تاریخ", "مشتری", "تلفن", "مبلغ مرجوعی", "فاکتور", "آخرین پیامک"]
                for r in src:
                    f = figs.get(r["customer_id"])
                    if not f or (cut and r["return_date"] < cut): continue
                    if q and not H.match(q, f["name"], f["phone"], r["return_no"], r["invoice_no"]): continue
                    rows.append(dict(id=r["id"], customer_id=f["id"], name=f["name"], raw=f["phone"], phone=norm_mobile(f["phone"]), ref=r["id"],
                                     last=last.get(r["id"], ""), pj=4,
                                     cells=[r["return_no"], H.iso_to_j(r["return_date"]), f["name"], f["phone"], H.money(r["total"]), r["invoice_no"] or ""],
                                     ctx=base(f, dict(return_no=r["return_no"], return_date=H.iso_to_j(r["return_date"]), return_total=m(r["total"]),
                                                      return_invoice=r["invoice_no"] or "—", return_items="\n".join(items.get(r["id"], []))))))
        return rows, heads

    def _check_all(self, on):
        self.table.blockSignals(True)
        for r, rec in enumerate(self.rows):
            if rec["phone"]: self.table.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.table.blockSignals(False); self._update_count()

    def _checked(self):
        return [self.rows[r] for r in range(len(self.rows)) if self.table.item(r, 0) and self.table.item(r, 0).checkState() == Qt.Checked]

    def _update_count(self):
        n = len(self._checked()); self.sel_lbl.setText(f"{n} گیرنده انتخاب شده از {len(self.rows)} ردیف")

    def _sample_ctx(self):
        return dict(name="مشتری نمونه", balance="1,250,000", total="3,000,000", received="1,750,000", overdue="500,000", open_chq="0",
                    invoice_count="2", open_invoices="فاکتور 1001 (1405/01/10): 750,000\nفاکتور 1002 (1405/01/20): 500,000",
                    date=self.H.today_j(), shop=self._shop(), unit=self._unit(), invoice_no="1002", invoice_date="1405/01/20",
                    due_date="1405/02/20", invoice_total="1,000,000", invoice_paid="500,000", invoice_remain="500,000",
                    payment_amount="800,000", payment_discount="50,000", payment_date="1405/02/25", payment_method="کارت به کارت", payment_ref="123456", payment_invoice="1002",
                    cheque_no="5566", cheque_amount="2,000,000", cheque_due="1405/04/10", cheque_bank="ملت", cheque_issuer="مشتری نمونه", cheque_status="در انتظار وصول", cheque_invoice="1002",
                    return_no="3", return_date="1405/02/26", return_total="400,000", return_invoice="1002", return_items="لنت جلو × 2\nفیلتر روغن × 1")

    def _current_rec(self):
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else (self.rows[0] if self.rows else None)

    def _update_preview(self, *_):
        text = self.editor.toPlainText(); rec = self._current_rec()
        msg, _lk = self._compose(rec["ctx"] if rec else self._sample_ctx())
        self.preview.setPlainText(msg)
        self.counter.setText(f"{len(text)} کاراکتر در قالب — پیام نهایی حدود {len(msg)} کاراکتر ≈ {sms_parts(msg)} پیامک")

    # ── ارسال ──
    def _need_key(self):
        if (self._pref("api_key", "")).strip(): return True
        QMessageBox.warning(self, "کلید API", "ابتدا در تب «تنظیمات پنل پیامک» کلید API کاوه‌نگار را وارد کن.")
        self.sub.setCurrentIndex(2); return False

    def send_selected(self, *_, confirm=True):
        if self._busy: return
        recs = self._checked(); text = self.editor.toPlainText().strip()
        if not recs: return QMessageBox.warning(self, "ارسال", "هیچ گیرنده‌ای انتخاب نشده است.")
        if not text: return QMessageBox.warning(self, "ارسال", "متن پیام خالی است.")
        if not self._need_key(): return
        lk = self._cur_lookup()
        if lk is not None and not lk["template"]:
            return QMessageBox.warning(self, "الگوی کاوه‌نگار", "نام الگوی کاوه‌نگار وارد نشده است. «ویرایش الگوی کاوه‌نگار» را بزن و نام الگو را بنویس.")
        today = self.H.today_iso(); again = sum(1 for r in recs if r["last"][:10] == today)
        jobs = []
        for r in recs:
            m_, l_ = self._compose(r["ctx"]); jobs.append(dict(rec=r, text=m_, kind=self.kind, lookup=l_))
        parts = sum(sms_parts(j["text"]) for j in jobs)
        msg = f"{len(jobs)} پیام (حدود {parts} بخش پیامک) ارسال شود؟"
        if again: msg += f"\n\n⚠ برای {again} نفر از این‌ها امروز همین نوع پیام قبلاً ارسال شده است."
        if confirm and QMessageBox.question(self, "تأیید ارسال", msg) != QMessageBox.Yes: return
        self._start(jobs)

    def send_test(self):
        if self._busy: return
        phone = norm_mobile(self._pref("test_phone", ""))
        if not phone:
            QMessageBox.warning(self, "ارسال آزمایشی", "شماره‌ی موبایل خودت را در «تنظیمات پنل پیامک» وارد کن.")
            self.sub.setCurrentIndex(2); return
        if not self._need_key(): return
        lk = self._cur_lookup()
        if lk is not None and not lk["template"]:
            return QMessageBox.warning(self, "الگوی کاوه‌نگار", "نام الگوی کاوه‌نگار وارد نشده است. «ویرایش الگوی کاوه‌نگار» را بزن و نام الگو را بنویس.")
        rec = self._current_rec(); msg, lkj = self._compose(rec["ctx"] if rec else self._sample_ctx())
        if not msg.strip(): return QMessageBox.warning(self, "ارسال", "متن پیام خالی است.")
        job = dict(rec=dict(customer_id=None, ref=0, phone=phone, name="آزمایشی"), text=msg, kind="test", lookup=lkj)
        self._start([job])

    def _start(self, jobs):
        self._busy = True; self.jobs = jobs; self.ok_n = self.fail_n = 0; self.cancel.clear()
        self.btn_send.setEnabled(False); self.btn_test.setEnabled(False)
        self.progress = QProgressDialog("در حال ارسال پیامک...", "توقف", 0, len(jobs), self)
        self.progress.setWindowTitle("ارسال پیامک"); self.progress.setWindowModality(Qt.WindowModal); self.progress.setMinimumDuration(0)
        self.progress.canceled.connect(self.cancel.set); self.progress.setValue(0)
        key, sender = self._pref("api_key", ""), self._pref("sender", "")
        threading.Thread(target=self._worker, args=(list(jobs), key, sender), daemon=True).start()

    def _worker(self, jobs, key, sender):
        fails = 0
        for i, j in enumerate(jobs):
            if self.cancel.is_set(): break
            try:
                if j.get("lookup"): e = send_lookup_safe(key, j["rec"]["phone"], j["lookup"])
                else: e = send_sms(key, j["rec"]["phone"], j["text"], sender)
                self.sig.one.emit(i, True, str(e.get("statustext") or "در صف ارسال"), str(e.get("messageid") or "")); fails = 0
            except Exception as ex:
                self.sig.one.emit(i, False, str(ex), ""); fails += 1
                if fails >= 3: break          # مثلاً کلید اشتباه یا اعتبار تمام شده: بقیه را بی‌هدف نفرست
            time.sleep(0.12)
        self.sig.done.emit()

    def _on_one(self, i, ok, text, mid):
        j = self.jobs[i]; rec = j["rec"]
        with self.H.db() as con:
            con.execute("INSERT INTO sms_log(created_at,customer_id,ref_id,kind,phone,message,ok,status_text,message_id) VALUES(?,?,?,?,?,?,?,?,?)",
                        (datetime.now().isoformat(timespec="seconds"), rec.get("customer_id"), rec.get("ref") or 0, j["kind"],
                         rec["phone"], j["text"], 1 if ok else 0, text, mid))
        if ok: self.ok_n += 1
        else: self.fail_n += 1; self.last_error = f"{rec.get('name', '')}: {text}"
        if self.progress: self.progress.setValue(self.ok_n + self.fail_n)

    def _on_done(self):
        self._busy = False; self.btn_send.setEnabled(True); self.btn_test.setEnabled(True)
        if self.progress: self.progress.close(); self.progress = None
        left = len(self.jobs) - self.ok_n - self.fail_n
        msg = f"ارسال موفق: {self.ok_n}\nناموفق: {self.fail_n}" + (f"\nارسال‌نشده (متوقف شد): {left}" if left else "")
        if self.fail_n: msg += f"\n\nآخرین خطا:\n{getattr(self, 'last_error', '')}"
        (QMessageBox.warning if self.fail_n else QMessageBox.information)(self, "نتیجه‌ی ارسال", msg)
        self.refresh(); self.refresh_history()

    # ── از بخش‌های دیگر برنامه ──
    def quick_invoice(self, iid):
        """بعد از ثبت فاکتور: پیامک همان فاکتور با قالب پیش‌فرض ارسال می‌شود (تأیید از کاربر قبلاً گرفته شده)."""
        if self._busy: return
        if not (self._pref("api_key", "")).strip(): return
        self.focus(KIND_INVOICE, iid)
        if not self._checked():
            return QMessageBox.warning(self, "ارسال پیامک", "برای این مشتری شماره‌ی موبایل معتبر ثبت نشده است.")
        self.send_selected(confirm=False)

    def focus(self, kind, ident):
        """رفتن به تب پیامک با یک مشتری (kind=total/detail) یا فاکتور (kind=invoice) تیک‌خورده."""
        self.sub.setCurrentIndex(0)
        self.search.blockSignals(True); self.search.clear(); self.search.blockSignals(False)
        self.only_debtors.blockSignals(True); self.only_debtors.setChecked(False); self.only_debtors.blockSignals(False)
        self.period.blockSignals(True); self.period.setCurrentIndex(0); self.period.blockSignals(False)
        self.only_open.blockSignals(True); self.only_open.setChecked(False); self.only_open.blockSignals(False)
        i = self.kind_combo.findData(kind)
        if self.kind_combo.currentIndex() != i: self.kind_combo.setCurrentIndex(i)
        else: self.refresh()
        for r, rec in enumerate(self.rows):
            if rec["id"] == ident:
                if rec["phone"]: self.table.item(r, 0).setCheckState(Qt.Checked)
                self.table.selectRow(r); self.table.scrollToItem(self.table.item(r, 1)); break

    # ───────────── تب سوابق ─────────────
    def _build_history(self):
        w = QWidget(); l = QVBoxLayout(w)
        self.h_search = QLineEdit(); self.h_search.setPlaceholderText("جست‌وجو در نام، شماره یا متن پیام‌ها...")
        self.h_search.textChanged.connect(lambda _t: self.refresh_history())
        row = QHBoxLayout(); row.addWidget(self.h_search, 1)
        b1 = QPushButton("به‌روزرسانی وضعیت تحویل (ردیف انتخاب‌شده)"); b1.clicked.connect(self._update_status)
        b2 = QPushButton("تازه‌سازی"); b2.clicked.connect(self.refresh_history)
        row.addWidget(b1); row.addWidget(b2); l.addLayout(row)
        heads = ["شناسه", "تاریخ و ساعت", "مشتری", "شماره", "نوع", "نتیجه", "وضعیت", "متن پیام"]
        self.h_table = QTableWidget(0, len(heads)); self.h_table.setHorizontalHeaderLabels(heads)
        self.h_table.setAlternatingRowColors(True); self.h_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.h_table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.h_table.verticalHeader().setVisible(False)
        self.h_table.horizontalHeader().setStretchLastSection(True); self.h_table.setColumnHidden(0, True)
        l.addWidget(self.h_table); self.sub.addTab(w, "سوابق ارسال"); self.refresh_history()

    def refresh_history(self):
        if not hasattr(self, "h_table"): return
        q = self.h_search.text().strip()
        with self.H.db() as con:
            rows = con.execute("""SELECT s.*, COALESCE(c.name,'') cname FROM sms_log s LEFT JOIN customers c ON c.id=s.customer_id
                ORDER BY s.id DESC LIMIT 3000""").fetchall()
        rows = [r for r in rows if self.H.match(q, r["cname"], r["phone"], r["message"], KIND_LABEL.get(r["kind"], r["kind"]))][:500]
        t = self.h_table; t.setRowCount(0)
        for r in rows:
            n = t.rowCount(); t.insertRow(n)
            when = r["created_at"]
            vals = [r["id"], f"{self.H.iso_to_j(when[:10])}  {when[11:16]}", r["cname"], r["phone"], KIND_LABEL.get(r["kind"], r["kind"]),
                    "موفق" if r["ok"] else "ناموفق", r["status_text"], r["message"].replace("\n", " ⏎ ")]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v)); it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j == 5: it.setForeground(GREEN if r["ok"] else RED)
                t.setItem(n, j, it)
        t.resizeColumnsToContents(); t.horizontalHeader().setStretchLastSection(True)

    def _update_status(self):
        r = self.h_table.currentRow()
        if r < 0: return QMessageBox.warning(self, "وضعیت", "یک ردیف از سوابق را انتخاب کن.")
        lid = int(self.h_table.item(r, 0).text())
        with self.H.db() as con: row = con.execute("SELECT message_id FROM sms_log WHERE id=?", (lid,)).fetchone()
        if not row or not row["message_id"]: return QMessageBox.information(self, "وضعیت", "این پیام شناسه‌ی کاوه‌نگار ندارد (ارسال نشده است).")
        if not self._need_key(): return
        try: st = message_status(self._pref("api_key", ""), [row["message_id"]])
        except Exception as e: return QMessageBox.warning(self, "وضعیت", str(e))
        txt = st.get(str(row["message_id"]))
        if txt:
            with self.H.db() as con: con.execute("UPDATE sms_log SET status_text=? WHERE id=?", (txt, lid))
            self.refresh_history()
        QMessageBox.information(self, "وضعیت تحویل", txt or "وضعیتی برگردانده نشد.")

    # ───────────── تب تنظیمات ─────────────
    def _build_settings(self):
        w = QWidget(); l = QVBoxLayout(w); form = QFormLayout()
        def field(key, ph, default="", password=False):
            e = QLineEdit(self._pref(key, default)); e.setPlaceholderText(ph)
            if password: e.setEchoMode(QLineEdit.Password)
            e.editingFinished.connect(lambda: (self.H.set_pref("sms_" + key, e.text().strip()), self._apply_prefs(), self._update_preview()))
            return e
        self.f_key = field("api_key", "کلید API از پنل کاوه‌نگار", password=True)
        show = QCheckBox("نمایش کلید"); show.toggled.connect(lambda v: self.f_key.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        krow = QHBoxLayout(); krow.addWidget(self.f_key, 1); krow.addWidget(show)
        form.addRow("کلید API:", krow)
        form.addRow("شماره‌ی خط ارسال‌کننده:", field("sender", "خالی = خط پیش‌فرض حساب کاوه‌نگار (مثلاً 10004346)"))
        form.addRow("واحد پول در پیام:", field("unit", "تومان", "تومان"))
        self.f_space = field("space_char", "_", "_")
        form.addRow("جایگزین فاصله در token/token2/token3:", self.f_space)
        form.addRow("امضای پایین پیام ({shop}):", field("shop", "خالی = نام فروشگاه از «تنظیمات چاپ»"))
        form.addRow("شماره‌ی موبایل من (برای تست):", field("test_phone", "09123456789"))
        l.addLayout(form)
        bt = QPushButton("بررسی اتصال و اعتبار حساب"); bt.clicked.connect(self._check_account)
        row = QHBoxLayout(); row.addWidget(bt); row.addStretch(); l.addLayout(row)
        self.acct_lbl = QLabel(""); self.acct_lbl.setWordWrap(True); l.addWidget(self.acct_lbl)
        help_ = QLabel("راهنما:\n• ثبت‌نام و دریافت کلید API: console.kavenegar.com ← بخش «تنظیمات حساب» ← «API Key».\n"
                       "• خط ارسال‌کننده را از بخش «خطوط» همان پنل می‌توانی ببینی؛ اگر خالی بماند، خط پیش‌فرض حساب استفاده می‌شود.\n"
                       "• هزینه‌ی پیامک از اعتبار حساب کاوه‌نگار کم می‌شود؛ پیامک فارسی در هر بخش ۷۰ کاراکتر است.\n"
                       "• کلید روی همین کامپیوتر (داخل دیتابیس برنامه و پشتیبان‌ها) ذخیره می‌شود؛ فایل پشتیبان را دست کسی نده.\n"
                       "• متغیرها داخل متن پیام به شکل {name}، {balance} و … گذاشته می‌شوند (در تب «ارسال پیامک» از «درج متغیر» استفاده کن).")
        help_.setWordWrap(True); l.addWidget(help_); l.addStretch()
        self.sub.addTab(w, "تنظیمات پنل پیامک")

    def _check_account(self):
        self.H.set_pref("sms_api_key", self.f_key.text().strip())
        if not self._need_key(): return
        self.acct_lbl.setText("در حال بررسی...")
        QApplication.processEvents()
        try:
            a = account_info(self._pref("api_key", ""))
            cred = a.get("remaincredit")
            try: cred_txt = f"{int(float(cred)):,} ریال"
            except Exception: cred_txt = str(cred)
            self.acct_lbl.setText(f"✔ اتصال برقرار است.\nاعتبار باقی‌مانده: {cred_txt}\nنوع حساب: {a.get('type', '—')}")
        except Exception as e:
            self.acct_lbl.setText(f"✘ {e}")
