@echo off
setlocal
rem Depo kokune gec (bu dosya scripts\windows\ altinda).
cd /d "%~dp0..\.."
title Mazot Fis Kontrol - Ilk Kurulum
set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

echo ==============================================
echo   MAZOT FIS KONTROL - ILK KURULUM
echo ==============================================
echo.
if not exist "%OLLAMA%" (
  echo Ollama bulunamadi.
  echo https://ollama.com/download adresinden Ollama'yi kurun,
  echo sonra bu dosyayi yeniden calistirin.
  pause
  exit /b 1
)

echo OCR modeli kontrol ediliyor. Ilk kurulumda indirme uzun surebilir...
"%OLLAMA%" pull qwen2.5vl:7b
if errorlevel 1 (
  echo.
  echo Model indirilemedi. Internet baglantisini kontrol edip tekrar deneyin.
  pause
  exit /b 1
)

echo.
echo Python ortami kontrol ediliyor...
call "scripts\windows\_ortam.bat"
if errorlevel 1 (
  echo Python ortami kurulamadi.
  if exist "Mazot Fis Kontrol.exe" (
    echo Sorun degil: program "Mazot Fis Kontrol.exe" ile calisacak.
  ) else (
    echo Python kurup bu dosyayi yeniden calistirin.
    pause
    exit /b 1
  )
)

echo.
echo Kurulum tamamlandi. Bundan sonra sadece scripts\windows\BASLAT_ARAYUZ.bat dosyasini acin.
pause
exit /b 0
