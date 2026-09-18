#!/usr/bin/env bash
# Requires Python 3.10+ (recommended 3.11–3.14) for presidio-analyzer / spaCy wheels.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
chmod +x clean-chart run-app.command 2>/dev/null || true

echo ""
echo "Setup complete. From this folder run:"
echo "  ./run-app.command           # the Chart Cleaner app (opens in your browser)"
echo "  ./clean-chart               # clipboard in -> cleaned out (CLI)"
echo "  ./clean-chart -h            # CLI file / folder options"
echo ""
