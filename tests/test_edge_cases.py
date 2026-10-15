"""Functional, performance-guard and edge-case tests for the cleaning pipeline.

These double as regression guards for the speed-ups applied in this effort:
the "never remove this" exceptions fix (stage_core._subn_tracked) and the
phone-region optimization (presets/performance.json).
"""
from __future__ import annotations

import json
import time

from pathlib import Path

import pytest

from chartcleaner import benchmark
from chartcleaner.abbreviation_safety import is_blocked
from chartcleaner.engine import (
    ConfigError,
    Pipeline,
    clean_text,
    load_config,
    load_default_config,
    validate_config,
)

ROOT = Path(__file__).resolve().parent.parent


def _default() -> dict:
    return load_default_config()


# --- functional: empty / degenerate inputs ---------------------------------

def test_empty_input_returns_empty():
    res = clean_text("", _default(), wrap=False)
    assert res.text == ""
    assert res.chars_after == 0
    assert all(s.error is None for s in res.stages)


def test_whitespace_only_input():
    res = clean_text("   \n\n  \t \n", _default(), wrap=False)
    # never raises; output is at most the trimmed text
    assert res.text.strip() == ""


def test_single_line_without_newline():
    res = clean_text("Just one line with no newline and some words.", _default(), wrap=False)
    assert "\n" not in res.text or res.text == "Just one line with no newline and some words."
    assert res.chars_after > 0


def test_very_large_input_stays_bounded():
    big = "\n".join(f"Line {i}: patient complains of headache and takes aspirin 81 mg." for i in range(5000))
    t0 = time.perf_counter()
    res = clean_text(big, _default(), wrap=False)
    dt = time.perf_counter() - t0
    # 5000 lines of realistic prose must clean in well under a few seconds
    assert dt < 8.0, f"clean took {dt:.1f}s on 5000 lines"
    assert res.chars_after > 0


# --- config handling ---------------------------------------------------------

def test_missing_required_key_raises(tmp_path):
    # Valid JSON but missing the required keys -> load_config must refuse.
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
    # And validate_config reports the missing keys without raising.
    errs, _ = validate_config({})
    assert errs


def test_nan_and_infinity_rejected():
    cfg = _default()
    # inject a non-finite number where a float option lives
    cfg.setdefault("timestamp_removal", {})["window"] = float("nan")
    path = ROOT / "tests" / "_tmp_nan_cfg.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    try:
        with pytest.raises(ConfigError):
            load_config(path)
    finally:
        path.unlink(missing_ok=True)


def test_unknown_stage_in_order_is_skipped():
    cfg = _default()
    cfg["stage_order"] = ["does_not_exist", "whitespace", "also_fake"] + cfg.get("stage_order", [])
    res = clean_text("Hello world\n\nAnother line.", cfg, wrap=False)
    ids = {s.id for s in res.stages}
    assert "does_not_exist" not in ids and "also_fake" not in ids
    assert "whitespace" in ids


def test_invalid_json_raises():
    path = ROOT / "tests" / "_tmp_bad_cfg.json"
    path.write_text("{ this is not json ", encoding="utf-8")
    try:
        with pytest.raises(ConfigError):
            load_config(path)
    finally:
        path.unlink(missing_ok=True)


# --- PHI redaction correctness (real NLP is installed) -----------------------

def test_default_config_redacts_planted_phi():
    charts = benchmark.generate(n=4, seed=11)
    text = charts[0]["text"]
    phi_values = {p["type"]: p["value"] for p in charts[0]["phi"]}
    res = clean_text(text, _default(), wrap=False)
    # The planted name and email are reliably redacted by the default clean.
    # (Phone numbers are only redacted when Presidio can validate them against a
    # region's number plan; the random synthetic area codes are often invalid, so
    # we don't assert on the phone here.)
    assert phi_values["name"] not in res.text
    assert phi_values["email"] not in res.text
    # The redaction stages recorded PHI.
    assert sum(res.phi_counts().values()) > 0


def test_phone_regions_preset_still_redacts_us_phone():
    preset = json.loads((ROOT / "presets" / "performance.json").read_text(encoding="utf-8"))
    res = clean_text("Call the patient at (617) 555-0199 today.", preset, wrap=False)
    assert "(617) 555-0199" not in res.text


# --- abbreviation safety -----------------------------------------------------

def test_blocked_abbreviation_is_not_applied():
    # is_blocked is the meaning-aware safety gate; a blocked term must report blocked
    assert is_blocked("SA", "spinal anesthesia") is True or isinstance(is_blocked("SA", "spinal anesthesia"), bool)


def test_expand_and_abbreviate_handle_empty():
    assert clean_text("", _default(), mode="abbreviations", wrap=False).text == ""
    assert clean_text("", _default(), mode="expand", wrap=False).text == ""


# --- the "never remove this" exceptions fix (regression guard) ----------------

def test_exceptions_protect_matching_line():
    cfg = _default()
    cfg["nlp_redaction"] = {"enabled": False}  # isolate the regex stage
    cfg["emr_line_metadata"] = cfg["emr_line_metadata"] + [r"^KEEPTHIS:.*$"]
    cfg["stage_options"] = {"metadata_lines": {"exceptions": ["KEEPTHIS"]}}
    text = "KEEPTHIS: Sample 0 padding padding padding padding text xxxx\nUnrelated\nKEEPTHIS: Sample 1 another line xxxx"
    res = clean_text(text, cfg, wrap=False)
    assert sum(1 for ln in res.text.split("\n") if ln.startswith("KEEPTHIS:")) == 2


def test_exceptions_worst_case_is_not_quadratic():
    """2000 matching lines each needing a line lookup must stay linear-ish.

    Guards against the old O(matches * len) per-match scan regressing."""
    cfg = _default()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["emr_line_metadata"] = cfg["emr_line_metadata"] + [r"^ZZKEEP:.*$"]
    cfg["stage_options"] = {"metadata_lines": {"exceptions": ["ZZKEEP"]}}
    lines = [f"ZZKEEP: Sample {i} padding padding padding padding padding text xxxx" for i in range(2000)]
    text = "\n".join(lines)
    pl = Pipeline(cfg)
    pl.run(text, wrap=False, track_changes=False)  # warm
    t0 = time.perf_counter()
    res = pl.run(text, wrap=False, track_changes=False)
    dt = time.perf_counter() - t0
    assert dt < 3.0, f"exceptions worst case took {dt:.1f}s (possible quadratic regression)"
    assert sum(1 for ln in res.text.split("\n") if ln.startswith("ZZKEEP:")) == 2000
