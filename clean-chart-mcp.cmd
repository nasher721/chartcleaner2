@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo No virtual environment found. Run install.bat in this folder first. >&2
  exit /b 1
)
cd /d "%ROOT%"
"%PY%" -m chartcleaner.mcp_server
