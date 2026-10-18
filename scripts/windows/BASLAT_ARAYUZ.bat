@echo off
setlocal
rem Depo kokune gec (bu dosya scripts\windows\ altinda).
cd /d "%~dp0..\.."
title Mazot Fis Kontrol
rem Gelistirici bilgisayari: .venv varsa her zaman guncel kaynak kodu calistir.
rem (EXE, git pull sonrasi eski kalabilir.)
if exist ".venv\Scripts\python.exe" (
  call "scripts\windows\_ortam.bat"
  if errorlevel 1 echo Uyari: paketler guncellenemedi, mevcut ortamla devam ediliyor.
  ".venv\Scripts\python.exe" -m mazot
  goto :eof
)
if exist "Mazot Fis Kontrol.exe" (
  "Mazot Fis Kontrol.exe"
  goto :eof
)
echo Program dosyalari eksik. Lutfen once scripts\windows\KURULUM.bat dosyasini calistirin.
pause
