@echo off
cd /d "%~dp0"
title Mazot Fis Kontrol - Yeniden Baslat
echo Calisan surum kapatiliyor...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uygulama.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 3 /nobreak >nul
echo Ekipman fisleri yeni kurala gore yeniden eslesiyor...
".venv\Scripts\python.exe" ekipman_yeniden_esle.py
echo.
echo Guncel surum baslatiliyor...
".venv\Scripts\python.exe" uygulama.py
