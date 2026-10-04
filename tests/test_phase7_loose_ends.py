"""Phase 7 group 1: validator coverage, batch note types, slow/risky rules,
per-entry abbreviation sections, Quick Action self-test."""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

from chartcleaner import rule_health, rule_preview, stages
from chartcleaner.abbreviations import abbreviate, normalize_settings
from chartcleaner.batch import run_batch
from chartcleaner.config_validator import KNOWN_CONFIG_KEYS
from chartcleaner.engine import Pipeline, load_default_config, validate_config
from chartcleaner.regex_risk import risks

ROOT = Path(__file__).resolve().parent.parent


# --- F4: every config group rejects a value of the wrong type ---------------

@pytest.mark.parametrize("key", sorted(KNOWN_CONFIG_KEYS))
def test_every_known_config_key_is_validated(key):
    cfg = load_default_config()
    cfg[key] = 12345
    errors, _warnings = validate_config(cfg)
    assert any(key in e for e in errors), f"{key}: a number was accepted without an error"


def test_clinical_identifiers_options_are_type_checked():
    cfg = load_default_config()
    cfg["clinical_identifiers"] = {"enabled": "yes", "redact_npi": 1, "replacement": None}
    errors, _ = validate_config(cfg)
    assert any("clinical_identifiers.enabled" in e for e in errors)
    assert any("clinical_identifiers.redact_npi" in e for e in errors)
    assert not any("replacement" in e for e in errors)


# --- batch note types ---------------------------------------------------------

_DISCHARGE = ("Discharge Summary\nHospital Course: uneventful stay.\n"
              "Discharge Medications: aspirin 81 mg daily.\nDisposition: home.\n")
_PROGRESS = "Progress Note\nInterval History: no events.\nSubjective: feels well.\n"


def test_run_batch_uses_the_preset_mapped_to_each_note_type(tmp_path):
    a = tmp_path / "dc.txt"; a.write_text(_DISCHARGE, encoding="utf-8")
    b = tmp_path / "pn.txt"; b.write_text(_PROGRESS, encoding="utf-8")
    loaded = []

    def loader(name):
        loaded.append(name)
        cfg = load_default_config()
        cfg["literal_replacements"] = [["uneventful", "UNEVENTFUL"]]
        return cfg

    results = run_batch([a, b], load_default_config(),
                        note_presets={"discharge_summary": "dc-preset"}, preset_loader=loader)
    assert [r.status for r in results] == ["ok", "ok"]
    assert results[0].note_type == "Discharge summary" and results[0].preset == "dc-preset"
    assert "UNEVENTFUL" in results[0].cleaned
    assert results[1].note_type == "Progress note" and results[1].preset == ""
    assert loaded == ["dc-preset"]   # loaded once, not per file


def test_run_batch_falls_back_when_a_preset_fails_to_load(tmp_path):
    a = tmp_path / "dc.txt"; a.write_text(_DISCHARGE, encoding="utf-8")

    def broken(_name):
        raise FileNotFoundError("gone")

    [res] = run_batch([a], load_default_config(), note_presets={"discharge_summary": "x"},
                      preset_loader=broken)
    assert res.status == "ok" and res.preset == "" and res.note_type == "Discharge summary"


def test_run_batch_without_mapping_skips_detection(tmp_path):
    a = tmp_path / "dc.txt"; a.write_text(_DISCHARGE, encoding="utf-8")
    [res] = run_batch([a], load_default_config())
    assert res.note_type == "" and res.preset == ""


# --- regex risk and slow rules ----------------------------------------------

@pytest.mark.parametrize("pattern", [r"(\w+\s?)+:", r"(a+)+$", r"(.*)*x", r"(\s*\w+)*;",
                                     r".*foo.*bar.*baz"])
def test_risky_patterns_are_flagged(pattern):
    assert risks(pattern)


@pytest.mark.parametrize("pattern", [r"^Printed on .*$", r"(?:\s*,\s*[A-Z]{2})+",
                                     r"(?:\d+\.\s*\n+)+", r"[A-Z]{2,}\s+\d+", r"(?:ab{2})+",
                                     r"^\s*Filed:.*?Date of Service:.*?Status:.*$"])
def test_safe_patterns_are_not_flagged(pattern):
    assert risks(pattern) == []


def test_shipped_configs_have_no_risky_rules():
    import json
    import re
    for path in (ROOT / "chartcleaner" / "default_config.json", ROOT / "config.json"):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        for key in ("emr_line_metadata", "boilerplate"):
            for p in cfg[key]:
                assert risks(p, re.I | re.M) == [], (path.name, p)


def test_validator_warns_about_risky_rules():
    cfg = load_default_config()
    cfg["boilerplate"] = cfg["boilerplate"] + [r"(\w+\s?)+:"]
    errors, warnings = validate_config(cfg)
    assert not errors
    assert any("boilerplate[" in w and "repeat inside a repeat" in w for w in warnings)


