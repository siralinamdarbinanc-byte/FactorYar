# -*- coding: utf-8 -*-
"""رمزگذاری مقادیر حساس (کلید پیامک، کلید گوگل) با DPAPI ویندوز — بدون کتابخانه‌ی اضافه.

مقدار رمزشده فقط برای همان کاربر ویندوز و روی همان کامپیوتر خوانده می‌شود؛ یعنی فایل دیتابیس یا پشتیبان اگر
دست کسی بیفتد (یا روی کامپیوتر دیگر باز شود) کلیدها در آن به‌صورت متن ساده دیده نمی‌شوند.
اگر DPAPI در دسترس نباشد (مثلاً سیستم غیر ویندوز) مقدار همان‌طور که هست ذخیره می‌شود تا کلیدی از دست نرود.
"""
import sys, base64

PREFIX = "dpapi1:"


def available():
    return sys.platform == "win32"


def _dpapi(data, encrypt):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    b_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    b_out = BLOB()
    if encrypt: ok = crypt32.CryptProtectData(ctypes.byref(b_in), "FactorDesk", None, None, None, 0, ctypes.byref(b_out))
    else: ok = crypt32.CryptUnprotectData(ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out))
    if not ok: raise OSError("DPAPI call failed")
    try: return ctypes.string_at(b_out.pbData, b_out.cbData)
    finally: kernel32.LocalFree(b_out.pbData)


def protect(text):
    """متن را رمز می‌کند؛ اگر ممکن نبود همان متن را برمی‌گرداند (هرگز کلید را از دست نمی‌دهد)."""
    text = "" if text is None else str(text)
    if not text or text.startswith(PREFIX) or not available(): return text
    try: return PREFIX + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")
    except Exception: return text


def unprotect(value):
    """مقدار ذخیره‌شده را به متن اصلی برمی‌گرداند. مقدار رمزشده‌ای که روی این کامپیوتر/کاربر باز نشود → رشته‌ی خالی."""
    value = "" if value is None else str(value)
    if not value.startswith(PREFIX): return value
    if not available(): return ""
    try: return _dpapi(base64.b64decode(value[len(PREFIX):]), False).decode("utf-8")
    except Exception: return ""
