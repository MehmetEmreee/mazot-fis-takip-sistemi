@echo off
setlocal
cd /d "%~dp0"
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
echo Kurulum tamamlandi. Bundan sonra sadece BASLAT_ARAYUZ dosyasini acin.
pause
