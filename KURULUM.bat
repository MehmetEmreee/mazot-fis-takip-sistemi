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
echo Python ortami kontrol ediliyor...
call :venv_hazirla
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
echo Kurulum tamamlandi. Bundan sonra sadece BASLAT_ARAYUZ dosyasini acin.
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
