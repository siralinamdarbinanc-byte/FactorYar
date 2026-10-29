# -*- coding: utf-8 -*-
"""فاکتور یار — سرور: صاحب تنهای فایل دیتابیس روی کامپیوتر اصلی فروشگاه. کلاینت‌ها فقط از طریق شبکه‌ی داخلی به آن وصل می‌شوند.
اجرا:  FactorDeskServer.exe            (پنجره‌ی وضعیت)
       FactorDeskServer.exe --headless (بدون پنجره؛ برای سرویس/اجرای خودکار)
       FactorDeskServer.exe --create-admin نام رمز  (ساخت مدیر اولیه بدون پنجره)"""
import os, sys, socket, threading, time, collections
from pathlib import Path
from datetime import datetime
import main as fd
import auth, netdb, secure
from netdb import DbServer

H = fd._auth_H()
LOG = collections.deque(maxlen=300)

def log(m):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {m}"; LOG.append(line)
    try:
        with open(fd.APP_DIR / "server.log", "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass

def server_cfg():
    c = netdb.load_config(fd.APP_DIR).get("server", {})
    return {"host": c.get("host", "0.0.0.0"), "port": int(c.get("port", netdb.DEFAULT_PORT)), "backup_dir": c.get("backup_dir", str(fd.BACKUP_DIR)),
            "keep": int(c.get("keep", 30)), "interval_min": int(c.get("interval_min", 30)), "allowed_cidrs": c.get("allowed_cidrs", [])}

def save_server_cfg(c):
    full = netdb.load_config(fd.APP_DIR); full["server"] = c; netdb.save_config(fd.APP_DIR, full)

def prepare_db():
    """ساخت/مهاجرت جدول‌ها (دیتابیس قبلی حفظ می‌شود) + قابل‌خواندن‌کردن کلیدهای رمزشده‌ی DPAPI برای همه‌ی کلاینت‌ها
    (DPAPI فقط روی یک کامپیوتر باز می‌شود؛ در حالت شبکه کلیدها روی سرور و داخل دیتابیسِ فقط‌سرور نگه‌داری می‌شوند)."""
    fd.init_db()
    for k in fd.SECRET_PREFS:
        with fd.db() as con: r = con.execute("SELECT value FROM settings WHERE key=?", ("pref_" + k,)).fetchone()
        if r and str(r["value"]).startswith(secure.PREFIX):
            plain = secure.unprotect(r["value"])
            if plain:
                with fd.db() as con: con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", ("pref_" + k, plain)); log(f"کلید «{k}» برای استفاده‌ی مشترک آماده شد")
            else: log(f"کلید «{k}» روی این کامپیوتر/کاربر باز نشد؛ بعداً از تنظیمات دوباره وارد کن")
    secure.available = lambda: False          # از این به بعد کلیدهای جدید رمزگذاری DPAPI نمی‌شوند (باید برای همه‌ی کلاینت‌ها خوانا باشند)

def make_server():
    c = server_cfg()
    return DbServer(fd.DB_PATH, lambda u, p: auth.verify_login(H, u, p), lambda: auth.count_users(H), host=c["host"], port=c["port"], backup_dir=c["backup_dir"],
                    keep=c["keep"], interval_min=c["interval_min"], allowed_cidrs=c["allowed_cidrs"], log=log,
                    audit=lambda user, action, detail: auth.audit(H, user, action, detail))

def local_ips():
    ips = set()
    try:
        for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET): ips.add(i[4][0])
    except Exception: pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ips.add(s.getsockname()[0]); s.close()
    except Exception: pass
    return sorted(i for i in ips if not i.startswith("127."))

def autostart_get():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k: winreg.QueryValueEx(k, "FactorDeskServer"); return True
    except Exception: return False

