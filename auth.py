# -*- coding: utf-8 -*-
"""کاربران، رمز ورود، سطح دسترسی و گزارش فعالیت (فاکتور یار).

- رمزها هرگز ذخیره نمی‌شوند؛ فقط هش PBKDF2-SHA256 با نمک تصادفی.
- نقش‌های آماده: مدیر (همه‌چیز)، حسابدار، فروشنده؛ یا «سفارشی» با تیک‌زدن دسترسی‌ها.
- رمز بازیابی (یک‌بار هنگام ساخت مدیر نشان داده می‌شود) برای وقتی که رمز مدیر فراموش شد.
"""
import hashlib, hmac, json, secrets
from datetime import datetime
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QMessageBox, QFormLayout, QDialogButtonBox, QTabWidget, QSpinBox, QTextEdit,
)
from ui_fit import QDialog

ITERS = 200_000
MIN_PW = 4
PERMS = [("delete", "حذف فاکتور، پیش‌فاکتور، مشتری، دریافت و چک"),
         ("edit", "ویرایش فاکتورها و پیش‌فاکتورهای ثبت‌شده"),
         ("reports", "تب گزارش‌ها و بدهکاران"),
         ("sms", "تب پیامک"),
         ("settings", "تب تنظیمات (پشتیبان، بازیابی، کاردکس، تنظیمات چاپ)"),
         ("purchases", "تب‌های خرید و تأمین‌کنندگان (فاکتور خرید، برگشت از خرید، پرداخت به تأمین‌کننده)")]
ALL_PERMS = [k for k, _ in PERMS]
ROLES = {"admin": ("مدیر", ALL_PERMS), "accountant": ("حسابدار", ["edit", "delete", "reports", "sms", "purchases"]),
         "seller": ("فروشنده", ["edit"]), "custom": ("سفارشی", [])}


def role_label(role): return ROLES.get(role, (role, []))[0]


