# -*- coding: utf-8 -*-
"""بخش‌های ظاهریِ حالت کلاینت: تنظیم/آزمایش اتصال، ورود از طریق سرور، و نشانگر وضعیت اتصال."""
import sys, json
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QSpinBox)
from ui_fit import QDialog
import netdb
from netdb import REMOTE


class NetSettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent); self.setWindowTitle("اتصال به سرور فروشگاه"); self.resize(460, 260)
        l = QVBoxLayout(self)
        t = QLabel("آدرس کامپیوتر سرور را وارد کن (IP که در برنامه‌ی «فاکتور یار سرور» نوشته شده)."); t.setWordWrap(True); l.addWidget(t)
        f = QFormLayout(); self.host = QLineEdit(cfg.get("host", "")); self.host.setPlaceholderText("مثلاً 192.168.1.10"); self.host.setLayoutDirection(Qt.LeftToRight)
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(int(cfg.get("port", netdb.DEFAULT_PORT)))
        f.addRow("آدرس سرور:", self.host); f.addRow("پورت:", self.port); l.addLayout(f)
        self.msg = QLabel(""); self.msg.setWordWrap(True); l.addWidget(self.msg)
        row = QHBoxLayout(); b1 = QPushButton("آزمایش اتصال"); b1.clicked.connect(self._test); b2 = QPushButton("ذخیره و ادامه"); b2.setDefault(True); b2.clicked.connect(self._ok)
        b3 = QPushButton("خروج"); b3.clicked.connect(self.reject); row.addWidget(b1); row.addWidget(b2); row.addWidget(b3); l.addLayout(row)

    def _test(self):
        REMOTE.configure(self.host.text(), self.port.value())
        try:
            d = REMOTE.status(timeout=4); self.msg.setStyleSheet("color:#2e7d32;"); self.msg.setText("✔ اتصال برقرار است." + ("" if d.get("users") else "\nهنوز هیچ کاربری روی سرور ساخته نشده؛ ابتدا در برنامه‌ی سرور مدیر بساز."))
            return True
        except Exception as e:
            self.msg.setStyleSheet("color:#c62828;"); self.msg.setText(f"✘ {e}"); return False

    def _ok(self):
        if self._test(): self.accept()


def client_setup(app_dir):
    """تا وقتی اتصال برقرار نشده، پنجره‌ی تنظیم را نشان می‌دهد. False = کاربر منصرف شد."""
    cfg = netdb.load_config(app_dir)
    if cfg.get("host"):
        REMOTE.configure(cfg["host"], cfg.get("port", netdb.DEFAULT_PORT))
        try: REMOTE.status(timeout=4); REMOTE.start_monitor(); return True
        except Exception: pass
    while True:
        d = NetSettingsDialog(cfg)
        if not d.exec(): return False
        cfg = {"host": d.host.text().strip(), "port": d.port.value()}; netdb.save_config(app_dir, cfg)
        REMOTE.configure(cfg["host"], cfg["port"]); REMOTE.start_monitor(); return True


def remote_count_users(H):
    n = REMOTE.status(timeout=6).get("users") or 0
    if n == 0:
        QMessageBox.warning(None, "سرور آماده نیست", "روی سرور هنوز مدیری ساخته نشده است.\nابتدا برنامه‌ی «فاکتور یار سرور» را روی کامپیوتر اصلی باز کن و مدیر بساز.")
        sys.exit(0)
    return n


def remote_verify_login(H, username, password):
    """ورود از طریق سرور (رمز هرگز در شبکه ذخیره نمی‌شود و هش‌ها از سرور بیرون نمی‌آیند)."""
    try:
        u = REMOTE.login((username or "").strip(), password or "")
    except netdb.NetError as e:
        QMessageBox.critical(None, "ارتباط با سرور", str(e)); return None
    except Exception as e:
        if "صبر" in str(e): QMessageBox.warning(None, "ورود", str(e))
        return None
    u = dict(u); u["perms"] = json.dumps(u.get("perms") or []); return u


def attach_status(win, app_dir):
    """نشانگر دائمی وضعیت اتصال در نوار پایین + دکمه‌ی تغییر آدرس سرور."""
    lbl = QLabel(""); btn = QPushButton("تنظیم اتصال"); btn.setFlat(True)
    sb = win.statusBar(); sb.addPermanentWidget(lbl); sb.addPermanentWidget(btn)
    def tick():
        if REMOTE.online is False: lbl.setText(f"🔴 ارتباط با سرور قطع است — در حال تلاش مجدد…"); lbl.setStyleSheet("color:#c62828; font-weight:bold;")
        else: lbl.setText(f"🟢 متصل به سرور {REMOTE.host}:{REMOTE.port}" + (f" — کاربر: {REMOTE.user['display'] or REMOTE.user['username']}" if REMOTE.user else "")); lbl.setStyleSheet("color:#2e7d32;")
    def settings():
        d = NetSettingsDialog(netdb.load_config(app_dir), win)
        if d.exec():
            netdb.save_config(app_dir, {"host": d.host.text().strip(), "port": d.port.value()})
            QMessageBox.information(win, "اتصال", "آدرس جدید ذخیره شد؛ برنامه را یک بار ببند و دوباره باز کن.")
    btn.clicked.connect(settings)
    t = QTimer(win); t.timeout.connect(tick); t.start(2000); tick(); win._net_timer = t
