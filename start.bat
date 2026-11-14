@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup-windows.bat first.
  pause
  exit /b 1
)
if not exist "dist\index.html" (
  echo Missing UI build. Run npm ci and npm run build inside ui first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe desktop_app.py
if errorlevel 1 pause
