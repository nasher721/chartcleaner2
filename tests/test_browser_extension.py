"""Browser extension: loopback-only permissions, and its JS tests (when Node is available)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parent.parent / "integrations" / "browser-extension"


def test_manifest_only_reaches_this_computer():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["host_permissions"] == ["http://127.0.0.1/*", "http://localhost/*"]
    assert "content_scripts" not in manifest  # nothing runs on Epic pages until you right-click
    assert set(manifest["permissions"]) == {"contextMenus", "storage", "scripting", "activeTab"}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_request_helpers_with_node():
    out = subprocess.run(["node", "--test", "request.test.mjs"], cwd=EXT, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
