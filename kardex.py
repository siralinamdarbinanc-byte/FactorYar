# -*- coding: utf-8 -*-
"""کاردکس کالا: گردش و موجودی هر کالا.

- خروجی‌ها خودکار از اقلام «فاکتورهای فروش» (نه پیش‌فاکتور) گرفته می‌شوند؛ با ویرایش یا حذف فاکتور، کاردکس هم درست می‌شود.
- ورودی‌ها (خرید، موجودی اول دوره، تعدیل) دستی در جدول stock_moves ثبت می‌شوند.
- ارزش موجودی با «میانگین موزون متحرک» قیمت خرید حساب می‌شود.
- کالاها با نام تطبیق داده می‌شوند (ی/ک عربی، ارقام و نیم‌فاصله یکسان‌سازی می‌شود).
"""
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QSplitter, QMessageBox, QFormLayout, QDialogButtonBox, QFileDialog,
)
from ui_fit import QDialog

KIND_LABEL = {"opening": "موجودی اول دوره", "purchase": "خرید / ورود", "adjust": "تعدیل"}
RED, GREEN, GRAY = QBrush(QColor("#c62828")), QBrush(QColor("#2e7d32")), QBrush(QColor("#6b6b6b"))


# ───────────────────────────── دیتابیس و محاسبه ─────────────────────────────
def ensure_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS stock_moves(
      id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, name TEXT NOT NULL, move_date TEXT NOT NULL,
      kind TEXT NOT NULL, qty REAL NOT NULL, unit_cost REAL DEFAULT 0, ref TEXT DEFAULT '', notes TEXT DEFAULT '',
      created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_stock_key ON stock_moves(key, move_date);
    """)


def fq(x):
    """نمایش تعداد: عدد صحیح بدون اعشار، غیر صحیح تا ۳ رقم؛ منفی با علامت امن برای راست‌به‌چپ."""
    x = float(x or 0)
    s = f"{abs(int(round(x))):,}" if abs(x - round(x)) < 1e-9 else f"{abs(x):,.3f}".rstrip("0").rstrip(".")
    return f"\u202a-{s}\u202c" if x < -1e-9 else s


def load_ledgers(con, norm_key):
    """-> (names{key:name}, moves{key:[move,...]}). هر move: date, rank, seq, mid, doc, desc, qty(+ورود/-خروج), price."""
    names, moves = {}, {}
    for r in con.execute("SELECT id,key,name,move_date,kind,qty,unit_cost,ref,notes FROM stock_moves"):
        k = r["key"]; names[k] = r["name"]; q = float(r["qty"])
        desc = (r["ref"] or "") + (" — " + r["notes"] if r["notes"] else "")
        moves.setdefault(k, []).append(dict(date=r["move_date"], rank=0 if q > 0 else 1, seq=r["id"], mid=r["id"], kind=r["kind"],
                                            doc=KIND_LABEL.get(r["kind"], r["kind"]), desc=desc.strip(" —"), qty=q, price=float(r["unit_cost"] or 0)))
    for r in con.execute("""SELECT it.product_name, it.quantity, it.unit_price, i.id iid, i.invoice_no, i.invoice_date, c.name cname
                            FROM invoice_items it JOIN invoices i ON i.id=it.invoice_id JOIN customers c ON c.id=i.customer_id
                            WHERE COALESCE(i.is_proforma,0)=0"""):
        k = norm_key(r["product_name"])
        if not k: continue
        names.setdefault(k, str(r["product_name"]).strip())
        moves.setdefault(k, []).append(dict(date=r["invoice_date"], rank=1, seq=r["iid"], mid=None, kind="sale", doc=f"فاکتور {r['invoice_no']}",
                                            desc=r["cname"], qty=-float(r["quantity"] or 0), price=float(r["unit_price"] or 0)))
    try:     # فاکتور خرید: ورود کالا با قیمت تمام‌شده (بعد از سهم تخفیف سند)؛ برگشت از خرید: خروج کالا
        for r in con.execute("""SELECT it.product_name, it.quantity, it.unit_price, p.id pid, p.doc_type, p.doc_no, p.doc_date, p.discount, s.name sname,
                                       (SELECT SUM(quantity*unit_price) FROM purchase_items WHERE purchase_id=p.id) gross
                                FROM purchase_items it JOIN purchases p ON p.id=it.purchase_id JOIN suppliers s ON s.id=p.supplier_id"""):
            k = norm_key(r["product_name"])
            if not k: continue
            names.setdefault(k, str(r["product_name"]).strip()); q = float(r["quantity"] or 0); gross = float(r["gross"] or 0)
            cost = float(r["unit_price"] or 0) * (1 - float(r["discount"] or 0) / gross) if gross > 0 else float(r["unit_price"] or 0)
            if r["doc_type"] == "purchase":
                moves.setdefault(k, []).append(dict(date=r["doc_date"], rank=0, seq=r["pid"], mid=None, kind="purchase_doc", doc=f"خرید {r['doc_no']}", desc=r["sname"], qty=q, price=cost))
            else:
                moves.setdefault(k, []).append(dict(date=r["doc_date"], rank=1, seq=r["pid"], mid=None, kind="purchase_return", doc=f"برگشت از خرید {r['doc_no']}", desc=r["sname"], qty=-q, price=0.0))
    except Exception: pass
    try:     # مرجوعی‌ها: ورود کالا (قیمت صفر تا میانگین خرید را خراب نکند)
        for r in con.execute("""SELECT it.product_name, it.quantity, r.id rid, r.return_no, r.return_date, c.name cname
                                FROM return_items it JOIN returns r ON r.id=it.return_id JOIN customers c ON c.id=r.customer_id"""):
            k = norm_key(r["product_name"])
            if not k: continue
            names.setdefault(k, str(r["product_name"]).strip())
            moves.setdefault(k, []).append(dict(date=r["return_date"], rank=0, seq=r["rid"], mid=None, kind="return", doc=f"مرجوعی {r['return_no']}",
                                                desc=r["cname"], qty=float(r["quantity"] or 0), price=0.0))
    except Exception: pass
    return names, moves


def running(ms):
    """ردیف‌ها را به ترتیب زمان مرتب می‌کند و مانده‌ی تعداد، میانگین خرید و ارزش مانده را کنارشان می‌گذارد."""
    out, stock, avg = [], 0.0, 0.0
    for m in sorted(ms, key=lambda m: (m["date"], m["rank"], m["seq"])):
        q = m["qty"]
        if q > 0 and m["price"] > 0:
            base = max(stock, 0.0); tot = base + q
            avg = (base * avg + q * m["price"]) / tot if tot > 0 else m["price"]
        stock += q
        out.append(dict(m, stock=stock, avg=avg, value=stock * avg))
    return out


def summarize(rows):
    qin = sum(r["qty"] for r in rows if r["qty"] > 0); qout = -sum(r["qty"] for r in rows if r["qty"] < 0)
    last = rows[-1] if rows else None
    return dict(qin=qin, qout=qout, stock=last["stock"] if last else 0.0, avg=last["avg"] if last else 0.0, value=last["value"] if last else 0.0)


# ───────────────────────────── پنجره‌ی ثبت ورود / تعدیل ─────────────────────────────
class MoveDialog(QDialog):
    TYPES = [("purchase", 1, "خرید / ورود کالا"), ("opening", 1, "موجودی اول دوره"),
             ("adjust", 1, "تعدیل — افزایش"), ("adjust", -1, "تعدیل — کاهش (ضایعات، شمارش، …)")]

    def __init__(self, H, names, name="", data=None, parent=None):
        super().__init__(parent); self.H = H
        self.setWindowTitle("ثبت ورود / تعدیل کالا" if not data else "ویرایش ردیف کاردکس"); self.resize(480, 380)
        l = QVBoxLayout(self); f = QFormLayout()
        self.name = QComboBox(); self.name.setEditable(True); self.name.addItems(sorted(names, key=lambda x: x))
        self.name.setEditText(name); f.addRow("نام کالا:", self.name)
        self.kind = QComboBox()
        for k, sg, t in self.TYPES: self.kind.addItem(t, (k, sg))
        f.addRow("نوع:", self.kind)
        self.date = H.DateEdit(); f.addRow("تاریخ:", self.date)
        self.qty = QLineEdit(); self.qty.setPlaceholderText("تعداد"); f.addRow("تعداد:", self.qty)
        self.cost = QLineEdit(); self.cost.setPlaceholderText("قیمت خرید هر واحد (برای ارزش‌گذاری؛ می‌تواند خالی باشد)"); f.addRow("قیمت خرید واحد:", self.cost)
        self.ref = QLineEdit(); self.ref.setPlaceholderText("مثلاً شماره فاکتور خرید یا نام فروشنده"); f.addRow("مرجع:", self.ref)
        self.notes = QLineEdit(); f.addRow("توضیح:", self.notes)
        l.addLayout(f)
        if data:
            sg = 1 if data["qty"] > 0 else -1
            for i in range(self.kind.count()):
                if tuple(self.kind.itemData(i)) == (data["kind"], sg): self.kind.setCurrentIndex(i)
            self.date.setIso(data["date"]); self.qty.setText(fq(abs(data["qty"])).replace(",", "")); self.cost.setText(str(int(data["price"])) if data["price"] else "")
            self.ref.setText(data.get("ref", "")); self.notes.setText(data.get("notes", ""))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); bb.accepted.connect(self._ok); bb.rejected.connect(self.reject); l.addWidget(bb)
        self._vals = None

    def _ok(self):
        name = self.name.currentText().strip()
        if not name: return QMessageBox.warning(self, "کالا", "نام کالا را بنویس.")
        iso = self.date.iso()
        if not iso: return QMessageBox.warning(self, "تاریخ", "تاریخ را به‌صورت شمسی و درست وارد کن.")
        try: q = self.H.parse_num(self.qty.text()); c = self.H.parse_num(self.cost.text())
        except Exception: return QMessageBox.warning(self, "عدد", "تعداد یا قیمت عدد معتبر نیست.")
        if q <= 0: return QMessageBox.warning(self, "تعداد", "تعداد باید بزرگ‌تر از صفر باشد.")
        k, sg = self.kind.currentData()
        self._vals = dict(name=name, kind=k, qty=sg * q, cost=c if sg > 0 else 0.0, date=iso, ref=self.ref.text().strip(), notes=self.notes.text().strip())
        self.accept()

    def values(self): return self._vals


# ───────────────────────────── پنجره‌ی کاردکس ─────────────────────────────
class KardexDialog(QDialog):
    """H: db, norm_key, parse_num, match, DateEdit, iso_to_j, strip_bidi (توابع برنامه‌ی اصلی)."""

    def __init__(self, H, parent=None):
        super().__init__(parent); self.H = H
        self.setWindowTitle("کاردکس کالا — گردش و موجودی"); self.resize(1250, 760)
        self.names, self.moves, self.run, self.sums = {}, {}, {}, {}; self.k_rows = []
        root = QVBoxLayout(self)
        root.addWidget(QLabel("خروجی هر کالا خودکار از فاکتورهای فروش (بدون پیش‌فاکتور) می‌آید. ورودی‌ها را با «ثبت ورود کالا» (خرید، موجودی اول دوره، تعدیل) وارد کن."))
        top = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("جست‌وجوی کالا...")
        self.search.textChanged.connect(lambda _t: self._fill_products())
        self.only_stock = QCheckBox("فقط دارای موجودی"); self.only_stock.toggled.connect(lambda _v: self._fill_products())
        self.only_neg = QCheckBox("فقط موجودی منفی"); self.only_neg.toggled.connect(lambda _v: self._fill_products())
        b_in = QPushButton("＋ ثبت ورود کالا"); b_in.clicked.connect(lambda: self._add_move())
        b_all = QPushButton("خروجی Excel همه‌ی کالاها"); b_all.clicked.connect(lambda: self._export(self.p_table, "موجودی کالاها"))
        for w in (self.search, self.only_stock, self.only_neg, b_in, b_all): top.addWidget(w)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal); root.addWidget(split, 1)
        self.p_table = QTableWidget(0, 5); self.p_table.setHorizontalHeaderLabels(["کالا", "ورودی", "خروجی", "موجودی", "ارزش موجودی"])
        self._style(self.p_table); self.p_table.itemSelectionChanged.connect(self._show_kardex); split.addWidget(self.p_table)

        right = QWidget(); r = QVBoxLayout(right)
        self.title = QLabel(""); self.title.setStyleSheet("font-weight:bold; font-size:12pt;"); r.addWidget(self.title)
        frow = QHBoxLayout(); self.d1 = H.DateEdit(allow_empty=True); self.d2 = H.DateEdit(allow_empty=True)
        for w in (QLabel("از تاریخ:"), self.d1, QLabel("تا تاریخ:"), self.d2):
            frow.addWidget(w)
        bf = QPushButton("اعمال"); bf.clicked.connect(self._show_kardex); frow.addWidget(bf)
        bc = QPushButton("همه‌ی تاریخ‌ها"); bc.clicked.connect(self._clear_dates); frow.addWidget(bc); frow.addStretch(); r.addLayout(frow)
        heads = ["تاریخ", "سند", "شرح", "ورود", "خروج", "مانده", "قیمت واحد", "ارزش مانده"]
        self.k_table = QTableWidget(0, len(heads)); self.k_table.setHorizontalHeaderLabels(heads); self._style(self.k_table); r.addWidget(self.k_table, 1)
        self.sum_lbl = QLabel(""); r.addWidget(self.sum_lbl)
        brow = QHBoxLayout()
        for t, fn in (("＋ ورود برای این کالا", lambda: self._add_move(self._cur_name())), ("ویرایش ردیف دستی", self._edit_move),
                      ("حذف ردیف دستی", self._del_move), ("خروجی Excel این کالا", lambda: self._export(self.k_table, self._cur_name() or "کاردکس"))):
            b = QPushButton(t); b.clicked.connect(fn); brow.addWidget(b)
        brow.addStretch(); r.addLayout(brow)
        split.addWidget(right); split.setStretchFactor(0, 2); split.setStretchFactor(1, 3)
        self.reload()

    @staticmethod
    def _style(t):
        t.setAlternatingRowColors(True); t.setSelectionBehavior(QAbstractItemView.SelectRows); t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.verticalHeader().setVisible(False); t.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _item(text, color=None, align=Qt.AlignRight | Qt.AlignVCenter):
        it = QTableWidgetItem(str(text)); it.setTextAlignment(align)
        if color is not None: it.setForeground(color)
        return it

    # ── داده ──
    def reload(self, keep_key=None):
        if keep_key is None: keep_key = self._cur_key()
        with self.H.db() as con: self.names, self.moves = load_ledgers(con, self.H.norm_key)
        self.run = {k: running(ms) for k, ms in self.moves.items()}
        self.sums = {k: summarize(rows) for k, rows in self.run.items()}
        self._fill_products(keep_key)

    def _cur_key(self):
        r = self.p_table.currentRow()
        it = self.p_table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _cur_name(self):
        k = self._cur_key(); return self.names.get(k, "") if k else ""

    def _fill_products(self, keep_key=None):
        if keep_key is None: keep_key = self._cur_key()
        q = self.search.text().strip(); rows = []
        for k, n in self.names.items():
            s = self.sums.get(k) or dict(qin=0, qout=0, stock=0, value=0)
            if q and not self.H.match(q, n): continue
            if self.only_stock.isChecked() and s["stock"] <= 1e-9: continue
            if self.only_neg.isChecked() and s["stock"] >= -1e-9: continue
            rows.append((n, k, s))
        rows.sort(key=lambda x: x[0])
        t = self.p_table; t.blockSignals(True); t.setRowCount(0); sel = -1
        for i, (n, k, s) in enumerate(rows):
            t.insertRow(i)
            first = self._item(n); first.setData(Qt.UserRole, k); t.setItem(i, 0, first)
            neg = s["stock"] < -1e-9
            t.setItem(i, 1, self._item(fq(s["qin"]))); t.setItem(i, 2, self._item(fq(s["qout"])))
            t.setItem(i, 3, self._item(fq(s["stock"]), RED if neg else (GRAY if abs(s["stock"]) < 1e-9 else None)))
            t.setItem(i, 4, self._item(self.H.strip_bidi(self.H.money(s["value"])) if s["value"] else "—"))
            if k == keep_key: sel = i
        t.resizeColumnsToContents(); t.horizontalHeader().setStretchLastSection(True); t.blockSignals(False)
        if t.rowCount(): t.selectRow(sel if sel >= 0 else 0)
        else: self._show_kardex()

    def _clear_dates(self):
        self.d1.setIso(""); self.d2.setIso(""); self._show_kardex()

    def _show_kardex(self, *_):
        k = self._cur_key(); t = self.k_table; t.setRowCount(0); self.k_rows = []
        if not k:
            self.title.setText("کالایی برای نمایش نیست"); self.sum_lbl.setText(""); return
        H = self.H; rows = self.run.get(k, [])
        d1, d2 = self.d1.iso() or "", self.d2.iso() or ""
        before = [r for r in rows if d1 and r["date"] < d1]
        shown = [r for r in rows if (not d1 or r["date"] >= d1) and (not d2 or r["date"] <= d2)]
        self.title.setText(f"کاردکس: {self.names.get(k, '')}")
        money = lambda v: H.strip_bidi(H.money(v)) if v else "—"
        def add(vals, color=None, mid=None, rec=None):
            i = t.rowCount(); t.insertRow(i)
            for j, v in enumerate(vals):
                it = self._item(v, color if j == 5 else None); t.setItem(i, j, it)
            self.k_rows.append(dict(mid=mid, rec=rec))
        if before:
            b = before[-1]; add(["", "مانده‌ی قبل", f"تا {H.iso_to_j(d1)}", "", "", fq(b["stock"]), "", money(b["value"])], GRAY)
        for r in shown:
            qin = fq(r["qty"]) if r["qty"] > 0 else ""; qout = fq(-r["qty"]) if r["qty"] < 0 else ""
            add([H.iso_to_j(r["date"]), r["doc"], r["desc"], qin, qout, fq(r["stock"]), money(r["price"]), money(r["value"])],
                RED if r["stock"] < -1e-9 else None, r["mid"], r)
        t.resizeColumnsToContents(); t.horizontalHeader().setStretchLastSection(True)
        s = self.sums.get(k, {}); self.sum_lbl.setText(
            f"کل ورودی: {fq(s.get('qin', 0))}    کل خروجی: {fq(s.get('qout', 0))}    موجودی فعلی: {fq(s.get('stock', 0))}    "
            f"میانگین خرید: {money(s.get('avg', 0))}    ارزش موجودی: {money(s.get('value', 0))}"
            + ("    ⚠ موجودی منفی: ورودی (خرید یا موجودی اول دوره) ثبت نشده" if s.get("stock", 0) < -1e-9 else ""))

    # ── ثبت / ویرایش / حذف ──
    def _add_move(self, name=""):
        dlg = MoveDialog(self.H, list(self.names.values()), name, None, self)
        if dlg.exec() and dlg.values(): self._save(dlg.values())

    def _save(self, v, mid=None):
        key = self.H.norm_key(v["name"]); disp = self.names.get(key) or v["name"]
        with self.H.db() as con:
            if mid is None:
                con.execute("INSERT INTO stock_moves(key,name,move_date,kind,qty,unit_cost,ref,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                            (key, disp, v["date"], v["kind"], v["qty"], v["cost"], v["ref"], v["notes"], datetime.now().isoformat(timespec="seconds")))
            else:
                con.execute("UPDATE stock_moves SET key=?,name=?,move_date=?,kind=?,qty=?,unit_cost=?,ref=?,notes=? WHERE id=?",
                            (key, disp, v["date"], v["kind"], v["qty"], v["cost"], v["ref"], v["notes"], mid))
        self.reload(key)

    def _manual_row(self):
        r = self.k_table.currentRow()
        if r < 0 or r >= len(self.k_rows) or self.k_rows[r]["mid"] is None:
            QMessageBox.information(self, "ردیف دستی", "فقط ردیف‌های دستی (خرید، موجودی اول دوره، تعدیل) قابل ویرایش و حذف‌اند. ردیف فروش را از خود فاکتور تغییر بده.")
            return None
        return self.k_rows[r]

    def _edit_move(self):
        row = self._manual_row()
        if not row: return
        with self.H.db() as con: m = con.execute("SELECT ref,notes FROM stock_moves WHERE id=?", (row["mid"],)).fetchone()
        rec = dict(row["rec"], ref=m["ref"] or "", notes=m["notes"] or "")
        dlg = MoveDialog(self.H, list(self.names.values()), self._cur_name(), rec, self)
        if dlg.exec() and dlg.values(): self._save(dlg.values(), row["mid"])

    def _del_move(self):
        row = self._manual_row()
        if not row: return
        if QMessageBox.question(self, "حذف", "این ردیف از کاردکس حذف شود؟") != QMessageBox.Yes: return
        with self.H.db() as con: con.execute("DELETE FROM stock_moves WHERE id=?", (row["mid"],))
        self.reload()

    # ── خروجی Excel ──
    def _export(self, table, title):
        path, _ = QFileDialog.getSaveFileName(self, f"خروجی {title}", f"{title}.xlsx", "Excel (*.xlsx)")
        if not path: return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            wb = Workbook(); ws = wb.active; ws.title = str(title)[:31]; ws.sheet_view.rightToLeft = True
            ws.append([table.horizontalHeaderItem(c).text() for c in range(table.columnCount())])
            for r in range(table.rowCount()):
                ws.append([self.H.strip_bidi((table.item(r, c).text() if table.item(r, c) else "").replace("\u202a", "").replace("\u202c", "")) for c in range(table.columnCount())])
            for cell in ws[1]: cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="286B4A")
            for col in ws.columns: ws.column_dimensions[col[0].column_letter].width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 40)
            wb.save(path); QMessageBox.information(self, "انجام شد", "فایل ذخیره شد.")
        except Exception as e:
            QMessageBox.warning(self, "خطا", str(e))
