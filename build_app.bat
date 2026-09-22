@echo off
setlocal EnableExtensions
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\build_release.py" build --unsigned-local %*
exit /b %errorlevel%