# ───────────────────────────── دیتابیس ─────────────────────────────
def ensure_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, display TEXT DEFAULT '',
      salt TEXT NOT NULL, hash TEXT NOT NULL, iters INTEGER NOT NULL DEFAULT 200000,
      role TEXT NOT NULL DEFAULT 'seller', perms TEXT DEFAULT '[]', active INTEGER DEFAULT 1,
      created_at TEXT DEFAULT '', last_login TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS audit_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, user TEXT DEFAULT '', action TEXT NOT NULL, detail TEXT DEFAULT ''
    );
    """)


def hash_pw(pw, salt=None, iters=ITERS):
    salt = salt or secrets.token_hex(16)
    return salt, hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), iters).hex(), iters


def check_pw(pw, salt, h, iters):
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(iters)).hex(), h)


class Session:
    """کاربر واردشده. user=None (قبل از ورود/آزمایش) یعنی بدون محدودیت."""
    def __init__(self, user=None): self.user = user
    @property
    def username(self): return self.user["username"] if self.user else ""
    @property
    def display(self): return (self.user.get("display") or self.user["username"]) if self.user else ""
    def is_admin(self): return self.user is None or self.user["role"] == "admin"
    def can(self, perm):
        if self.user is None or self.user["role"] == "admin": return True
        try: return perm in json.loads(self.user.get("perms") or "[]")
        except Exception: return False


def count_users(H):
    with H.db() as con: return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def list_users(H):
    with H.db() as con: return [dict(r) for r in con.execute("SELECT * FROM users ORDER BY id")]


def get_user(H, username):
    with H.db() as con:
        r = con.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    return dict(r) if r else None


def _active_admins(con, exclude_id=None):
    q = "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1" + (" AND id<>?" if exclude_id else "")
    return con.execute(q, (exclude_id,) if exclude_id else ()).fetchone()[0]


def create_user(H, username, display, password, role, perms):
    username = username.strip()
    if not username: raise ValueError("نام کاربری را بنویس.")
    if len(password) < MIN_PW: raise ValueError(f"رمز حداقل {MIN_PW} کاراکتر باشد.")
    salt, h, it = hash_pw(password)
    perms = ALL_PERMS if role == "admin" else [p for p in perms if p in ALL_PERMS]
    with H.db() as con:
        if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone(): raise ValueError("این نام کاربری از قبل هست.")
        return con.execute("INSERT INTO users(username,display,salt,hash,iters,role,perms,active,created_at) VALUES(?,?,?,?,?,?,?,1,?)",
                           (username, display.strip(), salt, h, it, role, json.dumps(perms), datetime.now().isoformat(timespec="seconds"))).lastrowid


def update_user(H, uid, display, role, perms, active):
    perms = ALL_PERMS if role == "admin" else [p for p in perms if p in ALL_PERMS]
    with H.db() as con:
        cur = con.execute("SELECT role, active FROM users WHERE id=?", (uid,)).fetchone()
        if not cur: raise ValueError("کاربر پیدا نشد.")
        if cur["role"] == "admin" and cur["active"] and (role != "admin" or not active) and _active_admins(con, uid) == 0:
            raise ValueError("حداقل یک مدیر فعال باید بماند.")
        con.execute("UPDATE users SET display=?, role=?, perms=?, active=? WHERE id=?", (display.strip(), role, json.dumps(perms), 1 if active else 0, uid))


def set_password(H, uid, password):
    if len(password) < MIN_PW: raise ValueError(f"رمز حداقل {MIN_PW} کاراکتر باشد.")
    salt, h, it = hash_pw(password)
    with H.db() as con: con.execute("UPDATE users SET salt=?, hash=?, iters=? WHERE id=?", (salt, h, it, uid))


def delete_user(H, uid):
    with H.db() as con:
        cur = con.execute("SELECT role, active FROM users WHERE id=?", (uid,)).fetchone()
        if not cur: return
        if cur["role"] == "admin" and cur["active"] and _active_admins(con, uid) == 0: raise ValueError("آخرین مدیر فعال را نمی‌شود حذف کرد.")
        con.execute("DELETE FROM users WHERE id=?", (uid,))


def verify_login(H, username, password):
    """کاربر (dict) در صورت درست‌بودن نام و رمز و فعال‌بودن؛ وگرنه None."""
    u = get_user(H, username)
    if not u or not u["active"]: 
        hash_pw(password or "x")          # زمان پاسخ برای کاربر ناموجود هم مشابه می‌ماند
        return None
    if not check_pw(password or "", u["salt"], u["hash"], u["iters"]): return None
    with H.db() as con: con.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), u["id"]))
    return u


def audit(H, user, action, detail=""):
    try:
        with H.db() as con:
            con.execute("INSERT INTO audit_log(at,user,action,detail) VALUES(?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), user or "", action, detail or ""))
    except Exception:
        pass


# رمز بازیابی
_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_recovery(H):
    code = "-".join("".join(secrets.choice(_ALPHA) for _ in range(4)) for _ in range(3))
    salt, h, it = hash_pw(code)
    with H.db() as con: con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('pref_auth_recovery',?)", (f"{salt}:{h}:{it}",))
    return code


def use_recovery(H, code, username, new_password):
    """با رمز بازیابی رمز یک مدیر را عوض می‌کند و رمز بازیابی جدید برمی‌گرداند."""
    with H.db() as con: r = con.execute("SELECT value FROM settings WHERE key='pref_auth_recovery'").fetchone()
    if not r or r["value"].count(":") != 2: raise ValueError("رمز بازیابی ساخته نشده است.")
    salt, h, it = r["value"].split(":")
    if not check_pw((code or "").strip().upper(), salt, h, it): raise ValueError("رمز بازیابی درست نیست.")
    u = get_user(H, username)
    if not u or u["role"] != "admin" or not u["active"]: raise ValueError("این نام کاربری مدیر فعال نیست.")
    set_password(H, u["id"], new_password)
    return make_recovery(H)


# ───────────────────────────── پنجره‌ها ─────────────────────────────
def _pw_edit(ph=""):
    e = QLineEdit(); e.setEchoMode(QLineEdit.Password); e.setPlaceholderText(ph); return e


class RecoveryShowDialog(QDialog):
    def __init__(self, code, parent=None):
        super().__init__(parent); self.setWindowTitle("رمز بازیابی"); self.resize(460, 260)
        l = QVBoxLayout(self)
        l.addWidget(QLabel("اگر رمز مدیر را فراموش کردی، فقط با این کد می‌توانی رمز را عوض کنی.\nآن را همین حالا جایی امن (کاغذ یا گوشی) یادداشت کن؛ دیگر نشان داده نمی‌شود."))
        self.code = QLineEdit(code); self.code.setReadOnly(True); self.code.setAlignment(Qt.AlignCenter); self.code.setLayoutDirection(Qt.LeftToRight)
        self.code.setStyleSheet("font-size:18pt; font-weight:bold; letter-spacing:2px;"); l.addWidget(self.code)
        self.ok_chk = QCheckBox("کد را یادداشت کردم"); l.addWidget(self.ok_chk)
        self.btn = QPushButton("ادامه"); self.btn.setEnabled(False); self.ok_chk.toggled.connect(self.btn.setEnabled); self.btn.clicked.connect(self.accept); l.addWidget(self.btn)

    def reject(self): pass          # بستن بدون تأیید ممکن نیست


class FirstSetupDialog(QDialog):
    """اولین اجرا: ساخت حساب مدیر."""
    def __init__(self, H, parent=None):
        super().__init__(parent); self.H = H; self.user = None
        self.setWindowTitle("راه‌اندازی اولیه — ساخت حساب مدیر"); self.resize(460, 330)
        l = QVBoxLayout(self)
        l.addWidget(QLabel("برای امن‌ماندن اطلاعات، برنامه از این به بعد با نام کاربری و رمز باز می‌شود.\nاول یک حساب مدیر بساز؛ بعداً می‌توانی برای فروشنده و حسابدار حساب جدا با دسترسی محدود بسازی."))
        f = QFormLayout(); self.u = QLineEdit("admin"); self.d = QLineEdit("مدیر"); self.p1 = _pw_edit(); self.p2 = _pw_edit()
        f.addRow("نام کاربری:", self.u); f.addRow("نام نمایشی:", self.d); f.addRow("رمز:", self.p1); f.addRow("تکرار رمز:", self.p2); l.addLayout(f)
        self.msg = QLabel(""); self.msg.setStyleSheet("color:#c62828;"); l.addWidget(self.msg)
        b = QPushButton("ساخت حساب و ورود"); b.clicked.connect(self._go); l.addWidget(b)
        bq = QPushButton("خروج"); bq.clicked.connect(self.reject); l.addWidget(bq)

    def _go(self):
        if self.p1.text() != self.p2.text(): return self.msg.setText("رمز و تکرارش یکی نیست.")
        try: uid = create_user(self.H, self.u.text(), self.d.text(), self.p1.text(), "admin", ALL_PERMS)
        except ValueError as e: return self.msg.setText(str(e))
        code = make_recovery(self.H)
        RecoveryShowDialog(code, self).exec()
        self.user = get_user(self.H, self.u.text()); audit(self.H, self.user["username"], "ساخت حساب مدیر"); self.accept()


class RecoveryDialog(QDialog):
    def __init__(self, H, username="", parent=None):
        super().__init__(parent); self.H = H; self.setWindowTitle("فراموشی رمز مدیر"); self.resize(440, 300)
        l = QVBoxLayout(self); f = QFormLayout()
        self.u = QLineEdit(username); self.c = QLineEdit(); self.c.setPlaceholderText("XXXX-XXXX-XXXX"); self.c.setLayoutDirection(Qt.LeftToRight)
        self.p1 = _pw_edit(); self.p2 = _pw_edit()
        f.addRow("نام کاربری مدیر:", self.u); f.addRow("رمز بازیابی:", self.c); f.addRow("رمز جدید:", self.p1); f.addRow("تکرار رمز جدید:", self.p2); l.addLayout(f)
        self.msg = QLabel(""); self.msg.setStyleSheet("color:#c62828;"); l.addWidget(self.msg)
        b = QPushButton("تغییر رمز"); b.clicked.connect(self._go); l.addWidget(b)

    def _go(self):
        if self.p1.text() != self.p2.text(): return self.msg.setText("رمز و تکرارش یکی نیست.")
        try: new_code = use_recovery(self.H, self.c.text(), self.u.text(), self.p1.text())
        except ValueError as e: return self.msg.setText(str(e))
        audit(self.H, self.u.text().strip(), "تغییر رمز با رمز بازیابی")
        RecoveryShowDialog(new_code, self).exec(); self.accept()


class LoginDialog(QDialog):
    def __init__(self, H, username="", locked=False, parent=None):
        super().__init__(parent); self.H = H; self.user = None; self.fails = 0; self._wait = 0
        self.setWindowTitle("برنامه قفل است" if locked else "ورود به فاکتور یار"); self.resize(420, 300)
        l = QVBoxLayout(self)
        t = QLabel("🔒 برنامه قفل شد؛ برای ادامه رمزت را بزن." if locked else "ورود به فاکتور یار"); t.setStyleSheet("font-size:13pt; font-weight:bold;"); l.addWidget(t)
        f = QFormLayout(); self.u = QLineEdit(username); self.p = _pw_edit(); f.addRow("نام کاربری:", self.u); f.addRow("رمز:", self.p); l.addLayout(f)
        self.msg = QLabel(""); self.msg.setStyleSheet("color:#c62828;"); l.addWidget(self.msg)
        self.btn = QPushButton("ورود"); self.btn.setDefault(True); self.btn.clicked.connect(self._go); l.addWidget(self.btn)
        self.p.returnPressed.connect(self._go); self.u.returnPressed.connect(lambda: self.p.setFocus())
        row = QHBoxLayout(); bf = QPushButton("رمز را فراموش کرده‌ام"); bf.clicked.connect(self._forgot)
        bq = QPushButton("خروج از برنامه"); bq.clicked.connect(self.reject); row.addWidget(bf); row.addWidget(bq); l.addLayout(row)
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick)
        (self.p if username else self.u).setFocus()

    def _tick(self):
        self._wait -= 1
        if self._wait <= 0:
            self.timer.stop(); self.btn.setEnabled(True); self.btn.setText("ورود")
        else: self.btn.setText(f"صبر کن ({self._wait})")

    def _go(self):
        if self._wait > 0: return
        u = verify_login(self.H, self.u.text(), self.p.text())
        if u:
            audit(self.H, u["username"], "ورود موفق"); self.user = u; self.accept(); return
        self.fails += 1; audit(self.H, self.u.text().strip(), "ورود ناموفق"); self.p.clear()
        self.msg.setText("نام کاربری یا رمز درست نیست، یا حساب غیرفعال است.")
        if self.fails >= 3:                    # هر بار بعد از سومین اشتباه چند ثانیه صبر
            self._wait = min(60, 5 * (self.fails - 2)); self.btn.setEnabled(False); self._tick_start()

    def _tick_start(self):
        self.btn.setText(f"صبر کن ({self._wait})"); self.timer.start(1000)

    def _forgot(self):
        RecoveryDialog(self.H, self.u.text(), self).exec()


def login_flow(H):
    """اولین اجرا → ساخت مدیر؛ بعد از آن → ورود. کاربر (dict) یا None (کاربر منصرف شد)."""
    if count_users(H) == 0:
        d = FirstSetupDialog(H)
        return d.user if d.exec() and d.user else None
    d = LoginDialog(H, H.get_pref("last_user", ""))
    if d.exec() and d.user:
        H.set_pref("last_user", d.user["username"]); return d.user
    return None


class ChangePasswordDialog(QDialog):
    def __init__(self, H, user, parent=None):
        super().__init__(parent); self.H = H; self.user = user; self.setWindowTitle("تغییر رمز من"); self.resize(400, 260)
        l = QVBoxLayout(self); f = QFormLayout(); self.o = _pw_edit(); self.n1 = _pw_edit(); self.n2 = _pw_edit()
        f.addRow("رمز فعلی:", self.o); f.addRow("رمز جدید:", self.n1); f.addRow("تکرار رمز جدید:", self.n2); l.addLayout(f)
        self.msg = QLabel(""); self.msg.setStyleSheet("color:#c62828;"); l.addWidget(self.msg)
        b = QPushButton("ذخیره"); b.clicked.connect(self._go); l.addWidget(b)

    def _go(self):
        u = get_user(self.H, self.user["username"])
        if not u or not check_pw(self.o.text(), u["salt"], u["hash"], u["iters"]): return self.msg.setText("رمز فعلی درست نیست.")
        if self.n1.text() != self.n2.text(): return self.msg.setText("رمز جدید و تکرارش یکی نیست.")
        try: set_password(self.H, u["id"], self.n1.text())
        except ValueError as e: return self.msg.setText(str(e))
        audit(self.H, u["username"], "تغییر رمز"); self.accept()


class UserEditDialog(QDialog):
    def __init__(self, H, user=None, parent=None):
        super().__init__(parent); self.H = H; self.user = user
        self.setWindowTitle("کاربر جدید" if not user else f"ویرایش کاربر: {user['username']}"); self.resize(500, 520)
        l = QVBoxLayout(self); f = QFormLayout()
        self.u = QLineEdit(user["username"] if user else ""); self.u.setEnabled(user is None)
        self.d = QLineEdit(user["display"] if user else "")
        f.addRow("نام کاربری:", self.u); f.addRow("نام نمایشی:", self.d)
        self.p1 = _pw_edit(); self.p2 = _pw_edit()
        if user is None: f.addRow("رمز:", self.p1); f.addRow("تکرار رمز:", self.p2)
        self.role = QComboBox()
        for k, (t, _p) in ROLES.items(): self.role.addItem(t, k)
        f.addRow("نقش:", self.role); l.addLayout(f)
        l.addWidget(QLabel("دسترسی‌ها:"))
        self.chk = {}
        for k, t in PERMS:
            c = QCheckBox(t); self.chk[k] = c; l.addWidget(c)
        self.active = QCheckBox("حساب فعال است"); self.active.setChecked(True); l.addWidget(self.active)
        self.msg = QLabel(""); self.msg.setStyleSheet("color:#c62828;"); l.addWidget(self.msg)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.accepted.connect(self._ok); bb.rejected.connect(self.reject); l.addWidget(bb)
        role = user["role"] if user else "seller"
        self.role.setCurrentIndex(max(0, self.role.findData(role)))
        self.role.currentIndexChanged.connect(self._role_changed)
        if user:
            try: have = set(json.loads(user["perms"] or "[]"))
            except Exception: have = set()
            for k, c in self.chk.items(): c.setChecked(k in have or role == "admin")
            self.active.setChecked(bool(user["active"]))
        else: self._role_changed()
        self._lock_boxes()

    def _lock_boxes(self):
        admin = self.role.currentData() == "admin"
        for c in self.chk.values(): c.setEnabled(not admin)

    def _role_changed(self, *_):
        r = self.role.currentData()
        if r != "custom":
            for k, c in self.chk.items(): c.setChecked(k in ROLES[r][1])
        self._lock_boxes()

    def _ok(self):
        role = self.role.currentData(); perms = [k for k, c in self.chk.items() if c.isChecked()]
        try:
            if self.user is None:
                if self.p1.text() != self.p2.text(): return self.msg.setText("رمز و تکرارش یکی نیست.")
                create_user(self.H, self.u.text(), self.d.text(), self.p1.text(), role, perms)
            else:
                update_user(self.H, self.user["id"], self.d.text(), role, perms, self.active.isChecked())
        except ValueError as e:
            return self.msg.setText(str(e))
        self.accept()


class UsersDialog(QDialog):
    """مدیریت کاربران، گزارش فعالیت و تنظیم قفل خودکار (فقط مدیر)."""
    def __init__(self, H, me, parent=None):
        super().__init__(parent); self.H = H; self.me = me
        self.setWindowTitle("کاربران و دسترسی‌ها"); self.resize(900, 600)
        root = QVBoxLayout(self); tabs = QTabWidget(); root.addWidget(tabs)
        # کاربران
        w = QWidget(); l = QVBoxLayout(w)
        self.t = QTableWidget(0, 6); self.t.setHorizontalHeaderLabels(["شناسه", "نام کاربری", "نام نمایشی", "نقش", "وضعیت", "آخرین ورود"])
        self._style(self.t); self.t.setColumnHidden(0, True); l.addWidget(self.t)
        row = QHBoxLayout()
        for txt, fn in (("＋ کاربر جدید", self._add), ("ویرایش", self._edit), ("تغییر رمز کاربر", self._pw), ("حذف", self._del)):
            b = QPushButton(txt); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(); l.addLayout(row); tabs.addTab(w, "کاربران")
        # گزارش فعالیت
        w2 = QWidget(); l2 = QVBoxLayout(w2)
        self.s = QLineEdit(); self.s.setPlaceholderText("جست‌وجو در گزارش فعالیت..."); self.s.textChanged.connect(lambda _t: self._load_log()); l2.addWidget(self.s)
        self.lg = QTableWidget(0, 4); self.lg.setHorizontalHeaderLabels(["زمان", "کاربر", "رویداد", "جزئیات"]); self._style(self.lg); l2.addWidget(self.lg)
        tabs.addTab(w2, "گزارش فعالیت")
        # امنیت
        w3 = QWidget(); l3 = QVBoxLayout(w3); f = QFormLayout()
        self.lock_min = QSpinBox(); self.lock_min.setRange(0, 600); self.lock_min.setSuffix(" دقیقه"); self.lock_min.setSpecialValueText("خاموش")
        try: self.lock_min.setValue(int(float(H.get_pref("auto_lock_min", "0") or 0)))
        except Exception: self.lock_min.setValue(0)
        self.lock_min.valueChanged.connect(lambda v: H.set_pref("auto_lock_min", v))
        f.addRow("قفل خودکار بعد از بی‌کاری:", self.lock_min); l3.addLayout(f)
        br = QPushButton("ساخت رمز بازیابی جدید (قبلی باطل می‌شود)"); br.clicked.connect(self._new_recovery); l3.addWidget(br)
        l3.addWidget(QLabel("قفل دستی: Ctrl+L یا منوی کاربر در نوار پایین پنجره. فراموشی رمز مدیر فقط با «رمز بازیابی» ممکن است؛ آن را جای امن نگه دار."))
        l3.addStretch(); tabs.addTab(w3, "امنیت")
        self._load(); self._load_log()

    @staticmethod
    def _style(t):
        t.setAlternatingRowColors(True); t.setSelectionBehavior(QAbstractItemView.SelectRows); t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False); t.horizontalHeader().setStretchLastSection(True)

    def _load(self):
        self.t.setRowCount(0)
        for u in list_users(self.H):
            r = self.t.rowCount(); self.t.insertRow(r)
            when = (u["last_login"] or "").replace("T", "  ")[:16]
            for c, v in enumerate([u["id"], u["username"], u["display"], role_label(u["role"]), "فعال" if u["active"] else "غیرفعال", when]):
                it = QTableWidgetItem(str(v)); it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter); self.t.setItem(r, c, it)
        self.t.resizeColumnsToContents(); self.t.horizontalHeader().setStretchLastSection(True)

    def _load_log(self):
        q = self.s.text().strip(); self.lg.setRowCount(0)
        with self.H.db() as con: rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 800").fetchall()
        for a in rows:
            if q and not self.H.match(q, a["user"], a["action"], a["detail"]): continue
            r = self.lg.rowCount(); self.lg.insertRow(r)
            for c, v in enumerate([self.H.when(a["at"]), a["user"], a["action"], a["detail"]]):
                it = QTableWidgetItem(str(v)); it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter); self.lg.setItem(r, c, it)
        self.lg.resizeColumnsToContents(); self.lg.horizontalHeader().setStretchLastSection(True)

    def _sel(self):
        r = self.t.currentRow()
        if r < 0: QMessageBox.information(self, "کاربر", "ابتدا یک کاربر را انتخاب کن."); return None
        uid = int(self.t.item(r, 0).text())
        return next((u for u in list_users(self.H) if u["id"] == uid), None)

    def _add(self):
        d = UserEditDialog(self.H, None, self)
        if d.exec(): audit(self.H, self.me["username"], "ساخت کاربر", d.u.text().strip()); self._load(); self._load_log()

    def _edit(self):
        u = self._sel()
        if not u: return
        d = UserEditDialog(self.H, u, self)
        if d.exec(): audit(self.H, self.me["username"], "ویرایش کاربر", u["username"]); self._load(); self._load_log()

    def _pw(self):
        u = self._sel()
        if not u: return
        d = QDialog(self); d.setWindowTitle(f"رمز جدید برای {u['username']}"); d.resize(380, 200); l = QVBoxLayout(d); f = QFormLayout()
        a, b = _pw_edit(), _pw_edit(); f.addRow("رمز جدید:", a); f.addRow("تکرار:", b); l.addLayout(f)
        msg = QLabel(""); msg.setStyleSheet("color:#c62828;"); l.addWidget(msg)
        def go():
            if a.text() != b.text(): return msg.setText("رمز و تکرارش یکی نیست.")
            try: set_password(self.H, u["id"], a.text())
            except ValueError as e: return msg.setText(str(e))
            d.accept()
        ok = QPushButton("ذخیره"); ok.clicked.connect(go); l.addWidget(ok)
        if d.exec(): audit(self.H, self.me["username"], "تغییر رمز کاربر", u["username"]); self._load_log()

    def _del(self):
        u = self._sel()
        if not u: return
        if u["id"] == self.me["id"]: return QMessageBox.warning(self, "حذف", "حساب خودت را نمی‌شود حذف کرد.")
        if QMessageBox.question(self, "حذف کاربر", f"کاربر «{u['username']}» حذف شود؟ (سوابق فعالیتش می‌ماند)") != QMessageBox.Yes: return
        try: delete_user(self.H, u["id"])
        except ValueError as e: return QMessageBox.warning(self, "حذف", str(e))
        audit(self.H, self.me["username"], "حذف کاربر", u["username"]); self._load(); self._load_log()

    def _new_recovery(self):
        if QMessageBox.question(self, "رمز بازیابی", "رمز بازیابی قبلی باطل و یکی جدید ساخته شود؟") != QMessageBox.Yes: return
        code = make_recovery(self.H); audit(self.H, self.me["username"], "ساخت رمز بازیابی جدید"); RecoveryShowDialog(code, self).exec()
