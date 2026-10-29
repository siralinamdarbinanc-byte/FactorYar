# -*- coding: utf-8 -*-
"""نگهداری دیتابیس: پشتیبان ابری (پوشه‌ی همگام‌شده)، بازیابی امن و بررسی/تعمیر سلامت دیتابیس.

«پشتیبان ابری» یعنی کپی هر پشتیبان داخل یک پوشه‌ی دلخواه؛ اگر آن پوشه را Google Drive، OneDrive، Dropbox،
Yandex Disk یا هر برنامه‌ی همگام‌سازی دیگری به اینترنت وصل کند، پشتیبان‌ها خودکار بیرون از کامپیوتر هم می‌روند.
(نیازی به ثبت‌نام، کلید یا API نیست.)
"""
import os, shutil, sqlite3, subprocess, sys
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QMessageBox, QFileDialog, QSpinBox, QFormLayout,
)
from ui_fit import QDialog

REQUIRED_TABLES = ("customers", "invoices", "invoice_items", "payments", "cheques", "settings")


# ───────────────────────────── ایندکس‌ها (سرعت با زیادشدن دیتا) ─────────────────────────────
def ensure_indexes(con):
    con.executescript("""
    CREATE INDEX IF NOT EXISTS idx_pay_date ON payments(payment_date);
    CREATE INDEX IF NOT EXISTS idx_chq_due ON cheques(due_date);
    CREATE INDEX IF NOT EXISTS idx_chq_invoice ON cheques(invoice_id);
    CREATE INDEX IF NOT EXISTS idx_inv_kind_date ON invoices(is_proforma, invoice_date);
    CREATE INDEX IF NOT EXISTS idx_items_name ON invoice_items(product_name);
    """)


# ───────────────────────────── پشتیبان ابری ─────────────────────────────
def copy_to_cloud(src, cloud_dir, keep):
    """src را داخل cloud_dir کپی می‌کند و فقط `keep` نسخه‌ی تازه را نگه می‌دارد. مسیر مقصد را برمی‌گرداند (یا None اگر پوشه تنظیم نشده)."""
    cloud_dir = (cloud_dir or "").strip()
    if not cloud_dir: return None
    d = Path(cloud_dir); d.mkdir(parents=True, exist_ok=True)
    dest = d / Path(src).name
    tmp = d / (dest.name + ".part")
    shutil.copy2(src, tmp); os.replace(tmp, dest)          # اول فایل موقت، بعد جایگزینی؛ همگام‌ساز نیمه‌کاره نمی‌بیند
    for old in sorted(d.glob("factordesk-*.db"))[:-max(3, int(keep))]:
        try: old.unlink()
        except Exception: pass
    return dest


def list_backups(local_dir, cloud_dir=""):
    out = []
    for where, d in (("محلی", local_dir), ("ابری", cloud_dir)):
        if not d: continue
        d = Path(d)
        if not d.is_dir(): continue
        for p in list(d.glob("factordesk-*.db")) + list(d.glob("before-restore-*.db")):
            try: st = p.stat()
            except OSError: continue
            out.append(dict(path=str(p), name=p.name, where=where, mtime=st.st_mtime, size=st.st_size))
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def validate_backup(path):
    """(ok, پیام, آمار) — فایل را فقط‌خواندنی باز می‌کند و سلامت و وجود جدول‌های اصلی را می‌سنجد."""
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except Exception as e:
        return False, f"فایل باز نشد: {e}", {}
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        miss = [t for t in REQUIRED_TABLES if t not in tables]
        if miss: return False, "این فایل پشتیبان فاکتور یار نیست (جدول‌های لازم ندارد: " + "، ".join(miss) + ")", {}
        chk = con.execute("PRAGMA quick_check").fetchone()[0]
        if chk != "ok": return False, f"فایل آسیب دیده است: {chk}", {}
        st = dict(customers=con.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
                  invoices=con.execute("SELECT COUNT(*) FROM invoices WHERE COALESCE(is_proforma,0)=0").fetchone()[0]
                  if "is_proforma" in {r[1] for r in con.execute("PRAGMA table_info(invoices)")} else con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0])
        return True, "سالم", st
    except Exception as e:
        return False, str(e), {}
    finally:
        con.close()


