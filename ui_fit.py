# -*- coding: utf-8 -*-
"""سازگاری پنجره‌ها با اندازه‌ی صفحه: هیچ پنجره‌ای بزرگ‌تر از صفحه باز نمی‌شود، همیشه داخل صفحه می‌ماند
و دکمه‌ی بزرگ‌کردن (Maximize) دارد تا هر پنجره‌ای را بشود تمام‌صفحه کرد."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog as _QtDialog, QLayout

SCREEN_W, SCREEN_H = 0.97, 0.94      # حداکثر سهم از ناحیه‌ی قابل‌استفاده‌ی صفحه (بدون نوار وظیفه)


def avail_geometry(w):
    p = w.parentWidget()
    scr = None
    try: scr = (p.window().screen() if p is not None else None) or w.screen()
    except Exception: pass
    scr = scr or QGuiApplication.primaryScreen()
    return scr.availableGeometry()


def fit_window(w):
    """قبل از نمایش: اندازه و حداقل اندازه را به صفحه محدود می‌کند."""
    if w.isMaximized() or w.isFullScreen(): return
    ag = avail_geometry(w); maxw, maxh = int(ag.width() * SCREEN_W), int(ag.height() * SCREEN_H)
    lay = w.layout()
    if lay is not None:
        need = lay.totalMinimumSize()
        if need.width() > maxw or need.height() > maxh: lay.setSizeConstraint(QLayout.SetNoConstraint)
    w.setMinimumSize(min(w.minimumWidth(), maxw), min(w.minimumHeight(), maxh))
    eff = w.size() if w.testAttribute(Qt.WA_Resized) else w.sizeHint()
    nw, nh = min(eff.width(), maxw), min(eff.height(), maxh)
    if (nw, nh) != (eff.width(), eff.height()): w.resize(nw, nh)


def keep_on_screen(w):
    """بعد از نمایش (وقتی قاب و نوار عنوان معلوم شد): پنجره را کاملاً داخل صفحه نگه می‌دارد."""
    try:
        if not w.isVisible() or w.isMaximized() or w.isFullScreen(): return
        ag = avail_geometry(w); fg = w.frameGeometry()
        dw, dh = max(0, fg.width() - ag.width()), max(0, fg.height() - ag.height())
        if dw or dh:
            w.resize(max(200, w.width() - dw), max(150, w.height() - dh)); fg = w.frameGeometry()
        x = min(max(fg.x(), ag.x()), ag.x() + ag.width() - fg.width())
        y = min(max(fg.y(), ag.y()), ag.y() + ag.height() - fg.height())
        if (x, y) != (fg.x(), fg.y()): w.move(x, y)
    except RuntimeError:
        pass   # پنجره بسته شده


class QDialog(_QtDialog):
    """جایگزین QDialog: دکمه‌ی بزرگ‌کردن + محدودشدن به اندازه‌ی صفحه (همه‌ی دیالوگ‌های برنامه از این ارث می‌برند)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)

    def showEvent(self, e):
        if not getattr(self, "_fit_done", False):
            self._fit_done = True; fit_window(self)
        super().showEvent(e)
        QTimer.singleShot(0, lambda: keep_on_screen(self))
