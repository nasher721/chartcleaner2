@echo off
REM Clipboard-only; no PowerShell. Double-click after running install.bat once.
setlocal
cd /d "%~dp0"
call "%~dp0clean-chart.cmd"
if errorlevel 1 pause
