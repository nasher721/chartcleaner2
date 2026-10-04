"""Phase 7 group 7: Doctor checks and fixes, update signing, lazy startup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from chartcleaner import doctor, store
from chartcleaner.engine import load_config, load_default_config, save_config, validate_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_config(load_default_config(), path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "data")
    return path


def _by_id(checks):
    return {c.id: c for c in checks}


def test_checks_cover_everything(cfg_path):
    checks = doctor.run_checks(ai=False)
    ids = set(_by_id(checks))
    assert {"python", "venv", "spacy_model", "config", "encryption", "data_dir", "api_token",
            "update_signing"} <= ids
    assert any(i.startswith("pkg:") for i in ids) and any(i.startswith("launcher:") for i in ids)
    assert all(c.status in ("ok", "warn", "fail", "info") for c in checks)
    assert _by_id(checks)["config"].status == "ok"
    assert "check" in doctor.summary(checks)


def test_broken_config_offers_restore(cfg_path):
    cfg_path.write_text("{not json", encoding="utf-8")
    check = _by_id(doctor.run_checks(ai=False))["config"]
    assert check.status == "fail" and check.fix == "restore_config"
    ok, msg = doctor.apply_fix("restore_config")
    assert not ok and "No config backup" in msg


def test_api_token_fix_and_permissions(cfg_path):
    check = _by_id(doctor.run_checks(ai=False))["api_token"]
    assert check.status == "info" and check.fix == "create_api_token"
    assert doctor.apply_fix("create_api_token")[0]
    from chartcleaner.api import token_path
    if os.name != "nt":
        os.chmod(token_path(), 0o644)
        check = _by_id(doctor.run_checks(ai=False))["api_token"]
        assert check.status == "warn" and check.fix == "fix_token_permissions"
        assert doctor.apply_fix("fix_token_permissions")[0]
    assert _by_id(doctor.run_checks(ai=False))["api_token"].status == "ok"


def test_missing_spacy_model_and_unknown_fix(cfg_path, monkeypatch):
    real = doctor.find_spec
    monkeypatch.setattr(doctor, "find_spec", lambda name: None if name == "en_core_web_sm" else real(name))
    check = _by_id(doctor.run_checks(ai=False))["spacy_model"]
    assert check.status == "fail" and check.fix == "install_spacy_model"
    assert doctor.apply_fix("nope") == (False, "No automatic fix for 'nope'")


def test_ollama_states(cfg_path, monkeypatch):
    from chartcleaner import local_llm
    monkeypatch.setattr(local_llm, "health", lambda url: {
        "loopback": True, "reachable": True, "version": "0.12", "models": [], "recommended": "llama3.1",
        "has_recommended": False, "error": "No models pulled yet"})
    check = _by_id(doctor.run_checks())["ollama"]
    assert check.status == "warn" and check.fix == "pull_model"


def test_update_signing_is_reported(cfg_path, monkeypatch):
    from chartcleaner import release_identity, update
    monkeypatch.setattr(update, "current_platform", lambda: "macos-arm64")
    monkeypatch.setattr(release_identity, "MACOS_PUBLISHER", "")
    assert _by_id(doctor.run_checks(ai=False))["update_signing"].status == "info"
    monkeypatch.setattr(release_identity, "MACOS_PUBLISHER", "Developer ID Application: X")
    check = _by_id(doctor.run_checks(ai=False))["update_signing"]
    assert check.status == "ok" and "Developer ID" in check.detail


def test_staging_refuses_updates_without_a_publisher(tmp_path, monkeypatch):
    """Self-update is never installed unless a signing identity is configured."""
    import hashlib
    import io
    import zipfile

    from chartcleaner import update
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("Chart Cleaner.exe")
        info.external_attr = 0o100755 << 16
        zf.writestr(info, b"exe")
    data = buf.getvalue()
    asset = update.ReleaseAsset("https://github.com/nasher721/chartcleaner2/releases/download/v9/app.zip",
                                hashlib.sha256(data).hexdigest(), len(data))

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def geturl(self):
            return asset.url

    client = update.UpdateClient("2.0.0", "https://github.com/x/y/releases/latest/download/m.json",
                                 transport=lambda *_: Resp(data), trusted_publisher_identity="",
                                 platform_name="windows-x64")
    with pytest.raises(update.UpdateError, match="missing_trusted_publisher"):
        client.stage(asset, tmp_path / "stage", rollback_size=10, free_space=lambda _p: 10**12)


def test_cli_doctor_exit_code(monkeypatch, capsys):
    import medical_cleaner
    monkeypatch.setattr(doctor, "run_checks", lambda **kw: [
        doctor.Check("x", "Thing", "fail", "broken", "reinstall", "Reinstall packages")])
    monkeypatch.setattr(sys, "argv", ["clean-chart", "--doctor"])
    with pytest.raises(SystemExit) as done:
        medical_cleaner.main()
    out = capsys.readouterr().out
    assert done.value.code == 1 and "✕ Thing: broken" in out and "Reinstall packages" in out


# --- startup ------------------------------------------------------------------

def test_engine_import_does_not_load_nlp_or_build_the_abbreviation_matcher():
    code = ("import sys, chartcleaner.engine, chartcleaner.abbreviations as a;"
            "print('presidio_analyzer' in sys.modules, 'spacy' in sys.modules,"
            " a._matcher.cache_info().currsize, 'urllib.request' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                         check=True).stdout.split()
    assert out == ["False", "False", "0", "False"]


def test_source_row_count_still_importable():
    from chartcleaner.abbreviations import SOURCE_ROW_COUNT
    assert SOURCE_ROW_COUNT > 1000


def test_shipped_configs_validate_without_warnings():
    assert validate_config(load_default_config()) == ([], [])
    assert validate_config(load_config(ROOT / "config.json"))[0] == []
    assert validate_config(load_config(ROOT / "config.json"))[1] == []
