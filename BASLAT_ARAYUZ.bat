@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" arayuz.py
) else (
  python arayuz.py
)
pause
