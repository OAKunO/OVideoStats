@echo off
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo Please install Python 3.11 or 3.12 x64 from python.org first.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto :error
.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
if errorlevel 1 goto :error
echo Ready. Run start.bat, or build-windows.bat to create a portable EXE.
echo Requires Microsoft Edge WebView2 Runtime and an installed Chrome or Edge browser.
pause
exit /b 0
:error
echo Setup failed. Read the error above.
pause
exit /b 1
