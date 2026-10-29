@echo off
chcp 65001 >nul
:: باید «Run as administrator» اجرا شود. فقط کامپیوترهای همین زیرشبکه‌ی فروشگاه اجازه‌ی اتصال به پورت سرور را دارند (نه اینترنت).
net session >nul 2>&1 || (echo لطفاً با راست‌کلیک و Run as administrator اجرا کن. & pause & exit /b 1)
set PORT=8750
netsh advfirewall firewall delete rule name="FactorDesk Server" >nul 2>&1
netsh advfirewall firewall add rule name="FactorDesk Server" dir=in action=allow protocol=TCP localport=%PORT% remoteip=localsubnet profile=private,domain
echo قانون فایروال برای پورت %PORT% (فقط شبکه‌ی داخلی) ساخته شد.
echo اگر شبکه‌ی ویندوز روی Public است، آن را در Settings روی Private بگذار.
pause
