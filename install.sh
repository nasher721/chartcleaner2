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

if [[ "${1:-}" == "--services" ]]; then
  if [[ "$(uname)" == "Darwin" ]]; then
    .venv/bin/python integrations/macos/make_quick_actions.py
    .venv/bin/python integrations/macos/make_quick_actions.py --self-test || echo "Quick Action self-test failed — see above."
  else
    echo "--services adds macOS Quick Actions; skipped on this system."
  fi
fi

echo ""
echo "Setup complete. From this folder run:"
echo "  ./run-app.command           # the Chart Cleaner app (opens in your browser)"
echo "  ./clean-chart               # clipboard in -> cleaned out (CLI)"
echo "  ./clean-chart -h            # CLI file / folder options"
echo "  ./install.sh --services     # add Clean / Abbreviate / Expand Selection Quick Actions (macOS)"
echo ""
