@echo off
setlocal
rem Depo kokune gec (bu dosya scripts\windows\ altinda).
cd /d "%~dp0..\.."
title Mazot Fis Kontrol - EXE Olustur
echo ==============================================
echo   MAZOT FIS KONTROL - EXE YENIDEN OLUSTURMA
echo ==============================================
echo Not: Calisan program varsa once kapatin (EXE uzerine yazilacak).
echo.
echo Python ortami ve PyInstaller hazirlaniyor...
call "scripts\windows\_ortam.bat" "pyinstaller>=6.0"
if errorlevel 1 (
  echo Python ortami veya PyInstaller hazirlanamadi.
  pause
  exit /b 1
)
echo.
echo EXE olusturuluyor, bu birkac dakika surebilir...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath dist --workpath build "packaging\mazot_fis_kontrol.spec"
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
echo Tamam: depo kokundeki "Mazot Fis Kontrol.exe" guncellendi.
pause
exit /b 0
