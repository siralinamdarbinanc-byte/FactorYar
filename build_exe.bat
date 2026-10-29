@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller --noconfirm --clean --windowed --name "FactorYar" --hidden-import assistant_ai --hidden-import sms_panel --hidden-import ui_fit --hidden-import kardex --hidden-import auth --hidden-import secure --hidden-import main --hidden-import barcode_print --hidden-import purchases --hidden-import purchase_ui --hidden-import barcode_codectenance main.py
echo.
echo EXE output: dist\FactorYar\FactorYar.exe
pause
