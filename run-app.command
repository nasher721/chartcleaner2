#!/usr/bin/env bash
# Chart Cleaner app launcher (macOS/Linux) — double-clickable in Finder
# (.command) or run from a terminal. Sets up .venv on first run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "First run: setting up Chart Cleaner (installs into .venv, needs internet once)..."
  ./install.sh
fi

exec "$PY" app.py "$@"
