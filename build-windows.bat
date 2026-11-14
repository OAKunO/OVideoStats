@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup-windows.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe packaging\build_windows.py --skip-ui-build
if errorlevel 1 (
  echo Build failed. Read the error above.
  pause
  exit /b 1
)
pause
