@echo off
rem Depo kokune gec (bu dosya scripts\windows\ altinda).
cd /d "%~dp0..\.."
title Mazot Fis Kontrol - Yeniden Baslat
echo Calisan surum kapatiliyor...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '-m mazot|uygulama\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 3 /nobreak >nul
echo Ekipman fisleri yeni kurala gore yeniden eslesiyor...
".venv\Scripts\python.exe" -m tools.ekipman_yeniden_esle
echo.
echo Guncel surum baslatiliyor...
".venv\Scripts\python.exe" -m mazot
