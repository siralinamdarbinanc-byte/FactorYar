@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements.txt pyinstaller
set HID=--hidden-import assistant_ai --hidden-import sms_panel --hidden-import ui_fit --hidden-import kardex --hidden-import auth --hidden-import secure --hidden-import main --hidden-import barcode_print --hidden-import purchases --hidden-import purchase_ui --hidden-import barcode_codectenance --hidden-import safety --hidden-import recycle_ui --hidden-import returns --hidden-import chq_acct --hidden-import barcodes --hidden-import netdb --hidden-import net_ui --hidden-import main --hidden-import barcode_print --hidden-import barcode_codec
echo --- ساخت برنامه‌ی سرور ---
py -m PyInstaller --noconfirm --clean --windowed --name "FactorDeskServer" %HID% server_app.py
echo --- ساخت برنامه‌ی کلاینت ---
py -m PyInstaller --noconfirm --clean --windowed --name "FactorDeskClient" %HID% client_app.py
echo.
echo خروجی سرور:   dist\FactorDeskServer\FactorDeskServer.exe
echo خروجی کلاینت: dist\FactorDeskClient\FactorDeskClient.exe
pause