def autostart_set(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
        if on: winreg.SetValueEx(k, "FactorDeskServer", 0, winreg.REG_SZ, f'"{sys.executable}" --headless' if getattr(sys, "frozen", False) else f'"{sys.executable}" "{Path(__file__).resolve()}" --headless')
        else:
            try: winreg.DeleteValue(k, "FactorDeskServer")
            except FileNotFoundError: pass

def run_headless():
    prepare_db(); srv = make_server(); srv.start(); print(f"سرور روی پورت {srv.port} فعال است. آدرس‌ها: {', '.join(local_ips())}  (Ctrl+C = توقف)")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: srv.stop()

def main():
    if "--create-admin" in sys.argv:
        i = sys.argv.index("--create-admin"); prepare_db(); auth.create_user(H, sys.argv[i + 1], "مدیر", sys.argv[i + 2], "admin", []); print("مدیر ساخته شد."); return
    if "--headless" in sys.argv: return run_headless()
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (QApplication, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton, QListWidget, QCheckBox, QFileDialog, QMessageBox,
                                   QInputDialog, QLineEdit, QSpinBox, QWidget)
    from ui_fit import QDialog
    app = QApplication(sys.argv); app.setApplicationName("فاکتور یار — سرور"); app.setLayoutDirection(Qt.RightToLeft); app.setFont(QFont("Tahoma", 10)); app.setStyleSheet(fd.APP_STYLE)
    prepare_db()
    if auth.count_users(H) == 0:
        d = auth.FirstSetupDialog(H)
        if not (d.exec() and d.user): sys.exit(0)
    srv = make_server()
    try: srv.start()
    except OSError as e: QMessageBox.critical(None, "راه‌اندازی سرور", f"پورت {srv.port} در دسترس نیست (برنامه‌ی دیگری از آن استفاده می‌کند؟)\n{e}"); sys.exit(1)

    w = QWidget(); w.setWindowTitle("فاکتور یار — سرور"); w.resize(640, 560); l = QVBoxLayout(w)
    st = QLabel(); st.setStyleSheet("font-size:13pt; font-weight:bold;"); info = QLabel(); info.setTextInteractionFlags(Qt.TextSelectableByMouse); info.setWordWrap(True)
    lst = QListWidget(); l.addWidget(st); l.addWidget(info); l.addWidget(QLabel("گزارش رویدادها:")); l.addWidget(lst, 1)
    def confirm_admin():
        u, ok = QInputDialog.getText(w, "تأیید مدیر", "نام کاربری مدیر:")
        if not ok: return False
        p, ok = QInputDialog.getText(w, "تأیید مدیر", "رمز:", QLineEdit.Password)
        r = auth.verify_login(H, u, p) if ok else None
        if not r or r["role"] != "admin": QMessageBox.warning(w, "تأیید مدیر", "نام کاربری/رمز مدیر درست نیست."); return False
        return True
    def backup():
        try: f = srv.backup_now(); QMessageBox.information(w, "پشتیبان", f"ذخیره شد:\n{f}")
        except Exception as e: QMessageBox.warning(w, "پشتیبان", str(e))
    def restore():
        p, _ = QFileDialog.getOpenFileName(w, "انتخاب فایل پشتیبان", str(srv.backup_dir), "Database (*.db)")
        if not p or not confirm_admin(): return
        if QMessageBox.question(w, "بازیابی", "اطلاعات فعلی با این پشتیبان جایگزین می‌شود (یک نسخه‌ی ایمنی از وضعیت فعلی ساخته می‌شود). ادامه؟") != QMessageBox.Yes: return
        try: s = srv.restore(p); QMessageBox.information(w, "بازیابی", f"انجام شد.\nنسخه‌ی ایمنی وضعیت قبلی: {s.name}")
        except Exception as e: QMessageBox.critical(w, "بازیابی", str(e))
    def settings():
        c = server_cfg(); d = QDialog(w); d.setWindowTitle("تنظیمات سرور"); f = QFormLayout(d)
        port = QSpinBox(); port.setRange(1024, 65535); port.setValue(c["port"]); iv = QSpinBox(); iv.setRange(0, 1440); iv.setValue(c["interval_min"]); iv.setSuffix(" دقیقه (۰=خاموش)")
        kp = QSpinBox(); kp.setRange(3, 999); kp.setValue(c["keep"]); bd = QLineEdit(c["backup_dir"]); bd.setLayoutDirection(Qt.LeftToRight)
        cd = QLineEdit(", ".join(c["allowed_cidrs"])); cd.setPlaceholderText("خالی = هر آدرس شبکه‌ی داخلی؛ یا مثلاً 192.168.1.0/24"); cd.setLayoutDirection(Qt.LeftToRight)
        f.addRow("پورت:", port); f.addRow("پشتیبان خودکار هر:", iv); f.addRow("تعداد نسخه‌ی نگه‌داری:", kp); f.addRow("پوشه‌ی پشتیبان:", bd); f.addRow("آدرس‌های مجاز:", cd)
        ok = QPushButton("ذخیره"); ok.clicked.connect(d.accept); f.addRow(ok)
        if d.exec():
            save_server_cfg({"host": "0.0.0.0", "port": port.value(), "interval_min": iv.value(), "keep": kp.value(), "backup_dir": bd.text().strip(), "allowed_cidrs": [x.strip() for x in cd.text().split(",") if x.strip()]})
            QMessageBox.information(w, "تنظیمات", "ذخیره شد؛ برنامه‌ی سرور را ببند و دوباره باز کن تا اعمال شود.")
    row = QHBoxLayout()
    for t, fn in (("پشتیبان‌گیری همین حالا", backup), ("بازیابی از پشتیبان…", restore), ("تنظیمات", settings)):
        b = QPushButton(t); b.clicked.connect(fn); row.addWidget(b)
    l.addLayout(row)
    cb = QCheckBox("اجرای خودکار سرور بعد از روشن‌شدن ویندوز (بدون پنجره)")
    if sys.platform == "win32": cb.setChecked(autostart_get()); cb.toggled.connect(lambda on: autostart_set(on))
    else: cb.setEnabled(False)
    l.addWidget(cb)
    shown = [0]
    def refresh():
        ok = srv.db_ok(); users = srv.active_users()
        st.setText("🟢 سرور فعال است" if ok else "🔴 دیتابیس مشکل دارد"); st.setStyleSheet("font-size:13pt; font-weight:bold; color:%s;" % ("#2e7d32" if ok else "#c62828"))
        info.setText(f"آدرس برای کلاینت‌ها:  {'  /  '.join(f'{ip}:{srv.port}' for ip in local_ips()) or '—'}\nپایگاه داده: {fd.DB_PATH}  ({'سالم' if ok else 'خطا'})\n"
                     f"کاربران متصل: {', '.join(users) or '—'}\nآخرین پشتیبان: {srv.last_backup_msg or '—'}   |   پوشه‌ی پشتیبان: {srv.backup_dir}")
        while shown[0] < len(LOG): lst.addItem(LOG[shown[0]]); shown[0] += 1
        lst.scrollToBottom()
    t = QTimer(w); t.timeout.connect(refresh); t.start(2000); refresh()
    def closing(ev):
        if QMessageBox.question(w, "خروج", "با بستن این پنجره سرور متوقف می‌شود و کلاینت‌ها قطع می‌شوند. خارج شوم؟") == QMessageBox.Yes: srv.stop(); ev.accept()
        else: ev.ignore()
    w.closeEvent = closing; w.show(); sys.exit(app.exec())

if __name__ == "__main__":
    main()
