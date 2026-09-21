@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
title PRIMORA KIT

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Python paoa jayni. Age Python install korun: https://www.python.org/downloads/
  echo  Install korar shomoy "Add Python to PATH" tick dite bhulben na.
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

echo.
echo  [1/2] Dorkari package install hocche (prothombar ektu shomoy lagbe)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo  Package install hoy nai. Internet connection check korun.
  pause
  exit /b 1
)

echo  [2/2] Website chalu hocche...
python app.py
echo.
pause
