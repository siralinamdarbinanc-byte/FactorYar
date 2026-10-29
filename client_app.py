# -*- coding: utf-8 -*-
"""ورودی «فاکتور یار (کلاینت)»: برنامه‌ی همیشگی، ولی به‌جای فایل دیتابیس به سرور فروشگاه وصل می‌شود."""
import os, sys
from pathlib import Path
os.environ["FACTORDESK_MODE"] = "client"
os.environ.setdefault("FACTORDESK_DATA", str(Path.home() / "FactorDeskClient"))      # پوشه‌ی جدا: دیتابیس قدیمیِ همین کامپیوتر دست نمی‌خورد
import main
if __name__ == "__main__":
    main.main()
