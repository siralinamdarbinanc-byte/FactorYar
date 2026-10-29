# -*- coding: utf-8 -*-
"""رابط بخش خرید: فاکتور خرید / برگشت از خرید، تأمین‌کنندگان، پرداخت به تأمین‌کننده، صورت‌حساب، چک‌های پرداختی.
وابستگی‌ها از main با bind() داده می‌شود (جلوگیری از import دایره‌ای)؛ منطق حسابداری در purchases.py است."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLabel, QLineEdit, QComboBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QAbstractItemView, QDialogButtonBox, QCheckBox, QCompleter, QStyledItemDelegate, QInputDialog, QApplication)
from PySide6.QtGui import QPageSize, QPageLayout
from PySide6.QtCore import QMarginsF
from ui_fit import QDialog
import purchases as P
import barcodes

X = None          # وابستگی‌های main (SimpleNamespace)


def bind(ctx):
    global X; X = ctx


def _names():
    with X.db() as con:
        rows = con.execute("""SELECT product_name FROM (SELECT product_name FROM purchase_items UNION SELECT product_name FROM invoice_items
                              UNION SELECT product_name FROM product_barcodes) WHERE product_name<>'' GROUP BY product_name ORDER BY product_name LIMIT 4000""")
        return [r[0] for r in rows]


def _suppliers():
    with X.db() as con: return con.execute("SELECT id,name FROM suppliers ORDER BY name").fetchall()


def _label(text, bold=False):
    l = QLabel(text)
    if bold: l.setStyleSheet("font-weight:bold;")
    return l


def _balance_text(b):
    return f"{X.money(abs(b))} تومان — " + ("بدهکار به تأمین‌کننده" if b > 0.5 else "بستانکار از تأمین‌کننده" if b < -0.5 else "تسویه")


def print_html(parent, title, html):
    doc = QTextDocument(); doc.setHtml(f'<body dir="rtl" style="font-family:Tahoma; font-size:10pt;">{html}</body>')
    pr = QPrinter(QPrinter.HighResolution); pr.setPageSize(QPageSize(QPageSize.A4)); pr.setPageOrientation(QPageLayout.Portrait)
    pr.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Millimeter); X.show_preview(parent, doc, pr, title)


def _table_html(headers, rows):
    th = "".join(f'<th style="border:1px solid #888; background:#eee; padding:3px;">{h}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f'<td style="border:1px solid #888; padding:3px;">{c}</td>' for c in r) + "</tr>" for r in rows)
    return f'<table width="100%" cellspacing="0" cellpadding="2" style="border-collapse:collapse;"><tr>{th}</tr>{body}</table>'


class _NameDelegate(QStyledItemDelegate):
    """پیشنهاد نام کالا هنگام تایپ در جدول اقلام."""
    def __init__(self, names, parent=None): super().__init__(parent); self.names = names
    def createEditor(self, parent, option, index):
        e = QLineEdit(parent); c = QCompleter(self.names, e); c.setCaseSensitivity(Qt.CaseInsensitive); c.setFilterMode(Qt.MatchContains); e.setCompleter(c); return e


# ───────────────────────── تأمین‌کننده ─────────────────────────
class SupplierDialog(QDialog):
    def __init__(self, parent=None, sid=None):
        super().__init__(parent); self.sid = sid; self.saved_id = None; self.setWindowTitle("ویرایش تأمین‌کننده" if sid else "تأمین‌کننده‌ی جدید"); self.setMinimumWidth(460)
        row = None
        if sid:
            with X.db() as con: row = con.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
        f = QFormLayout(self)
        self.name = QLineEdit(row["name"] if row else ""); self.phone = QLineEdit(row["phone"] if row else ""); self.address = QLineEdit(row["address"] if row else "")
        self.notes = QLineEdit(row["notes"] if row else "")
        f.addRow("نام تأمین‌کننده / شرکت", self.name); f.addRow("تلفن", self.phone); f.addRow("آدرس", self.address); f.addRow("توضیحات", self.notes)
        b = X.bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); b.accepted.connect(self.accept); b.rejected.connect(self.reject); f.addRow(b)

    def accept(self):
        name = self.name.text().strip()
        if not name: return X.warn(self, "نام", "نام تأمین‌کننده را بنویس.")
        with X.db() as con:
            dup = con.execute("SELECT id FROM suppliers WHERE name=? AND id<>?", (name, self.sid or 0)).fetchone()
            if dup: return X.warn(self, "تکراری", "تأمین‌کننده‌ای با این نام قبلاً ثبت شده است.")
            if self.sid: con.execute("UPDATE suppliers SET name=?,phone=?,address=?,notes=? WHERE id=?", (name, self.phone.text().strip(), self.address.text().strip(), self.notes.text().strip(), self.sid)); self.saved_id = self.sid
            else: self.saved_id = con.execute("INSERT INTO suppliers(name,phone,address,notes,created_at) VALUES(?,?,?,?,?)",
                                              (name, self.phone.text().strip(), self.address.text().strip(), self.notes.text().strip(), X.now_iso())).lastrowid
        super().accept()


# ───────────────────────── فاکتور خرید / برگشت از خرید ─────────────────────────
class PurchaseDialog(QDialog):
    COLS = ["نام کالا", "شرح", "تعداد", "قیمت خرید واحد (تومان)", "جمع ردیف"]

    def __init__(self, parent=None, pid=None, doc_type="purchase", supplier_id=None):
        super().__init__(parent); self.pid = pid; self.doc_type = doc_type; self.saved_id = None; self.print_after = False; self._busy = False; self._loading = True; self._fp = None
        self.ret = (doc_type == "return")
        with X.db() as con:
            h, its = P.load_doc(con, pid) if pid else (None, [])
            if h: self.doc_type = doc_type = h["doc_type"]; self.ret = (doc_type == "return"); self._fp = P.doc_fp(con, pid)
        self.names = _names(); self.setWindowTitle(("ویرایش " if pid else "ثبت ") + P.DOC_LABEL[doc_type]); self.resize(1080, 700)
        root = QVBoxLayout(self)
        bar = QHBoxLayout(); bar.setContentsMargins(4, 0, 4, 0)
        ok = QPushButton("ثبت " + P.DOC_LABEL[doc_type]); ok.setDefault(True); ok.clicked.connect(lambda: self.save(False))
        pv = QPushButton("ذخیره و چاپ"); pv.clicked.connect(lambda: self.save(True)); cancel = QPushButton("لغو"); cancel.clicked.connect(self.reject)
        bar.addWidget(ok); bar.addWidget(pv); bar.addWidget(cancel); bar.addStretch(); root.addLayout(bar)
        g = QGridLayout(); root.addLayout(g)
        self.sup = QComboBox()
        for s in _suppliers(): self.sup.addItem(s["name"], s["id"])
        X.make_searchable_combo(self.sup, "تایپ کن تا تأمین‌کننده پیدا شود...")
        want = h["supplier_id"] if h else supplier_id
        if want: self.sup.setCurrentIndex(max(0, self.sup.findData(want)))
        newsup = QPushButton("＋ تأمین‌کننده"); newsup.clicked.connect(self._new_supplier)
        self.date = X.JalaliDateEdit(h["doc_date"] if h else None); self.ref = QLineEdit(h["ref_no"] if h else ""); self.ref.setPlaceholderText("شماره‌ی فاکتور روی برگه‌ی تأمین‌کننده")
        self.kind = QComboBox(); self.kind.addItems(list(P.KINDS)); self.due = X.JalaliDateEdit(h["due_date"] if h and h["due_date"] else None)
        self.desc = QLineEdit(h["description"] if h else ""); self.notes = QLineEdit(h["notes"] if h else "")
        self.no_label = _label(("شماره: " + h["doc_no"]) if h else "شماره: خودکار (بعد از ثبت)", True)
        g.addWidget(_label("تأمین‌کننده"), 0, 0); g.addWidget(self.sup, 0, 1); g.addWidget(newsup, 0, 2); g.addWidget(self.no_label, 0, 3)
        g.addWidget(_label("تاریخ (شمسی)"), 1, 0); g.addWidget(self.date, 1, 1); g.addWidget(_label("شماره‌ی فاکتور تأمین‌کننده"), 1, 2); g.addWidget(self.ref, 1, 3)
        if not self.ret:
            g.addWidget(_label("نوع پرداخت"), 2, 0); g.addWidget(self.kind, 2, 1); g.addWidget(_label("سررسید پرداخت"), 2, 2); g.addWidget(self.due, 2, 3)
        else:
            self.kind.hide(); self.due.hide()
        g.addWidget(_label("توضیحات"), 3, 0); g.addWidget(self.desc, 3, 1, 1, 3)
        self.items = QTableWidget(0, 5); self.items.setHorizontalHeaderLabels(self.COLS); self.items.setColumnWidth(0, 300); self.items.setColumnWidth(1, 220)
        self.items.horizontalHeader().setStretchLastSection(True); self.items.setItemDelegateForColumn(0, _NameDelegate(self.names, self.items)); root.addWidget(self.items, 1)
        row = QHBoxLayout(); self.hint = QLabel("بارکد کالا را اسکن کن؛ کالا خودکار اضافه می‌شود. با Enter روی آخرین ردیف، ردیف جدید باز می‌شود."); self.hint.setStyleSheet("color:#666;")
        dl = QPushButton("حذف ردیف"); dl.clicked.connect(self.del_row); row.addWidget(dl); row.addWidget(self.hint, 1); root.addLayout(row)
        s = QGridLayout(); root.addLayout(s)
        self.discount = X.money_spin(); self.paid = X.money_spin()
        self.sum_total = _label("", True); self.sum_net = _label("", True); self.sum_remain = _label("", True)
        s.addWidget(_label("جمع اقلام"), 0, 0); s.addWidget(self.sum_total, 0, 1); s.addWidget(_label("تخفیف (تومان)"), 0, 2); s.addWidget(X.with_words(self.discount), 0, 3)
        s.addWidget(_label("مبلغ نهایی"), 1, 0); s.addWidget(self.sum_net, 1, 1)
        s.addWidget(_label("وجه نقد دریافتی از تأمین‌کننده (تومان)" if self.ret else "پرداخت اولیه (تومان)"), 1, 2); s.addWidget(X.with_words(self.paid), 1, 3)
        s.addWidget(_label("مانده‌ی این سند" if not self.ret else "اعتبار ایجادشده نزد تأمین‌کننده"), 2, 0); s.addWidget(self.sum_remain, 2, 1)
        s.addWidget(_label("یادداشت"), 2, 2); s.addWidget(self.notes, 2, 3)
        self._prev_kind = self.kind.currentText()
        if h:
            self.discount.setValue(float(h["discount"] or 0)); self.paid.setValue(float(h["paid_initial"] or 0)); self.kind.setCurrentText(h["kind"] or "نقدی"); self._prev_kind = self.kind.currentText()
        for it in its: self._add_row(it["product_name"], it["description"], X.fmt_qty(it["quantity"]), X.fmt_qty(it["unit_price"]))
        for _ in range(max(0, 3 - len(its))): self._add_row()
        self.items.itemChanged.connect(self._cell_changed); self.discount.valueChanged.connect(self._recalc); self.paid.valueChanged.connect(self._recalc)
        self.kind.currentTextChanged.connect(self._kind_changed); self.items.installEventFilter(self)
        self._loading = False; self._recalc()
        self._sniffer = X.BarcodeSniffer(self); QApplication.instance().installEventFilter(self._sniffer)
        self.finished.connect(lambda *_: QApplication.instance().removeEventFilter(self._sniffer))

    # —— جدول ——
    def _add_row(self, name="", desc="", qty="1", price=""):
        self._busy = True; r = self.items.rowCount(); self.items.insertRow(r)
        for c, v in enumerate((name, desc, qty, price)): self.items.setItem(r, c, X.table_item(str(v if v is not None else ""), Qt.AlignRight | Qt.AlignVCenter) if c in (2, 3) else QTableWidgetItem(str(v or "")))
        t = X.table_item("", Qt.AlignRight | Qt.AlignVCenter); t.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled); self.items.setItem(r, 4, t); self._busy = False
        self._row_total(r)

    def _txt(self, r, c):
        it = self.items.item(r, c); return it.text().strip() if it is not None else ""

    def _num(self, r, c):
        try: return X.parse_num(self._txt(r, c) or "0")
        except Exception: return 0.0

    def _row_total(self, r):
        it = self.items.item(r, 4)
        if it is not None: self._busy = True; it.setText(X.money(self._num(r, 2) * self._num(r, 3)) if self._txt(r, 3) else ""); self._busy = False

    def del_row(self):
        r = self.items.currentRow()
        if r >= 0: self.items.removeRow(r); self._recalc()

    def _cell_changed(self, item):
        if self._busy or self._loading: return
        r, c = item.row(), item.column()
        if c == 0 and item.text().strip() and not self._txt(r, 3):               # آخرین قیمت خرید همین کالا
            with X.db() as con: price = P.last_purchase_price(con, item.text(), self.sup.currentData())
            if price > 0: self._busy = True; self.items.setItem(r, 3, X.table_item(X.fmt_qty(price), Qt.AlignRight | Qt.AlignVCenter)); self._busy = False
        if c in (0, 2, 3): self._row_total(r)
        self._recalc()

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.items and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.items.state() != QAbstractItemView.EditingState:
            r = self.items.currentRow()
            if r >= self.items.rowCount() - 1: self._add_row()
            self.items.setCurrentCell(min(r + 1, self.items.rowCount() - 1), 0); return True
        return super().eventFilter(obj, ev)

    def _rows(self):
        out = []
        for r in range(self.items.rowCount()):
            name, desc = self._txt(r, 0), self._txt(r, 1)
            if not (name or desc or self._txt(r, 3)): continue
            out.append((name, desc, self._num(r, 2), self._num(r, 3)))
        return out

    # —— مبلغ‌ها و نوع پرداخت ——
    def _net(self): return P.doc_total(self._rows(), self.discount.value())

    def _recalc(self, *_):
        if self._loading: return
        gross = P.items_total(self._rows()); net = self._net()
        self.sum_total.setText(f"{X.money(gross)} تومان"); self.sum_net.setText(f"{X.money(net)} تومان")
        if not self.ret and self.kind.currentText() == "نقدی":
            self.paid.blockSignals(True); self.paid.setValue(net); self.paid.setEnabled(False); self.paid.blockSignals(False)
        else: self.paid.setEnabled(True)
        self.sum_remain.setText(f"{X.money(max(0.0, net - self.paid.value()) if not self.ret else net - self.paid.value())} تومان")
        self.due.setEnabled(self.kind.currentText() != "نقدی")

    def _kind_changed(self, kind):
        """مثل فروش: نقدی ← پرداخت اولیه = کل مبلغ؛ اعتباری/چکی ← صفر (مبلغ دلخواه را خودت وارد می‌کنی)."""
        prev, self._prev_kind = self._prev_kind, kind
        if not self._loading and kind != "نقدی" and prev == "نقدی": self.paid.setValue(0)
        self._recalc()

    def _new_supplier(self):
        d = SupplierDialog(self)
        if d.exec() and d.saved_id:
            self.sup.addItem(dict(_suppliers_by_id()).get(d.saved_id, ""), d.saved_id); self.sup.setCurrentIndex(self.sup.findData(d.saved_id))

    # —— بارکد ——
    def _items_snapshot(self):
        return [[self._txt(r, c) for c in range(5)] for r in range(self.items.rowCount())]

    def _items_restore(self, snap):
        self._busy = True
        try:
            for r, row in enumerate(snap[:self.items.rowCount()]):
                for c, t in enumerate(row):
                    it = self.items.item(r, c)
                    if it is None: self.items.setItem(r, c, QTableWidgetItem(t))
                    elif it.text() != t: it.setText(t)
        finally: self._busy = False
        self._recalc()

    def _define_barcode(self, code):
        name, ok = QInputDialog.getItem(self, "تعریف بارکد جدید", f"بارکد «{code}» هنوز برای کالایی ثبت نشده.\nنام کالا را انتخاب کن یا تایپ کن؛ از این به بعد با اسکن همین بارکد، این کالا اضافه می‌شود:", [""] + self.names, 0, True)
        name = (name or "").strip()
        if not ok or not name: return None
        with X.db() as con: barcodes.define(con, code, name, "")
        if name not in self.names: self.names.append(name)
        return name

    def _barcode_scanned(self, code):
        """کالای بارکد اضافه می‌شود؛ اسکن دوباره‌ی همان کالا تعدادش را یکی زیاد می‌کند؛ قیمت = آخرین قیمت خرید."""
        code = barcodes.norm(code)
        with X.db() as con: row = barcodes.lookup(con, code)
        if row: name, desc = row["product_name"], row["description"] or ""
        else:
            name, desc = self._define_barcode(code), ""
            if not name: return
        key = X.norm_key(name)
        for r in range(self.items.rowCount()):
            if X.norm_key(self._txt(r, 0)) == key:
                self._busy = True; self.items.setItem(r, 2, X.table_item(X.fmt_qty(self._num(r, 2) + 1), Qt.AlignRight | Qt.AlignVCenter)); self._busy = False
                self._row_total(r); self._recalc(); self.items.setCurrentCell(r, 2); return
        with X.db() as con: price = P.last_purchase_price(con, name, self.sup.currentData())
        r = next((i for i in range(self.items.rowCount()) if not self._txt(i, 0) and not self._txt(i, 3)), None)
        if r is None: self._add_row(); r = self.items.rowCount() - 1
        self._busy = True
        for c, v in ((0, name), (1, desc), (2, "1"), (3, X.fmt_qty(price) if price > 0 else "")):
            self.items.setItem(r, c, X.table_item(v, Qt.AlignRight | Qt.AlignVCenter) if c in (2, 3) else QTableWidgetItem(v))
        self._busy = False; self._row_total(r); self._recalc(); self.items.setCurrentCell(r, 2 if price > 0 else 3)

    # —— ثبت ——
    def save(self, print_after=False):
        if self.sup.currentData() is None: return X.warn(self, "تأمین‌کننده", "ابتدا تأمین‌کننده را انتخاب کن یا با «＋ تأمین‌کننده» بساز.")
        idate = X.need_date(self, self.date, "تاریخ سند")
        if not idate: return
        items = self._rows()
        if not items: return X.warn(self, "خالی", "حداقل یک ردیف کالا وارد کن.")
        for n, _d, q, p in items:
            if not n: return X.warn(self, "نام کالا", "نام کالا در همه‌ی ردیف‌ها باید پر باشد.")
            if q <= 0 or p < 0: return X.warn(self, "ورودی نامعتبر", "تعداد باید بیشتر از صفر و قیمت نباید منفی باشد.")
        net = self._net(); paid = float(self.paid.value()); kind = "نقدی" if self.ret else self.kind.currentText()
        if self.discount.value() > P.items_total(items): return X.warn(self, "تخفیف", "تخفیف از جمع اقلام بیشتر است.")
        if paid > net + 0.5: return X.warn(self, "مبلغ", "مبلغ پرداخت/دریافت از مبلغ نهایی سند بیشتر است.")
        due = ""
        if kind != "نقدی" and not self.ret:
            due = self.due.iso() or ""
        try:
            with X.db() as con:
                self.saved_id, no = P.save_doc(con, self.pid, self.doc_type, self.sup.currentData(), idate, self.ref.text().strip(), kind, due, self.desc.text().strip(),
                                               self.discount.value(), paid, self.notes.text().strip(), items, expected_fp=self._fp if self.pid else None)
        except P.Conflict as e: return X.warn(self, "ویرایش هم‌زمان", str(e) + "\nبرای اینکه تغییرات او پاک نشود، پنجره را ببند و سند را دوباره باز کن.")
        X.audit(("ویرایش " if self.pid else "ثبت ") + P.DOC_LABEL[self.doc_type], f"شماره {no} — {self.sup.currentText()} — {X.money(net)} تومان")
        self.print_after = bool(print_after); self._finish_accept()

    def _finish_accept(self): super().accept()


def _suppliers_by_id():
    return [(r["id"], r["name"]) for r in _suppliers()]


def print_doc(parent, pid):
    with X.db() as con: h, its = P.load_doc(con, pid)
    items = [(i["product_name"], i["description"], i["quantity"], i["unit_price"]) for i in its]; gross = P.items_total(items); net = P.doc_total(items, h["discount"])
    rows = [[n + 1, a, b, X.fmt_qty(q), X.money(p), X.money(q * p)] for n, (a, b, q, p) in enumerate(items)]
    html = (f'<h2 align="center">{P.DOC_LABEL[h["doc_type"]]} شماره {h["doc_no"]}</h2><p>تأمین‌کننده: <b>{h["supplier_name"]}</b> &nbsp;&nbsp; تاریخ: {X.iso_to_j(h["doc_date"])}'
            + (f' &nbsp;&nbsp; شماره‌ی فاکتور تأمین‌کننده: {h["ref_no"]}' if h["ref_no"] else "") + "</p>" + _table_html(["ردیف", "نام کالا", "شرح", "تعداد", "قیمت واحد", "جمع"], rows)
            + f'<p>جمع اقلام: {X.money(gross)} — تخفیف: {X.money(h["discount"] or 0)} — <b>مبلغ نهایی: {X.money(net)} تومان</b><br>'
            + (f'وجه نقد دریافتی: {X.money(h["paid_initial"] or 0)}' if h["doc_type"] == "return" else f'پرداخت اولیه: {X.money(h["paid_initial"] or 0)} — نوع پرداخت: {h["kind"]}')
            + (f'<br>توضیحات: {h["description"]}' if h["description"] else "") + "</p>")
    print_html(parent, f"{P.DOC_LABEL[h['doc_type']]} {h['doc_no']}", html)


# ───────────────────────── پرداخت به تأمین‌کننده ─────────────────────────
class SupplierPaymentDialog(QDialog):
    def __init__(self, parent=None, supplier_id=None, pay_id=None):
        super().__init__(parent); self.pay_id = pay_id; self.saved_id = None; self.setWindowTitle("ویرایش پرداخت" if pay_id else "ثبت پرداخت به تأمین‌کننده"); self.setMinimumWidth(520)
        data = None
        if pay_id:
            with X.db() as con: data = con.execute("SELECT * FROM supplier_payments WHERE id=?", (pay_id,)).fetchone()
        f = QFormLayout(self)
        self.sup = QComboBox()
        for s in _suppliers(): self.sup.addItem(s["name"], s["id"])
        X.make_searchable_combo(self.sup, "تایپ کن تا تأمین‌کننده پیدا شود...")
        want = data["supplier_id"] if data else supplier_id
        if want: self.sup.setCurrentIndex(max(0, self.sup.findData(want)))
        self.bal = _label("", True); self.doc = QComboBox(); self.date = X.JalaliDateEdit(data["pay_date"] if data else None); self.amount = X.money_spin()
        self.method = QComboBox(); self.method.addItems(P.PAY_METHODS); self.ref = QLineEdit(); self.notes = QLineEdit()
        self.chq_due = X.JalaliDateEdit(data["due_date"] if data and data["due_date"] else None); self.cleared = QCheckBox("چک پاس شده است")
        if data:
            self.amount.setValue(float(data["amount"])); self.method.setCurrentText(data["method"]); self.ref.setText(data["reference"] or ""); self.notes.setText(data["notes"] or "")
            self.cleared.setChecked(bool(data["cleared"]))
        self._fill_docs(data["purchase_id"] if data else None); self.sup.currentIndexChanged.connect(lambda _i: self._fill_docs(None)); self.method.currentTextChanged.connect(self._method_changed)
        f.addRow("تأمین‌کننده", self.sup); f.addRow("مانده‌ی فعلی حساب", self.bal); f.addRow("فاکتور خرید مرتبط (اختیاری)", self.doc); f.addRow("تاریخ پرداخت (شمسی)", self.date)
        f.addRow("مبلغ (تومان)", X.with_words(self.amount)); f.addRow("روش پرداخت", self.method); f.addRow("شماره پیگیری / شماره چک", self.ref)
        f.addRow("سررسید چک", self.chq_due); f.addRow("", self.cleared); f.addRow("توضیحات", self.notes)
        b = X.bbox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); b.accepted.connect(self.accept); b.rejected.connect(self.reject); f.addRow(b); self._method_changed(self.method.currentText())

    def _method_changed(self, m):
        chq = (m == "چک"); self.chq_due.setEnabled(chq); self.cleared.setEnabled(chq)
        if not chq: self.cleared.setChecked(True)

    def _fill_docs(self, select_id):
        sid = self.sup.currentData(); self.doc.clear(); self.doc.addItem("بدون اتصال به فاکتور (پرداخت عمومی)", None)
        if sid is None: return
        with X.db() as con:
            docs = [d for d in P.doc_rows(con, supplier_id=sid, doc_type="purchase") if d["remain"] > 0.5 or d["id"] == select_id]; bal = P.supplier_figures(con).get(sid, {}).get("balance", 0.0)
        for d in docs: self.doc.addItem(f"{d['doc_no']} — {X.iso_to_j(d['doc_date'])} — مانده {X.money(d['remain'])}", d["id"])
        self.bal.setText(_balance_text(bal))
        if select_id: self.doc.setCurrentIndex(max(0, self.doc.findData(select_id)))

    def accept(self):
        if self.sup.currentData() is None: return X.warn(self, "تأمین‌کننده", "ابتدا تأمین‌کننده را انتخاب کن.")
        pdate = X.need_date(self, self.date, "تاریخ پرداخت")
        if not pdate: return
        if self.amount.value() <= 0: return X.warn(self, "مبلغ نامعتبر", "مبلغ پرداخت باید بیشتر از صفر باشد.")
        chq = self.method.currentText() == "چک"; due = (self.chq_due.iso() or "") if chq else ""
        if chq and not due: return X.warn(self, "سررسید چک", "تاریخ سررسید چک را وارد کن.")
        vals = (self.sup.currentData(), self.doc.currentData(), pdate, self.amount.value(), self.method.currentText(), self.ref.text().strip(), self.notes.text().strip(), due, 1 if (not chq or self.cleared.isChecked()) else 0)
        with X.db() as con:
            if self.pay_id: con.execute("UPDATE supplier_payments SET supplier_id=?,purchase_id=?,pay_date=?,amount=?,method=?,reference=?,notes=?,due_date=?,cleared=? WHERE id=?", vals + (self.pay_id,)); self.saved_id = self.pay_id
            else: self.saved_id = con.execute("INSERT INTO supplier_payments(supplier_id,purchase_id,pay_date,amount,method,reference,notes,due_date,cleared,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", vals + (X.now_iso(),)).lastrowid
        X.audit("ثبت پرداخت به تأمین‌کننده" if not self.pay_id else "ویرایش پرداخت به تأمین‌کننده", f"{self.sup.currentText()} — {X.money(self.amount.value())} تومان — {self.method.currentText()}")
        super().accept()


# ───────────────────────── صورت‌حساب و چک‌های پرداختی ─────────────────────────
class StatementDialog(QDialog):
    def __init__(self, parent, sid):
        super().__init__(parent); self.sid = sid; self.resize(940, 600)
        with X.db() as con: self.name = con.execute("SELECT name FROM suppliers WHERE id=?", (sid,)).fetchone()["name"]
        self.setWindowTitle(f"صورت‌حساب تأمین‌کننده — {self.name}"); l = QVBoxLayout(self); self.head = _label("", True); l.addWidget(self.head)
        self.table = QTableWidget(0, 5); self.table.setHorizontalHeaderLabels(["تاریخ", "شرح", "کاهش بدهی ما (پرداخت/برگشت)", "افزایش بدهی ما (خرید)", "مانده"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(1, 380); l.addWidget(self.table)
        row = QHBoxLayout()
        for t, fn in (("＋ ثبت پرداخت", self.pay), ("ویرایش پرداخت", self.edit_pay), ("حذف پرداخت", self.del_pay), ("چاپ", self.print_), ("خروجی Excel", lambda: X.export_table(self.table, f"صورت‌حساب {self.name}")), ("بستن", self.accept)):
            b = QPushButton(t); b.clicked.connect(fn); row.addWidget(b)
        l.addLayout(row); self.refresh()

    def refresh(self):
        with X.db() as con: self.rows = P.statement(con, self.sid)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            for c, v in enumerate((X.iso_to_j(r["date"]) + (" " + r["time"] if r.get("time") else ""), r["text"], X.money(r["dec"]) if r["dec"] else "", X.money(r["inc"]) if r["inc"] else "", X.money(r["balance"]))):
                it = X.table_item(v) if c else QTableWidgetItem(v); self.table.setItem(i, c, it)
                if r["kind"] == "payment": it.setBackground(QBrush(QColor("#eaf5ea")))
        bal = self.rows[-1]["balance"] if self.rows else 0.0; self.head.setText(f"{self.name} — مانده: {_balance_text(bal)}")

    def _sel_payment(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self.rows) or self.rows[r]["kind"] != "payment": X.warn(self, "انتخاب پرداخت", "یک ردیفِ «پرداخت» را انتخاب کن."); return None
        return self.rows[r]["ref_id"]

    def pay(self):
        if SupplierPaymentDialog(self, self.sid).exec(): self.refresh()

    def edit_pay(self):
        pid = self._sel_payment()
        if pid and SupplierPaymentDialog(self, self.sid, pid).exec(): self.refresh()

    def del_pay(self):
        pid = self._sel_payment()
        if not pid or not X.can("delete"): return None if pid is None else X.warn(self, "دسترسی ندارید", "حذف پرداخت برای کاربر فعلی مجاز نیست.")
        if not X.ask(self, "تأیید حذف", "این پرداخت حذف شود؟ مانده‌ی حساب دوباره محاسبه می‌شود."): return
        rsn = X.ask_reason(self)
        if rsn is None: return
        with X.db() as con: con.execute("DELETE FROM supplier_payments WHERE id=?", (pid,))
        X.audit("حذف پرداخت به تأمین‌کننده", f"{self.name} — شناسه {pid}: {rsn}"); self.refresh()

    def print_(self):
        rows = [[X.iso_to_j(r["date"]), r["text"], X.money(r["dec"]) if r["dec"] else "", X.money(r["inc"]) if r["inc"] else "", X.money(r["balance"])] for r in self.rows]
        print_html(self, f"صورت‌حساب {self.name}", f'<h2 align="center">صورت‌حساب تأمین‌کننده: {self.name}</h2><p>{self.head.text()}</p>' + _table_html(["تاریخ", "شرح", "کاهش بدهی ما", "افزایش بدهی ما", "مانده"], rows))


class ChequesPayableDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("چک‌های پرداختیِ پاس‌نشده"); self.resize(820, 480); l = QVBoxLayout(self); self.ids = []
        self.table = QTableWidget(0, 5); self.table.setHorizontalHeaderLabels(["سررسید", "تأمین‌کننده", "شماره چک", "مبلغ", "توضیحات"]); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.horizontalHeader().setStretchLastSection(True); l.addWidget(self.table)
        self.total = _label("", True); l.addWidget(self.total); row = QHBoxLayout()
        b1 = QPushButton("✔ پاس شد"); b1.clicked.connect(self.clear_selected); b2 = QPushButton("بستن"); b2.clicked.connect(self.accept); row.addWidget(b1); row.addWidget(b2); l.addLayout(row); self.refresh()

    def refresh(self):
        with X.db() as con: rows = P.pending_cheques(con)
        self.ids = [r["id"] for r in rows]; self.table.setRowCount(len(rows)); today = X.today_iso(); tot = 0.0
        for i, r in enumerate(rows):
            tot += r["amount"]
            for c, v in enumerate((X.iso_to_j(r["due_date"]), r["supplier_name"], r["reference"] or "", X.money(r["amount"]), r["notes"] or "")):
                it = X.table_item(v) if c == 3 else QTableWidgetItem(v); self.table.setItem(i, c, it)
                if r["due_date"] <= today: it.setBackground(QBrush(QColor("#fde0e0")))
        self.total.setText(f"جمع چک‌های پاس‌نشده: {X.money(tot)} تومان (ردیف‌های قرمز: سررسیدشده)")

    def clear_selected(self):
        r = self.table.currentRow()
        if r < 0: return X.warn(self, "انتخاب", "یک چک را انتخاب کن.")
        with X.db() as con: con.execute("UPDATE supplier_payments SET cleared=1 WHERE id=?", (self.ids[r],))
        self.refresh()


# ───────────────────────── تب‌ها ─────────────────────────
def _make_table(headers):
    t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers); t.setAlternatingRowColors(True); t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.horizontalHeader().setStretchLastSection(True); t.verticalHeader().setVisible(False); return t


def _bar(layout, widgets):
    b = QHBoxLayout()
    for w in widgets: b.addWidget(w)
    layout.addLayout(b)


def _btn(text, fn):
    b = QPushButton(text); b.clicked.connect(fn); return b


class PurchasesPanel(QWidget):
    HEAD = ["شناسه", "نوع", "شماره", "تاریخ", "تأمین‌کننده", "شماره‌ی فاکتور تأمین‌کننده", "مبلغ نهایی", "پرداخت‌شده", "مانده", "توضیحات", "ساعت ثبت"]

    def __init__(self, parent=None, on_change=None):
        super().__init__(parent); self.on_change = on_change; self.rows = []; l = QVBoxLayout(self)
        self.search = QLineEdit(); self.search.setPlaceholderText("جست‌وجو: شماره، تأمین‌کننده، نام کالا، توضیحات..."); self.search.textChanged.connect(self.refresh)
        self.type = QComboBox(); self.type.addItems(["همه‌ی اسناد", "فقط فاکتور خرید", "فقط برگشت از خرید"]); self.type.currentIndexChanged.connect(self.refresh)
        _bar(l, [self.search, self.type, _btn("＋ فاکتور خرید", lambda: self.new("purchase")), _btn("＋ برگشت از خرید", lambda: self.new("return")), _btn("ویرایش", self.edit), _btn("حذف", self.delete),
                 _btn("چاپ", self.print_), _btn("پرداخت به تأمین‌کننده", self.pay), _btn("خروجی Excel", lambda: X.export_table(self.table, "خرید"))])
        self.table = _make_table(self.HEAD); self.table.setColumnHidden(0, True); self.table.doubleClicked.connect(self.edit); l.addWidget(self.table)
        self.foot = _label("", True); l.addWidget(self.foot); self.refresh()

    def showEvent(self, e): self.refresh(); super().showEvent(e)

    def _sel(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self.rows): X.warn(self, "انتخاب سند", "ابتدا یک سند را انتخاب کن."); return None
        return self.rows[r]

    def refresh(self, *_):
        t = ["", "purchase", "return"][self.type.currentIndex()] if hasattr(self, "type") else ""
        with X.db() as con: self.rows = P.doc_rows(con, doc_type=t or None, text=self.search.text().strip())
        self.table.setRowCount(len(self.rows)); buy = ret = rem = 0.0
        for i, d in enumerate(self.rows):
            vals = (d["id"], P.DOC_LABEL[d["doc_type"]], d["doc_no"], X.iso_to_j(d["doc_date"]), d["supplier_name"], d["ref_no"], X.money(d["total"]), X.money(d["paid"]),
                    X.money(d["remain"]) if d["doc_type"] == "purchase" else "", d["description"], (d["created_at"] or "")[11:16])
            for c, v in enumerate(vals):
                it = X.table_item(str(v)) if c in (6, 7, 8) else QTableWidgetItem(str(v)); self.table.setItem(i, c, it)
                if d["doc_type"] == "return": it.setForeground(QBrush(QColor("#b26a00")))
            if d["doc_type"] == "purchase": buy += d["total"]; rem += d["remain"]
            else: ret += d["total"]
        self.foot.setText(f"جمع خرید: {X.money(buy)} — جمع برگشت از خرید: {X.money(ret)} — خالص: {X.money(buy - ret)} — مانده‌ی پرداخت نشده‌ی فاکتورها: {X.money(rem)} تومان")

    def _done(self):
        self.refresh()
        if self.on_change: self.on_change()

    def new(self, doc_type):
        d = PurchaseDialog(self, None, doc_type)
        if d.exec():
            self._done()
            if d.print_after and d.saved_id: print_doc(self, d.saved_id)

    def edit(self, *_):
        s = self._sel()
        if s is None: return
        d = PurchaseDialog(self, s["id"])
        if d.exec():
            self._done()
            if d.print_after and d.saved_id: print_doc(self, d.saved_id)

    def print_(self):
        s = self._sel()
        if s: print_doc(self, s["id"])

    def pay(self):
        s = self.rows[self.table.currentRow()] if 0 <= self.table.currentRow() < len(self.rows) else None
        if SupplierPaymentDialog(self, s["supplier_id"] if s else None).exec(): self._done()

    def delete(self):
        s = self._sel()
        if s is None: return
        if not X.can("delete"): return X.warn(self, "دسترسی ندارید", "حذف سند برای کاربر فعلی مجاز نیست.")
        if not X.ask(self, "تأیید حذف", f"{P.DOC_LABEL[s['doc_type']]} شماره {s['doc_no']} حذف شود؟ موجودی انبار و حساب تأمین‌کننده دوباره محاسبه می‌شود.\n(پرداخت‌های ثبت‌شده برای آن حذف نمی‌شوند.)"): return
        rsn = X.ask_reason(self)
        if rsn is None: return
        with X.db() as con:
            its = con.execute("SELECT product_name,quantity,unit_price FROM purchase_items WHERE purchase_id=? ORDER BY row_no", (s["id"],)).fetchall()
            snap = "؛ ".join(f"{i['product_name']}×{X.fmt_qty(i['quantity'])}@{X.fmt_qty(i['unit_price'])}" for i in its)[:600]
            P.delete_doc(con, s["id"])
        X.audit("حذف " + P.DOC_LABEL[s["doc_type"]], f"شماره {s['doc_no']} — {s['supplier_name']} — {X.money(s['total'])} تومان — اقلام: {snap} — دلیل: {rsn}"); self._done()


class SuppliersPanel(QWidget):
    HEAD = ["شناسه", "تأمین‌کننده", "تلفن", "جمع خرید", "جمع برگشت", "پرداخت‌شده", "مانده (بدهی ما)", "آدرس"]

    def __init__(self, parent=None, on_change=None):
        super().__init__(parent); self.on_change = on_change; self.ids = []; l = QVBoxLayout(self)
        self.search = QLineEdit(); self.search.setPlaceholderText("جست‌وجوی تأمین‌کننده..."); self.search.textChanged.connect(self.refresh)
        _bar(l, [self.search, _btn("＋ تأمین‌کننده", self.new), _btn("ویرایش", self.edit), _btn("حذف", self.delete), _btn("صورت‌حساب", self.statement), _btn("ثبت پرداخت", self.pay),
                 _btn("چک‌های پرداختی", self.cheques), _btn("گزارش خرید کالا", self.report), _btn("خروجی Excel", lambda: X.export_table(self.table, "تأمین‌کنندگان"))])
        self.table = _make_table(self.HEAD); self.table.setColumnHidden(0, True); self.table.doubleClicked.connect(self.statement); l.addWidget(self.table)
        self.foot = _label("", True); l.addWidget(self.foot); self.refresh()

    def showEvent(self, e): self.refresh(); super().showEvent(e)

    def _sid(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self.ids): X.warn(self, "انتخاب", "ابتدا یک تأمین‌کننده را انتخاب کن."); return None
        return self.ids[r]

    def refresh(self, *_):
        q = self.search.text().strip() if hasattr(self, "search") else ""
        with X.db() as con: fig = P.supplier_figures(con)
        items = sorted(((i, f) for i, f in fig.items() if not q or q in f["name"] or q in (f["phone"] or "")), key=lambda t: t[1]["name"]); self.ids = [i for i, _ in items]
        self.table.setRowCount(len(items)); debt = 0.0
        for r, (i, f) in enumerate(items):
            for c, v in enumerate((i, f["name"], f["phone"], X.money(f["purchases"]), X.money(f["returns"]), X.money(f["paid"] - f["refunds"]), X.money(f["balance"]), f["address"])):
                it = X.table_item(str(v)) if c in (3, 4, 5, 6) else QTableWidgetItem(str(v)); self.table.setItem(r, c, it)
                if c == 6 and f["balance"] > 0.5: it.setForeground(QBrush(QColor("#c62828")))
            debt += max(0.0, f["balance"])
        self.foot.setText(f"جمع بدهی ما به تأمین‌کنندگان: {X.money(debt)} تومان — تعداد: {len(items)}")

    def _done(self):
        self.refresh()
        if self.on_change: self.on_change()

    def new(self):
        if SupplierDialog(self).exec(): self._done()

    def edit(self, *_):
        s = self._sid()
        if s and SupplierDialog(self, s).exec(): self._done()

    def delete(self):
        s = self._sid()
        if s is None: return
        if not X.can("delete"): return X.warn(self, "دسترسی ندارید", "حذف تأمین‌کننده برای کاربر فعلی مجاز نیست.")
        with X.db() as con: used = con.execute("SELECT (SELECT COUNT(*) FROM purchases WHERE supplier_id=?)+(SELECT COUNT(*) FROM supplier_payments WHERE supplier_id=?)", (s, s)).fetchone()[0]
        if used: return X.warn(self, "قابل حذف نیست", "این تأمین‌کننده سند یا پرداخت ثبت‌شده دارد؛ برای حفظ حساب‌ها حذف نمی‌شود.")
        if not X.ask(self, "تأیید حذف", "این تأمین‌کننده حذف شود؟"): return
        with X.db() as con: name = con.execute("SELECT name FROM suppliers WHERE id=?", (s,)).fetchone()["name"]; con.execute("DELETE FROM suppliers WHERE id=?", (s,))
        X.audit("حذف تأمین‌کننده", name); self._done()

    def statement(self, *_):
        s = self._sid()
        if s: StatementDialog(self, s).exec(); self._done()

    def pay(self):
        r = self.table.currentRow()
        if SupplierPaymentDialog(self, self.ids[r] if 0 <= r < len(self.ids) else None).exec(): self._done()

    def cheques(self): ChequesPayableDialog(self).exec()

    def report(self):
        with X.db() as con: rows = P.product_summary(con); s = P.period_summary(con)
        html = (f'<h2 align="center">گزارش خرید به تفکیک کالا</h2><p>جمع خرید: {X.money(s["purchases"])} — برگشت از خرید: {X.money(s["returns"])} — خالص: {X.money(s["net"])} — پرداخت‌ها: {X.money(s["payments"])}</p>'
                + _table_html(["کالا", "تعداد خالص", "مبلغ خالص", "میانگین قیمت خرید"], [[r["name"], X.fmt_qty(r["qty"]), X.money(r["amount"]), X.money(r["avg"])] for r in rows]))
        print_html(self, "گزارش خرید", html)
