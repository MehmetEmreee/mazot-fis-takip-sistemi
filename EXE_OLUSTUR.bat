@echo off
setlocal
cd /d "%~dp0"
title Mazot Fis Kontrol - EXE Olustur
echo ==============================================
echo   MAZOT FIS KONTROL - EXE YENIDEN OLUSTURMA
echo ==============================================
echo Not: Calisan program varsa once kapatin (EXE uzerine yazilacak).
echo.
call :venv_hazirla
if errorlevel 1 (
  echo Python ortami hazirlanamadi.
  pause
  exit /b 1
)
echo PyInstaller kuruluyor...
call :paket_kur "pyinstaller>=6.0"
if errorlevel 1 (
  echo PyInstaller kurulamadi.
  pause
  exit /b 1
)
echo.
echo EXE olusturuluyor, bu birkac dakika surebilir...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean "Mazot Fis Kontrol.spec"
if errorlevel 1 (
  echo EXE olusturulamadi. Yukaridaki hata mesajina bakin.
  pause
  exit /b 1
)
copy /y "dist\Mazot Fis Kontrol.exe" "Mazot Fis Kontrol.exe" >nul
if errorlevel 1 (
  echo Yeni EXE kopyalanamadi. Program acik olabilir; kapatip tekrar deneyin.
  pause
  exit /b 1
)
echo.
echo Tamam: "Mazot Fis Kontrol.exe" guncellendi.
pause
exit /b 0

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