def restore_backup(path, db_path, backup_dir, sqlite_copy):
    """اول یک نسخه از وضعیت فعلی (before-restore-...) می‌گیرد، بعد با API پشتیبان SQLite محتوا را جایگزین می‌کند."""
    ok, msg, _ = validate_backup(path)
    if not ok: raise ValueError(msg)
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    safety = Path(backup_dir) / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db"
    if Path(db_path).exists(): sqlite_copy(db_path, safety)
    sqlite_copy(path, db_path)
    return safety


# ───────────────────────────── بررسی سلامت دیتابیس ─────────────────────────────
def run_checks(con, invoice_rows=None):
    """فهرست یافته‌ها: dict(level: ok|warn|error, title, count, sample, fix: SQL یا None)."""
    res = []

    def add(level, title, count=0, sample="", fix=None):
        res.append(dict(level=level, title=title, count=count, sample=sample, fix=fix))

    ic = con.execute("PRAGMA integrity_check").fetchall()
    if len(ic) == 1 and ic[0][0] == "ok": add("ok", "سلامت فایل دیتابیس (integrity_check)")
    else: add("error", "فایل دیتابیس آسیب دیده است — از «بازیابی پشتیبان» استفاده کن", len(ic), "؛ ".join(str(r[0]) for r in ic[:3]))

    def q(title, sql, fix=None, level="error", sample_sql=None, ok_title=None):
        n = con.execute(sql).fetchone()[0]
        if n == 0: add("ok", ok_title or title.split("(")[0].strip() + " — مشکلی نیست"); return
        sample = ""
        if sample_sql: sample = "، ".join(str(r[0]) for r in con.execute(sample_sql + " LIMIT 5").fetchall())
        add(level if fix is None else "warn", title, n, sample, fix)

    q("اقلام فاکتور بدون فاکتور (یتیم)", "SELECT COUNT(*) FROM invoice_items WHERE invoice_id NOT IN (SELECT id FROM invoices)",
      "DELETE FROM invoice_items WHERE invoice_id NOT IN (SELECT id FROM invoices)")
    q("فاکتور بدون مشتری", "SELECT COUNT(*) FROM invoices WHERE customer_id NOT IN (SELECT id FROM customers)",
      None, "error", "SELECT invoice_no FROM invoices WHERE customer_id NOT IN (SELECT id FROM customers)")
    q("دریافتی بدون مشتری", "SELECT COUNT(*) FROM payments WHERE customer_id NOT IN (SELECT id FROM customers)",
      None, "error", "SELECT id FROM payments WHERE customer_id NOT IN (SELECT id FROM customers)")
    q("دریافتی که فاکتورش پاک شده (جدا می‌شود و به‌صورت «بدون فاکتور» می‌ماند)",
      "SELECT COUNT(*) FROM payments WHERE invoice_id IS NOT NULL AND invoice_id NOT IN (SELECT id FROM invoices)",
      "UPDATE payments SET invoice_id=NULL WHERE invoice_id IS NOT NULL AND invoice_id NOT IN (SELECT id FROM invoices)",
      sample_sql="SELECT id FROM payments WHERE invoice_id IS NOT NULL AND invoice_id NOT IN (SELECT id FROM invoices)")
    q("دریافتی که چکش پاک شده", "SELECT COUNT(*) FROM payments WHERE cheque_id IS NOT NULL AND cheque_id NOT IN (SELECT id FROM cheques)",
      "UPDATE payments SET cheque_id=NULL WHERE cheque_id IS NOT NULL AND cheque_id NOT IN (SELECT id FROM cheques)")
    q("چک بدون مشتری", "SELECT COUNT(*) FROM cheques WHERE customer_id NOT IN (SELECT id FROM customers)",
      None, "error", "SELECT cheque_no FROM cheques WHERE customer_id NOT IN (SELECT id FROM customers)")
    q("چک که فاکتورش پاک شده", "SELECT COUNT(*) FROM cheques WHERE invoice_id IS NOT NULL AND invoice_id NOT IN (SELECT id FROM invoices)",
      "UPDATE cheques SET invoice_id=NULL WHERE invoice_id IS NOT NULL AND invoice_id NOT IN (SELECT id FROM invoices)")
    q("قلم فاکتور با تعداد یا قیمت منفی", "SELECT COUNT(*) FROM invoice_items WHERE quantity<0 OR unit_price<0", None, "warn",
      "SELECT i.invoice_no FROM invoice_items it JOIN invoices i ON i.id=it.invoice_id WHERE it.quantity<0 OR it.unit_price<0")
    q("فاکتور فروش بدون هیچ قلم", "SELECT COUNT(*) FROM invoices WHERE COALESCE(is_proforma,0)=0 AND id NOT IN (SELECT invoice_id FROM invoice_items)",
      None, "warn", "SELECT invoice_no FROM invoices WHERE COALESCE(is_proforma,0)=0 AND id NOT IN (SELECT invoice_id FROM invoice_items)")
    if invoice_rows is not None:
        over = [r for r in invoice_rows(con) if r["remain"] < -0.5]
        if over: add("warn", "فاکتور با دریافتی بیشتر از مبلغ فاکتور", len(over), "، ".join(str(r["invoice_no"]) for r in over[:5]))
        else: add("ok", "دریافتی بیشتر از مبلغ فاکتور — مشکلی نیست")
    dup = con.execute("SELECT name, COUNT(*) c FROM customers GROUP BY lower(trim(name)) HAVING c>1").fetchall()
    if dup: add("warn", "مشتری با نام تکراری (بهتر است ادغام یا اصلاح شود)", len(dup), "، ".join(str(r["name"]) for r in dup[:5]))
    else: add("ok", "نام مشتری تکراری — مشکلی نیست")
    return res


