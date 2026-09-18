@echo off
REM Build a standalone Windows app: dist\ChartCleaner\ChartCleaner.exe
REM Requires the .venv from install.bat. Internet needed once for PyInstaller.
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo No .venv found - run install.bat first.
  exit /b 1
)

"%VENV_PY%" -m pip install --quiet pyinstaller

for /f "delims=" %%i in ('"%VENV_PY%" -c "import nicegui, os.path; print(os.path.dirname(nicegui.__file__))"') do set "NICEGUI_DIR=%%i"

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

"%VENV_PY%" -m PyInstaller ^
  --noconfirm --name ChartCleaner --windowed ^
  --add-data "%NICEGUI_DIR%;nicegui" ^
  --collect-all spacy --collect-all en_core_web_sm ^
  --collect-all thinc --collect-all srsly --collect-all catalogue ^
  --collect-all wasabi --collect-all weasel --collect-all preshed ^
  --collect-all murmurhash --collect-all cymem --collect-all blis ^
  --collect-all regex --collect-all uvloop ^
  --collect-all presidio_analyzer --collect-all presidio_anonymizer ^
  --collect-all phonenumbers --collect-all tldextract ^
  --collect-all thefuzz --collect-all rapidfuzz ^
  --collect-all pyperclip ^
  --hidden-import en_core_web_sm ^
  --add-data "chartcleaner/default_config.json;chartcleaner" ^
  --add-data "custom_rules;custom_rules" ^
  --add-data "sample_chart.txt;." ^
  app.py

echo.
echo Build complete: dist\ChartCleaner\ChartCleaner.exe
echo Copy the whole ChartCleaner folder anywhere; on first launch it creates
echo config.json, custom_rules\ and data\ next to the exe. Logs: data\app.log