def test_slow_rules_are_recorded_and_reported(monkeypatch):
    monkeypatch.setattr(stages, "SLOW_RULE_MS", 0.0)
    cfg = load_default_config()
    cfg["boilerplate"] = [r"needle"]
    result = Pipeline(cfg).run("a needle here\n" + "plain line\n" * 20, fact_check=False)
    stage = next(s for s in result.stages if s.id == "boilerplate")
    rid = stages.rule_id("needle")
    assert rid in stage.details["slow_rules"]
    history = [result.to_history_dict("t"), result.to_history_dict("t")]
    rows = rule_health.report(cfg, history, min_runs=1)
    row = next(r for r in rows if r["pattern"] == "needle")
    assert row["status"] == "slow" and row["max_ms"] >= 0


def test_rule_health_lists_backtracking_risks():
    cfg = load_default_config()
    cfg["boilerplate"] = [r"(\w+\s?)+:"]
    rows = rule_health.report(cfg, [])
    assert rows[0]["risks"]


def test_rule_preview_reports_risk_and_time(monkeypatch):
    monkeypatch.setattr(rule_preview, "SLOW_RULE_MS", -1.0)
    impact = rule_preview.preview(load_default_config(), "boilerplate", r"(\w+\s?)+:",
                                  ["one line\nanother: line\n"])
    assert impact.risks and "Could hang" in impact.headline
    assert impact.slow and "Slow" in impact.headline
    assert impact.to_dict()["risks"] == impact.risks


# --- per-entry abbreviation sections -----------------------------------------

_SECTIONED = ("Assessment and Plan:\nsubarachnoid hemorrhage, on nimodipine.\n\n"
              "History of Present Illness:\nsubarachnoid hemorrhage noted on CT.\n")


def _abbr_cfg(**entry):
    return {"abbreviations": {"custom": [{"term": "subarachnoid hemorrhage", "replacement": "SAH",
                                          "enabled": True, **entry}]}}


def test_custom_entry_limited_to_named_sections():
    out, n, _ = abbreviate(_SECTIONED, _abbr_cfg(sections=["Assessment and Plan"]))
    ap, hpi = out.split("\n\n")
    assert "SAH" in ap and "subarachnoid hemorrhage" in hpi
    assert n >= 1


def test_custom_entry_without_sections_applies_everywhere():
    out, _, _ = abbreviate(_SECTIONED, _abbr_cfg())
    assert "subarachnoid hemorrhage" not in out


def test_custom_entry_sections_without_headers_apply_nowhere():
    out, _, _ = abbreviate("subarachnoid hemorrhage on CT.", _abbr_cfg(sections=["Assessment"]))
    assert out == "subarachnoid hemorrhage on CT."


def test_sections_survive_normalize_and_validation():
    group = normalize_settings(_abbr_cfg(sections=[" Assessment ", ""])["abbreviations"])
    assert group["custom"][0]["sections"] == ["Assessment"]
    cfg = load_default_config()
    cfg["abbreviations"] = _abbr_cfg(sections="Assessment")["abbreviations"]
    errors, _ = validate_config(cfg)
    assert any("abbreviations.custom[0].sections" in e for e in errors)


# --- Quick Action self-test ----------------------------------------------------

def _qa():
    spec = importlib.util.spec_from_file_location(
        "make_quick_actions", ROOT / "integrations" / "macos" / "make_quick_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Done:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_self_test_reports_missing_install(tmp_path):
    rows = _qa().self_test(tmp_path)
    assert len(rows) == 3 and not any(r["ok"] for r in rows)
    assert "not installed" in rows[0]["detail"]


def test_self_test_runs_each_command(tmp_path):
    qa = _qa()
    root = tmp_path / "app"; root.mkdir()
    cli = root / "clean-chart"; cli.write_text("#!/bin/bash\ncat\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    qa.install(tmp_path / "svc", root=root)
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw["input"]))
        return _Done(0, out="cleaned text")

    rows = qa.self_test(tmp_path / "svc", run=fake_run)
    assert all(r["ok"] for r in rows), rows
    assert len(calls) == 3 and calls[0][0][:2] == ["/bin/bash", "-c"]
    assert cli.as_posix() in calls[0][0][2] and "MRN" in calls[0][1]

    rows = qa.self_test(tmp_path / "svc", run=lambda *a, **k: _Done(1, err="No virtual environment"))
    assert not rows[0]["ok"] and "No virtual environment" in rows[0]["detail"]


def test_self_test_flags_moved_folder(tmp_path):
    qa = _qa()
    qa.install(tmp_path / "svc", root=tmp_path / "gone")
    rows = qa.self_test(tmp_path / "svc", run=lambda *a, **k: _Done(0, out="x"))
    assert "missing" in rows[0]["detail"]


@pytest.mark.skipif(os.name == "nt", reason="bash launcher")
def test_self_test_real_command(tmp_path):
    qa = _qa()
    root = tmp_path / "app"; root.mkdir()
    cli = root / "clean-chart"; cli.write_text("#!/bin/bash\ntr a-z A-Z\n")
    cli.chmod(0o755)
    qa.install(tmp_path / "svc", root=root)
    rows = qa.self_test(tmp_path / "svc")
    assert all(r["ok"] for r in rows), rows
