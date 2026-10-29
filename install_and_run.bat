@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".deps_ok" (
  echo Installing required packages - first run only...
  py -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installation failed. Check your internet connection and Python installation.
    pause
    exit /b 1
  )
  echo ok> .deps_ok
)
start "" wscript "%~dp0FactorDesk.vbs"
