#!/usr/bin/env bash
# Build a standalone macOS app: dist/Chart Cleaner.app
# Requires the .venv from install.sh. Internet needed once for PyInstaller.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "No .venv found — run ./install.sh first." >&2
  exit 1
fi

"$PY" -m pip install --quiet pyinstaller

NICEGUI_DIR="$("$PY" -c 'import nicegui, os.path; print(os.path.dirname(nicegui.__file__))')"

rm -rf build dist "dist/Chart Cleaner.app"

"$PY" -m PyInstaller \
  --noconfirm --name "Chart Cleaner" --windowed \
  --add-data "${NICEGUI_DIR}:nicegui" \
  --collect-all spacy --collect-all en_core_web_sm \
  --collect-all thinc --collect-all srsly --collect-all catalogue \
  --collect-all wasabi --collect-all weasel --collect-all preshed \
  --collect-all murmurhash --collect-all cymem --collect-all blis \
  --collect-all regex --collect-all uvloop \
  --collect-all presidio_analyzer --collect-all presidio_anonymizer \
  --collect-all phonenumbers --collect-all tldextract \
  --collect-all thefuzz --collect-all rapidfuzz \
  --collect-all pyperclip \
  --hidden-import en_core_web_sm \
  --add-data "chartcleaner/default_config.json:chartcleaner" \
  --add-data "custom_rules:custom_rules" \
  --add-data "sample_chart.txt:." \
  app.py

echo ""
echo "Build complete: dist/Chart Cleaner.app"
echo "Copy it anywhere; on first launch it creates config.json, custom_rules/,"
echo "and data/ next to itself. Logs: data/app.log."
