@echo off
setlocal
cd /d "%~dp0"
title Mazot Fis Kontrol
rem Gelistirici bilgisayari: .venv varsa her zaman guncel kaynak kodu calistir.
rem (Depodaki EXE, git pull sonrasi eski kalabilir.)
if exist ".venv\Scripts\python.exe" (
  call :venv_hazirla
  if errorlevel 1 echo Uyari: paketler guncellenemedi, mevcut ortamla devam ediliyor.
  ".venv\Scripts\python.exe" uygulama.py
  goto :eof
)
if exist "Mazot Fis Kontrol.exe" (
  "Mazot Fis Kontrol.exe"
  goto :eof
)
echo Program dosyalari eksik. Lutfen once KURULUM.bat dosyasini calistirin.
pause
goto :eof

:venv_hazirla
rem .venv yoksa olusturur; requirements.txt degistiyse paketleri kurar.
if not exist ".venv\Scripts\python.exe" (
  echo Python ortami olusturuluyor ^(.venv^)...
  where uv >nul 2>nul
  if not errorlevel 1 (
    uv venv .venv
  ) else (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      py -3 -m venv .venv
    ) else (
      python -c "import sys" >nul 2>nul
      if not errorlevel 1 (
        python -m venv .venv
      ) else (
        echo Python bulunamadi. https://www.python.org/downloads/ adresinden kurun.
        exit /b 1
      )
    )
  )
  if exist ".venv\.requirements.kurulu" del ".venv\.requirements.kurulu"
)
if not exist ".venv\Scripts\python.exe" exit /b 1
if exist ".venv\.requirements.kurulu" (
  fc /b "requirements.txt" ".venv\.requirements.kurulu" >nul 2>nul
  if not errorlevel 1 exit /b 0
)
echo Gerekli Python paketleri kuruluyor...
call :paket_kur -r requirements.txt
if errorlevel 1 exit /b 1
copy /y "requirements.txt" ".venv\.requirements.kurulu" >nul
exit /b 0

:paket_kur
where uv >nul 2>nul
if not errorlevel 1 (
  uv pip install --python ".venv\Scripts\python.exe" %*
  if errorlevel 1 exit /b 1
  exit /b 0
)
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul
".venv\Scripts\python.exe" -m pip install %*
if errorlevel 1 exit /b 1
exit /b 0
