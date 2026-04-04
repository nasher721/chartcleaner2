@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM No admin required: venv and packages live only in this folder.
REM You need Python 3.10+ for your user (python.org: uncheck "install for all users", check "Add to PATH").

set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto :have_venv

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
  py -3 -m venv .venv
  goto :check_venv
)
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
  python -m venv .venv
  goto :check_venv
)

echo ERROR: No Python found. Install Python 3.10+ for your user only from https://www.python.org/downloads/ >&2
echo Use "Customize" and install to a folder you can write to; enable "Add python.exe to PATH". >&2
exit /b 1

:check_venv
if not exist "%VENV_PY%" (
  echo ERROR: Could not create .venv >&2
  exit /b 1
)

:have_venv
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_PY%" -m pip install -r "%CD%\requirements.txt"
if errorlevel 1 exit /b 1

echo.
echo Setup complete. From this folder run:
echo   clean-chart.cmd              (clipboard in -^> cleaned out^)
echo   clean-chart.cmd -h           (file / folder options^)
echo   Clean_Medical_Chart.cmd      (clipboard only, double-click^)
echo.
exit /b 0
