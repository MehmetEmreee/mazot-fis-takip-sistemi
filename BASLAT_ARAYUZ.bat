@echo off
cd /d "%~dp0"
title Mazot Fis Kontrol
if exist "Mazot Fis Kontrol.exe" (
  "Mazot Fis Kontrol.exe"
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" uygulama.py
) else (
  echo Program dosyalari eksik. Lutfen kurulumu yeniden yapin.
  pause
)