def apply_fixes(con, findings):
    """فقط موارد قابل‌تعمیر (fix) را اجرا می‌کند؛ تعداد ردیف‌های اصلاح‌شده را برمی‌گرداند."""
    n = 0
    for f in findings:
        if f["fix"] and f["count"]:
            n += con.execute(f["fix"]).rowcount
    return n


# ───────────────────────────── پنجره‌ها ─────────────────────────────
def _tbl(heads):
    t = QTableWidget(0, len(heads)); t.setHorizontalHeaderLabels(heads); t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows); t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.verticalHeader().setVisible(False); t.horizontalHeader().setStretchLastSection(True)
    return t


def _it(text, brush=None):
    it = QTableWidgetItem(str(text)); it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if brush is not None: it.setForeground(brush)
    return it


class BackupDialog(QDialog):
    """H: db_path, backup_dir, sqlite_copy, get_pref, set_pref, auto_backup, iso_to_j, info."""
    def __init__(self, H, parent=None):
        super().__init__(parent); self.H = H; self.restored = False
        self.setWindowTitle("پشتیبان ابری و بازیابی"); self.resize(900, 640)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("پوشه‌ی پشتیبان ابری را روی پوشه‌ای بگذار که Google Drive / OneDrive / Dropbox / Yandex Disk همگامش می‌کند؛ "
                              "از آن به بعد هر پشتیبان خودکار بیرون از این کامپیوتر هم نگه داشته می‌شود."))
        f = QFormLayout()
        row = QHBoxLayout(); self.cloud = QLineEdit(H.get_pref("cloud_dir", "")); self.cloud.setPlaceholderText("مثلاً D:\\Google Drive\\FactorDesk-backup")
        self.cloud.setLayoutDirection(Qt.LeftToRight); self.cloud.editingFinished.connect(self._save_cloud)
        bc = QPushButton("انتخاب پوشه..."); bc.clicked.connect(self._pick_cloud); row.addWidget(self.cloud, 1); row.addWidget(bc)
        f.addRow("پوشه‌ی پشتیبان ابری:", row)
        self.every = QSpinBox(); self.every.setRange(0, 1440); self.every.setSuffix(" دقیقه"); self.every.setSpecialValueText("خاموش")
        try: self.every.setValue(int(float(H.get_pref("backup_every_min", "30") or 30)))
        except Exception: self.every.setValue(30)
        self.every.valueChanged.connect(lambda v: H.set_pref("backup_every_min", v))
        f.addRow("پشتیبان خودکار حین کار، هر:", self.every); root.addLayout(f)
        self.status = QLabel(""); self.status.setWordWrap(True); root.addWidget(self.status)
        row2 = QHBoxLayout()
        for t, fn in (("پشتیبان‌گیری همین الان", self._backup_now), ("باز کردن پوشه‌ی محلی", self._open_local), ("تازه‌سازی فهرست", self._load)):
            b = QPushButton(t); b.clicked.connect(fn); row2.addWidget(b)
        row2.addStretch(); root.addLayout(row2)
        self.t = _tbl(["نسخه", "محل", "زمان", "حجم", "فاکتور", "مشتری"]); self.t.itemSelectionChanged.connect(self._sel_info); root.addWidget(self.t, 1)
        self.sel = QLabel(""); root.addWidget(self.sel)
        row3 = QHBoxLayout()
        b1 = QPushButton("بازیابی نسخه‌ی انتخاب‌شده"); b1.clicked.connect(self._restore_sel)
        b2 = QPushButton("بازیابی از فایل دیگر..."); b2.clicked.connect(self._restore_file)
        row3.addWidget(b1); row3.addWidget(b2); row3.addStretch(); root.addLayout(row3)
        self.rows = []; self._load()

    def _save_cloud(self):
        self.H.set_pref("cloud_dir", self.cloud.text().strip()); self._status()

    def _pick_cloud(self):
        d = QFileDialog.getExistingDirectory(self, "پوشه‌ی پشتیبان ابری", self.cloud.text() or "")
        if d: self.cloud.setText(d); self._save_cloud(); self._load()

    def _status(self):
        s = self.H.get_pref("cloud_status", "")
        self.status.setText(("وضعیت آخرین ارسال به پوشه‌ی ابری: " + s) if s else ("پوشه‌ی ابری تنظیم نشده؛ فقط پشتیبان محلی گرفته می‌شود." if not self.cloud.text().strip() else ""))

    def _open_local(self):
        Path(self.H.backup_dir).mkdir(parents=True, exist_ok=True); p = str(self.H.backup_dir)
        try:
            if sys.platform == "win32": os.startfile(p)
            else: subprocess.Popen(["xdg-open", p])
        except Exception: pass

    def _backup_now(self):
        try:
            p = self.H.auto_backup(True)
            QMessageBox.information(self, "پشتیبان", "پشتیبان گرفته شد:\n" + str(p) if p else "پشتیبانی ساخته نشد.")
        except Exception as e:
            QMessageBox.warning(self, "پشتیبان", str(e))
        self._load()

    def _load(self):
        self._status()
        self.rows = list_backups(self.H.backup_dir, self.cloud.text().strip())
        self.t.setRowCount(0)
        for b in self.rows:
            r = self.t.rowCount(); self.t.insertRow(r)
            dt = datetime.fromtimestamp(b["mtime"]); when = f"{self.H.iso_to_j(dt.date().isoformat())}  {dt:%H:%M}"
            for c, v in enumerate([b["name"], b["where"], when, f"{b['size'] / 1024:,.0f} KB", "", ""]): self.t.setItem(r, c, _it(v))
        self.t.resizeColumnsToContents(); self.t.horizontalHeader().setStretchLastSection(True)

    def _sel_info(self):
        r = self.t.currentRow()
        if r < 0 or r >= len(self.rows): return
        ok, msg, st = validate_backup(self.rows[r]["path"])
        if ok:
            self.t.setItem(r, 4, _it(st.get("invoices", ""))); self.t.setItem(r, 5, _it(st.get("customers", "")))
            self.sel.setText("✔ این نسخه سالم است.")
        else: self.sel.setText("✘ " + msg)

    def _restore_sel(self):
        r = self.t.currentRow()
        if r < 0 or r >= len(self.rows): return QMessageBox.information(self, "بازیابی", "ابتدا یک نسخه را انتخاب کن.")
        self._do_restore(self.rows[r]["path"])

    def _restore_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "انتخاب فایل پشتیبان", "", "Database (*.db)")
        if p: self._do_restore(p)

    def _do_restore(self, path):
        ok, msg, st = validate_backup(path)
        if not ok: return QMessageBox.warning(self, "بازیابی", msg)
        if QMessageBox.question(self, "تأیید بازیابی",
                                f"اطلاعات فعلی با این نسخه جایگزین می‌شود (فاکتور: {st.get('invoices')}، مشتری: {st.get('customers')}).\n"
                                "قبل از آن از وضعیت فعلی یک نسخه‌ی امن (before-restore) گرفته می‌شود.\nبعد از بازیابی برنامه بسته می‌شود تا دوباره باز کنی. ادامه؟") != QMessageBox.Yes: return
        try:
            safety = restore_backup(path, self.H.db_path, self.H.backup_dir, self.H.sqlite_copy)
        except Exception as e:
            return QMessageBox.warning(self, "بازیابی", str(e))
        self.H.log("بازیابی پشتیبان", Path(path).name)
        QMessageBox.information(self, "بازیابی", f"بازیابی انجام شد. نسخه‌ی قبلی اینجا ذخیره شد:\n{safety}\n\nبرنامه حالا بسته می‌شود؛ دوباره بازش کن.")
        self.restored = True; self.accept()


