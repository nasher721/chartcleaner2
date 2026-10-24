"""Static supply-chain guards that do not require network access."""

from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
MODEL_URL = ("https://github.com/explosion/spacy-models/releases/download/"
             "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl")
MODEL_SHA256 = "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85"


def test_every_github_action_is_pinned_to_a_full_commit():
    uses = []
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        uses.extend((path.name, ref) for ref in re.findall(r"uses:\s*([^\s#]+)", path.read_text()))
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", ref) for _path, ref in uses), uses


def test_ci_and_release_use_hash_locked_dependencies():
    for name in ("requirements.lock", "requirements-dev.lock"):
        text = (ROOT / name).read_text()
        assert "--hash=sha256:" in text
        assert f"en-core-web-sm @ {MODEL_URL}" in text
        assert f"--hash=sha256:{MODEL_SHA256}" in text
        assert "file://" not in text
        assert "/mnt/agents/" not in text
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text()
        for line in text.splitlines():
            if "pip install" in line and "requirements" in line:
                assert ".lock" in line and "--require-hashes" in line, (path.name, line)


def test_dependabot_covers_python_and_actions():
    config = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "package-ecosystem: pip" in config
    assert "package-ecosystem: github-actions" in config


def test_release_workflow_publishes_platform_sboms():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "generate_sbom.py" in text
    assert "sbom-${{ matrix.platform }}.cdx.json" in text
