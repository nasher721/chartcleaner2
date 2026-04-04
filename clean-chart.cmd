@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo No virtual environment found. Run once from this folder: >&2
  echo   install.bat                              (no admin / no PowerShell^) >&2
  echo   powershell -ExecutionPolicy Bypass -File .\install.ps1 >&2
  exit /b 1
)
"%PY%" "%ROOT%medical_cleaner.py" %*
