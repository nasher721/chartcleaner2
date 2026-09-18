@echo off
REM Chart Cleaner app launcher (Windows) — double-clickable. Sets up .venv on first run.
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto :run

echo First run: setting up Chart Cleaner (installs into .venv, needs internet once)...
call install.bat
if errorlevel 1 (
  echo.
  echo Setup failed. See messages above.
  pause
  exit /b 1
)

:run
"%VENV_PY%" app.py %*
if errorlevel 1 pause
exit /b 0
