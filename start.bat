@echo off
setlocal
title Video Transcriber
cd /d "%~dp0"

rem ---- Find Python ---------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo Python 3.10+ is required. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

rem ---- First run: create the virtual environment and install dependencies ---
if not exist ".venv\Scripts\python.exe" (
  echo Setting up for the first time. This takes a few minutes...
  %PY% -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
  where nvidia-smi >nul 2>&1 && (
    echo NVIDIA GPU detected, installing GPU acceleration libraries...
    ".venv\Scripts\python.exe" -m pip install -r requirements-gpu.txt
  )
  echo.
  echo Setup complete.
)

".venv\Scripts\python.exe" -m transcriber %*
if errorlevel 1 pause
exit /b

:fail
echo.
echo Setup failed. See the messages above.
rmdir /s /q .venv 2>nul
pause
exit /b 1
