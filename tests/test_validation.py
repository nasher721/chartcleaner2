"""Boundary validation: a damaged or hostile file must degrade, not crash.

``config.json``, ``prefs.json`` and the preset files are all plain JSON the
user (or a partial write) can corrupt. These tests pin the behaviour at each
boundary: a wrong-typed preference falls back to its default, and a config
carrying values JSON does not even allow (NaN/Infinity) is rejected with a
clear error instead of loading and poisoning arithmetic later.
"""

from __future__ import annotations

import json

import pytest

from chartcleaner import store
from chartcleaner.engine import ConfigError, load_config
from chartcleaner.store_prefs import _coerce_pref


@pytest.fixture()
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    monkeypatch.setattr(store, "PREFS_FILE", path)
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return path


# ---------------------------------------------------------------------------
# preferences: a wrong-typed value degrades to the shipped default
# ---------------------------------------------------------------------------

def test_load_prefs_keeps_valid_values(prefs_file):
    prefs_file.write_text(json.dumps({"dark": False, "retention_days": 30,
                                      "last_preset": "My rules"}), encoding="utf-8")
    prefs = store.load_prefs()
    assert prefs["dark"] is False
    assert prefs["retention_days"] == 30
    assert prefs["last_preset"] == "My rules"


def test_load_prefs_falls_back_on_wrong_types(prefs_file):
    # A string where a number belongs, a string where a bool belongs, and a
    # list where a dict belongs — none may reach the UI.
    prefs_file.write_text(json.dumps({
        "retention_days": "fourteen",
        "dark": "yes",
        "clipboard_watcher": ["not", "a", "dict"],
        "copy_default": 7,
    }), encoding="utf-8")
    prefs = store.load_prefs()
    assert prefs["retention_days"] == store.DEFAULT_PREFS["retention_days"]
    assert prefs["dark"] is store.DEFAULT_PREFS["dark"]
    assert prefs["clipboard_watcher"] == store.DEFAULT_PREFS["clipboard_watcher"]
    assert prefs["copy_default"] == store.DEFAULT_PREFS["copy_default"]


def test_load_prefs_ignores_unknown_keys(prefs_file):
    prefs_file.write_text(json.dumps({"dark": False, "not_a_pref": "x"}), encoding="utf-8")
    prefs = store.load_prefs()
    assert "not_a_pref" not in prefs


def test_load_prefs_survives_a_corrupt_file(prefs_file):
    prefs_file.write_text("{ this is not json", encoding="utf-8")
    prefs = store.load_prefs()
    assert prefs["dark"] == store.DEFAULT_PREFS["dark"]  # factory defaults


def test_coerce_rejects_non_finite_numbers():
    assert _coerce_pref("retention_days", float("nan"), 14) == 14
    assert _coerce_pref("retention_days", float("inf"), 14) == 14
    assert _coerce_pref("retention_days", 7, 14) == 7
    # bool is an int subclass: it must not pass as a number.
    assert _coerce_pref("retention_days", True, 14) == 14


# ---------------------------------------------------------------------------
# config: NaN/Infinity are not valid JSON and must be refused
# ---------------------------------------------------------------------------

def test_load_config_accepts_a_normal_file(tmp_path):
    from chartcleaner.engine import load_default_config

    default = load_default_config()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(default), encoding="utf-8")
    assert load_config(path) == default


def test_load_config_rejects_nan(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"emr_line_metadata": [], "boilerplate": [], '
                    '"epic_phi_patterns": [], "literal_replacements": [], '
                    '"some_option": NaN}', encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(path)


def test_load_config_rejects_infinity(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"emr_line_metadata": [], "boilerplate": [], '
                    '"epic_phi_patterns": [], "literal_replacements": [], '
                    '"some_option": Infinity}', encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(path)


def test_load_config_rejects_a_non_object(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(path)


def test_load_config_reports_missing_required_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"clinical_headers": []}', encoding="utf-8")
    with pytest.raises(ConfigError, match="missing required key"):
        load_config(path)


def test_load_config_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.json")


@pytest.mark.parametrize("regions,errors,warnings", [
    (["US"], 0, 0),
    ([], 0, 0),
    (["US", "ZZ"], 0, 1),
    ("US", 1, 0),
])
def test_phone_regions_are_validated(regions, errors, warnings):
    from chartcleaner.engine import load_default_config, validate_config

    cfg = load_default_config()
    cfg["nlp_redaction"] = {"phone_regions": regions}
    errs, warns = validate_config(cfg)
    assert sum("phone_regions" in e for e in errs) == errors
    assert sum("phone_regions" in w for w in warns) == warnings
