# -*- coding: utf-8 -*-
"""تب «چاپ بارکد»: نام کالا را می‌نویسی/انتخاب می‌کنی، نوع بارکد را برمی‌گزینی، تعداد برچسب را می‌دهی و چاپ می‌کنی.
کالای بدون بارکد خودکار بارکد داخلیِ یکتا می‌گیرد و همان لحظه به کالا وصل می‌شود تا اسکنِ بعدی در فاکتور/پیش‌فاکتور همان کالا را بیاورد."""
import math
from PySide6.QtCore import Qt, QSizeF, QRectF, QMarginsF
from PySide6.QtGui import QPainter, QFont, QFontMetrics, QColor, QPageSize, QPageLayout
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QCheckBox, QGroupBox, QAbstractItemView)
import barcode_codec as bc
import barcodes

SIZES = {"۵۰×۳۰ میلی‌متر": (50, 30), "۴۰×۲۵ میلی‌متر": (40, 25), "۶۰×۴۰ میلی‌متر": (60, 40), "۳۰×۲۰ میلی‌متر": (30, 20), "سفارشی": None}
MODES = ["رول/پرینتر برچسب (هر برچسب یک صفحه)", "برگه‌ی A4 (چند برچسب در هر برگه)"]


def draw_label(p, r, name, code, kind, price, dpmm, show_text=True):
    """یک برچسب را داخل مستطیل r (پیکسل دستگاه) می‌کشد: نام کالا، میله‌ها، شماره‌ی زیر میله‌ها و (اختیاری) قیمت."""
    pad = 1.4 * dpmm; x0, y0, w, h = r.x() + pad, r.y() + pad, r.width() - 2 * pad, r.height() - 2 * pad
    p.setPen(QColor("black")); black = QColor("black")
    f = QFont("Tahoma"); f.setPixelSize(max(8, int(min(3.4 * dpmm, h * 0.17)))); f.setBold(True); p.setFont(f)
    th = QFontMetrics(f).height()
    p.drawText(QRectF(x0, y0, w, th), Qt.AlignHCenter | Qt.AlignVCenter, QFontMetrics(f).elidedText(name, Qt.ElideRight, int(w)))
    bits = bc.encode(kind, code); quiet = 10
    fs = QFont("Tahoma"); fs.setPixelSize(max(7, int(min(2.8 * dpmm, h * 0.14)))); sh = QFontMetrics(fs).height()
    ph = sh if price else 0
    bar_top = y0 + th + 0.5 * dpmm; bar_h = max(4.0, h - th - sh - ph - 1.5 * dpmm)
    mw = max(1.0, math.floor(w / (len(bits) + 2 * quiet)))
    if mw * (len(bits) + 2 * quiet) > w: mw = w / (len(bits) + 2 * quiet)
    x = x0 + (w - mw * len(bits)) / 2; i = 0
    while i < len(bits):
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1": j += 1
            p.fillRect(QRectF(x + i * mw, bar_top, (j - i) * mw, bar_h), black); i = j
        else: i += 1
    p.setFont(fs)
    if show_text: p.drawText(QRectF(x0, bar_top + bar_h + 0.3 * dpmm, w, sh), Qt.AlignHCenter | Qt.AlignVCenter, code)
    if price:
        fp = QFont("Tahoma"); fp.setPixelSize(fs.pixelSize()); fp.setBold(True); p.setFont(fp)
        p.drawText(QRectF(x0, bar_top + bar_h + 0.3 * dpmm + sh, w, ph), Qt.AlignHCenter | Qt.AlignVCenter, str(price))


class BarcodePanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent); self.db = db; self.queue = []
        root = QVBoxLayout(self)
        g = QGroupBox("کالا و بارکد"); f = QFormLayout(g)
        self.name = QComboBox(); self.name.setEditable(True); self.name.setInsertPolicy(QComboBox.NoInsert); self.name.lineEdit().setPlaceholderText("نام کالا را بنویس یا از فهرست انتخاب کن")
        self.name.currentTextChanged.connect(self._name_changed)
        self.kind = QComboBox(); self.kind.addItems(list(bc.TYPES))
        self.code = QLineEdit(); self.code.setLayoutDirection(Qt.LeftToRight); self.code.setPlaceholderText("خالی بگذار تا بارکد داخلی ساخته شود")
        self.hint = QLabel(""); self.hint.setStyleSheet("color:#666;")
        self.count = QSpinBox(); self.count.setRange(1, 5000); self.count.setValue(1)
        self.price = QLineEdit(); self.price.setPlaceholderText("اختیاری؛ مثلاً 150,000"); self.price.setLayoutDirection(Qt.LeftToRight)
        f.addRow("نام کالا:", self.name); f.addRow("نوع بارکد:", self.kind); f.addRow("شماره‌ی بارکد:", self.code); f.addRow("", self.hint)
        f.addRow("تعداد برچسب:", self.count); f.addRow("قیمت روی برچسب:", self.price)
        add = QPushButton("＋ افزودن به صف چاپ"); add.clicked.connect(self.add_to_queue); f.addRow(add)
        root.addWidget(g)
        s = QGroupBox("اندازه و چیدمان برچسب"); sf = QFormLayout(s)
        self.size = QComboBox(); self.size.addItems(list(SIZES)); self.size.currentTextChanged.connect(self._size_changed)
        self.w = QDoubleSpinBox(); self.w.setRange(15, 200); self.w.setValue(50); self.w.setSuffix(" mm"); self.h = QDoubleSpinBox(); self.h.setRange(10, 200); self.h.setValue(30); self.h.setSuffix(" mm")
        self.mode = QComboBox(); self.mode.addItems(MODES); self.show_text = QCheckBox("شماره‌ی بارکد زیر میله‌ها چاپ شود"); self.show_text.setChecked(True)
        sf.addRow("اندازه:", self.size); sf.addRow("عرض:", self.w); sf.addRow("ارتفاع:", self.h); sf.addRow("نوع چاپ:", self.mode); sf.addRow("", self.show_text)
        root.addWidget(s)
        self.table = QTableWidget(0, 5); self.table.setHorizontalHeaderLabels(["نام کالا", "بارکد", "نوع", "تعداد", "قیمت"]); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); root.addWidget(self.table, 1)
        row = QHBoxLayout(); b1 = QPushButton("حذف ردیف انتخاب‌شده"); b1.clicked.connect(self.remove_selected); b2 = QPushButton("خالی‌کردن صف"); b2.clicked.connect(self.clear_queue)
        self.total = QLabel(""); b3 = QPushButton("🖨 پیش‌نمایش و چاپ"); b3.setDefault(True); b3.clicked.connect(self.print_labels)
        row.addWidget(b1); row.addWidget(b2); row.addStretch(); row.addWidget(self.total); row.addWidget(b3); root.addLayout(row)
        self.reload_names(); self._size_changed(self.size.currentText())

    # —— کمکی ——
    def reload_names(self):
        try:
            with self.db() as con: names = barcodes.known_names(con)
        except Exception: names = []
        cur = self.name.currentText(); self.name.blockSignals(True); self.name.clear(); self.name.addItems(names); self.name.setEditText(cur); self.name.blockSignals(False)

    def showEvent(self, e): self.reload_names(); super().showEvent(e)

    def _size_changed(self, t):
        sz = SIZES.get(t); custom = sz is None
        self.w.setEnabled(custom); self.h.setEnabled(custom)
        if sz: self.w.setValue(sz[0]); self.h.setValue(sz[1])

    def _name_changed(self, t):
        t = (t or "").strip()
        if not t: self.hint.setText(""); return
        try:
            with self.db() as con: code = barcodes.barcode_of(con, t)
        except Exception: return
        if code:
            self.code.setText(code); self.hint.setText("این کالا بارکد دارد؛ همان چاپ می‌شود.")
            self.kind.setCurrentText("EAN-13" if bc.ean13_valid(code) else ("Code 128" if bc.compatible("Code 128", code) else "Code 39"))
        else: self.code.setText(""); self.hint.setText("این کالا بارکد ندارد؛ با «افزودن به صف» بارکد داخلیِ جدید ساخته و به کالا وصل می‌شود.")

    def _msg(self, t, m): QMessageBox.warning(self, t, m)

    # —— صف ——
    def add_to_queue(self):
        name = self.name.currentText().strip(); kind = self.kind.currentText(); code = barcodes.norm(self.code.text()) if self.code.text().strip() else ""
        if not name: return self._msg("نام کالا", "نام کالا را بنویس یا انتخاب کن.")
        if code and not bc.compatible(kind, code):
            if kind == "EAN-13" and bc.compatible("Code 128", code):
                if QMessageBox.question(self, "نوع بارکد", f"شماره‌ی «{code}» یک EAN-13 معتبر نیست (۱۳ رقم با رقم کنترل).\nبا نوع Code 128 چاپ شود؟") != QMessageBox.Yes: return
                kind = "Code 128"; self.kind.setCurrentText(kind)
            else: return self._msg("بارکد نامعتبر", f"این شماره برای نوع «{kind}» قابل چاپ نیست.")
        try:
            with self.db() as con:
                if not code: code = barcodes.new_unique_code(con, kind)
                other = barcodes.lookup(con, code)
                if other and other["product_name"] != name: raise ValueError(f"این بارکد قبلاً برای «{other['product_name']}» ثبت شده است.")
                barcodes.define(con, code, name, other["description"] if other else "")      # از این به بعد اسکن همین بارکد، این کالا را در فاکتور می‌آورد
        except ValueError as e: return self._msg("بارکد تکراری", str(e))
        except Exception as e: return self._msg("خطا", f"ثبت بارکد انجام نشد:\n{e}")
        self.code.setText(code); self.queue.append((name, code, kind, self.count.value(), self.price.text().strip())); self._refresh(); self.reload_names()

    def _refresh(self):
        self.table.setRowCount(len(self.queue))
        for i, q in enumerate(self.queue):
            for c, v in enumerate(q): self.table.setItem(i, c, QTableWidgetItem(str(v)))
        self.total.setText(f"جمع برچسب‌ها: {sum(q[3] for q in self.queue)}")

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows: del self.queue[r]
        self._refresh()

    def clear_queue(self): self.queue = []; self._refresh()

    # —— چاپ ——
    def print_labels(self):
        if not self.queue and self.name.currentText().strip(): self.add_to_queue()
        if not self.queue: return self._msg("صف خالی است", "اول کالا را به صف چاپ اضافه کن.")
        w, h = float(self.w.value()), float(self.h.value()); pr = QPrinter(QPrinter.HighResolution); a4 = self.mode.currentIndex() == 1
        if a4: pr.setPageSize(QPageSize(QPageSize.A4)); pr.setPageMargins(QMarginsF(8, 8, 8, 8), QPageLayout.Millimeter)
        else: pr.setPageSize(QPageSize(QSizeF(w, h), QPageSize.Millimeter)); pr.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
        pr.setPageOrientation(QPageLayout.Portrait)
        dlg = QPrintPreviewDialog(pr, self); dlg.setWindowTitle("پیش‌نمایش و چاپ بارکد"); dlg.resize(900, 700)
        dlg.paintRequested.connect(lambda printer: self._paint(printer, w, h, a4)); dlg.exec()

    def _paint(self, printer, w, h, a4):
        labels = [q for q in self.queue for _ in range(q[3])]; res = printer.resolution(); dpmm = res / 25.4
        p = QPainter(printer); page = printer.pageRect(QPrinter.DevicePixel)
        try:
            if a4:
                cols, rows = barcodes.grid_layout(w, h); per = cols * rows
                for i, (name, code, kind, _n, price) in enumerate(labels):
                    k = i % per
                    if i and k == 0: printer.newPage()
                    r = QRectF(page.x() + (k % cols) * w * dpmm, page.y() + (k // cols) * h * dpmm, w * dpmm, h * dpmm)
                    draw_label(p, r, name, code, kind, price, dpmm, self.show_text.isChecked())
            else:
                for i, (name, code, kind, _n, price) in enumerate(labels):
                    if i: printer.newPage()
                    draw_label(p, QRectF(page.x(), page.y(), page.width(), page.height()), name, code, kind, price, dpmm, self.show_text.isChecked())
        finally: p.end()