class CheckDialog(QDialog):
    """H: db, invoice_rows, db_path, backup_dir, sqlite_copy, log."""
    def __init__(self, H, parent=None):
        super().__init__(parent); self.H = H; self.findings = []
        self.setWindowTitle("بررسی و تعمیر دیتابیس"); self.resize(900, 560)
        from PySide6.QtGui import QBrush, QColor
        self.col = {"ok": QBrush(QColor("#2e7d32")), "warn": QBrush(QColor("#b26a00")), "error": QBrush(QColor("#c62828"))}
        root = QVBoxLayout(self)
        root.addWidget(QLabel("این ابزار سلامت فایل دیتابیس و ارتباط فاکتور، مشتری، دریافت و چک را می‌سنجد. تعمیر خودکار فقط روی موارد بی‌خطر انجام می‌شود و قبلش یک پشتیبان گرفته می‌شود."))
        self.t = _tbl(["وضعیت", "مورد", "تعداد", "نمونه"]); root.addWidget(self.t, 1)
        self.sum = QLabel(""); root.addWidget(self.sum)
        row = QHBoxLayout()
        for txt, fn in (("بررسی دوباره", self.check), ("رفع خودکار موارد قابل‌تعمیر", self.fix), ("بهینه‌سازی و فشرده‌سازی دیتابیس", self.optimize)):
            b = QPushButton(txt); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(); root.addLayout(row); self.check()

    def check(self):
        with self.H.db() as con: self.findings = run_checks(con, self.H.invoice_rows)
        self.t.setRowCount(0); lab = {"ok": "✔ سالم", "warn": "⚠ هشدار", "error": "✘ خطا"}
        for f in self.findings:
            r = self.t.rowCount(); self.t.insertRow(r)
            self.t.setItem(r, 0, _it(lab[f["level"]], self.col[f["level"]])); self.t.setItem(r, 1, _it(f["title"] + (" — قابل‌تعمیر خودکار" if f["fix"] and f["count"] else "")))
            self.t.setItem(r, 2, _it(f["count"] or "")); self.t.setItem(r, 3, _it(f["sample"]))
        self.t.resizeColumnsToContents(); self.t.horizontalHeader().setStretchLastSection(True)
        bad = sum(1 for f in self.findings if f["level"] != "ok")
        self.sum.setText("همه‌چیز سالم است." if not bad else f"{bad} مورد نیاز به توجه دارد.")

    def fix(self):
        todo = [f for f in self.findings if f["fix"] and f["count"]]
        if not todo: return QMessageBox.information(self, "تعمیر", "موردی برای تعمیر خودکار نیست.")
        if QMessageBox.question(self, "تعمیر", f"{len(todo)} نوع مشکل قابل‌تعمیر هست. اول پشتیبان گرفته می‌شود، بعد اصلاح انجام می‌شود. ادامه؟") != QMessageBox.Yes: return
        try:
            safety = Path(self.H.backup_dir) / f"before-restore-repair-{datetime.now():%Y%m%d-%H%M%S}.db"
            Path(self.H.backup_dir).mkdir(parents=True, exist_ok=True); self.H.sqlite_copy(self.H.db_path, safety)
            with self.H.db() as con: n = apply_fixes(con, todo)
            self.H.log("تعمیر دیتابیس", f"{n} ردیف اصلاح شد")
            QMessageBox.information(self, "تعمیر", f"{n} ردیف اصلاح شد.\nنسخه‌ی قبل از تعمیر: {safety}")
        except Exception as e:
            QMessageBox.warning(self, "تعمیر", str(e))
        self.check()

    def optimize(self):
        try:
            import sqlite3 as _s
            con = _s.connect(str(self.H.db_path)); con.isolation_level = None
            try:
                con.execute("REINDEX"); con.execute("ANALYZE"); con.execute("VACUUM")
            finally:
                con.close()
            QMessageBox.information(self, "بهینه‌سازی", "دیتابیس بهینه و فشرده شد.")
        except Exception as e:
            QMessageBox.warning(self, "بهینه‌سازی", str(e))
