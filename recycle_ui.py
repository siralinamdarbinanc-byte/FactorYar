# -*- coding: utf-8 -*-
"""سطل بازیافت و تاریخچه‌ی اسناد (فقط مدیر)."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem, QPushButton, QTextEdit,
                               QAbstractItemView, QMessageBox, QWidget, QHeaderView, QLabel)
from ui_fit import QDialog
import safety


def _table(headers):
    t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows); t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.horizontalHeader().setStretchLastSection(True)
    t.setAlternatingRowColors(True); t.verticalHeader().setVisible(False)
    return t


class RecycleDialog(QDialog):
    def __init__(self, db, user, fmt, parent=None):
        super().__init__(parent); self.db = db; self.user = user; self.fmt = fmt; self.changed = False; self.rows = []
        self.setWindowTitle("سطل بازیافت و تاریخچه (فقط مدیر)"); self.resize(980, 620)
        lay = QVBoxLayout(self); tabs = QTabWidget(); lay.addWidget(tabs)
        w1 = QWidget(); l1 = QVBoxLayout(w1)
        l1.addWidget(QLabel("هر چیزی که حذف شود اینجا می‌ماند و با همان شماره و اطلاعات قابل بازگردانی است."))
        self.t1 = _table(["زمان حذف", "کاربر", "نوع", "سند", "دلیل حذف"]); l1.addWidget(self.t1)
        self.detail = QTextEdit(); self.detail.setReadOnly(True); self.detail.setMaximumHeight(150); l1.addWidget(self.detail)
        b = QPushButton("↩ بازگردانی مورد انتخاب‌شده"); b.clicked.connect(self.do_restore); l1.addWidget(b)
        tabs.addTab(w1, "حذف‌شده‌ها")
        w2 = QWidget(); l2 = QVBoxLayout(w2)
        l2.addWidget(QLabel("تاریخچه‌ی کامل ثبت، ویرایش، حذف، بازگردانی و چاپ فیش (غیرقابل ویرایش و حذف)."))
        self.t2 = _table(["زمان", "کاربر", "عملیات", "نوع", "سند", "دلیل"]); l2.addWidget(self.t2)
        tabs.addTab(w2, "تاریخچه‌ی کامل")
        self.t1.itemSelectionChanged.connect(self.show_detail)
        self.load()

    def load(self):
        with self.db() as con:
            self.rows = safety.list_deleted(con); hist = safety.list_history(con, 1500)
        self.t1.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            for c, v in enumerate((self.fmt(r["at"]), r["user"], safety.KIND_FA.get(r["kind"], r["kind"]), r["label"], r["reason"])):
                self.t1.setItem(i, c, QTableWidgetItem(str(v)))
        self.t2.setRowCount(len(hist))
        for i, r in enumerate(hist):
            for c, v in enumerate((self.fmt(r["at"]), r["user"], safety.ACTION_FA.get(r["action"], r["action"]),
                                   safety.KIND_FA.get(r["kind"], r["kind"]), r["label"], r["reason"])):
                self.t2.setItem(i, c, QTableWidgetItem(str(v)))
        self.t1.resizeColumnsToContents(); self.t2.resizeColumnsToContents(); self.t1.horizontalHeader().setStretchLastSection(True); self.t2.horizontalHeader().setStretchLastSection(True)
        self.detail.clear()

    def show_detail(self):
        r = self.t1.currentRow()
        if r < 0 or r >= len(self.rows): return
        try: self.detail.setPlainText(json.dumps(json.loads(self.rows[r]["snapshot"]), ensure_ascii=False, indent=1))
        except Exception: self.detail.setPlainText("")

    def do_restore(self):
        r = self.t1.currentRow()
        if r < 0 or r >= len(self.rows):
            return QMessageBox.information(self, "انتخاب", "ابتدا یک مورد را انتخاب کن.")
        if QMessageBox.question(self, "بازگردانی", f"این مورد بازگردانده شود؟\n{self.rows[r]['label']}") != QMessageBox.Yes: return
        try:
            with self.db() as con: safety.restore(con, self.user, self.rows[r]["id"])
        except safety.RestoreError as e:
            return QMessageBox.warning(self, "بازگردانی ممکن نشد", str(e))
        except Exception as e:
            return QMessageBox.warning(self, "خطا", str(e))
        self.changed = True; self.load()
        QMessageBox.information(self, "انجام شد", "مورد بازگردانده شد.")
