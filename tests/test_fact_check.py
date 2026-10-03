"""The "nothing clinical lost" check (chartcleaner/fact_check.py)."""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path

import pytest

from chartcleaner import service
from chartcleaner.engine import clean_text, load_config
from chartcleaner.fact_check import (
    FactTracker,
    check,
    extract_facts,
    fact_counts,
    stage_category,
)
from app_pages import common

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    c = load_config(ROOT / "config.json")
    c.setdefault("nlp_redaction", {})["enabled"] = False
    return c


def displays(text: str) -> list[str]:
    return [f.display for f in extract_facts(text)]


def test_extracts_units_labels_bp_drugs_and_keywords():
    found = displays("Sodium 141 mmol/L, K: 3.9. BP 138/76. Nimodipine 60 mg PO q4h. "
                     "GCS 14, EVD at 10 cm H2O. NKDA. Code status: DNR/DNI.")
    for want in ("Sodium 141", "K 3.9", "BP 138/76", "Nimodipine", "60 mg", "GCS 14",
                 "10 cm H2O", "NKDA", "DNR", "DNI"):
        assert want in found, want


def test_dates_times_tokens_and_plain_numbers_are_not_facts():
    assert displays("Seen 10/03/2026 at 06:00 and 2026-10-02. Version 2 of 3. [[T4]] Bed 4.") == []


def test_thousands_and_trailing_zeros_compare_by_value():
    assert fact_counts("WBC 1,200 and 0.50 mg") == fact_counts("WBC 1200 and 0.5 mg")


def test_reworded_value_is_kept():
    report = check("Sodium 141 mmol/L", "BMP: Na 141")
    assert report.status == "ok" and report.losses == []


def test_loss_without_stage_info_is_unexpected():
    report = check("K 3.1, heparin 5000 units", "heparin")
    assert report.status == "alert"
    assert {x.display for x in report.losses} == {"K 3.1", "5000 units"}
    assert report.losses[0].lines == ["K 3.1, heparin 5000 units"]


def test_tracker_attributes_loss_to_stage():
    t = FactTracker("Plan: K 3.1, replete.\nVersion 2 of 3")
    t.after_stage("whitespace", "Whitespace cleanup", "Plan: K 3.1, replete.\nVersion 2 of 3")
    t.after_stage("boilerplate", "Boilerplate blocks", "Plan: replete.")
    report = t.report("Plan: replete.")
    assert [(x.display, x.stage_id, x.category) for x in report.losses] == [
        ("K 3.1", "boilerplate", "rule")]
    assert report.status == "review"


def test_categories():
    assert stage_category("bullets") == "unexpected"
    assert stage_category("boilerplate") == "rule"
    assert stage_category("custom:my_rule") == "rule"
    assert stage_category("sections") == "by_design"


def test_dedup_loss_ignored_while_value_still_present():
    drops = [("duplicate_notes", "Duplicate note folding", Counter({"n:141": 1}))]
    assert check("Na 141\nNa 141", "Na 141", drops).losses == []


def test_abbreviating_a_drug_is_not_a_loss():
    drops = [("medical_abbreviations", "Medical abbreviations", Counter({"drug:acetaminophen": 1}))]
    assert check("acetaminophen 650 mg", "APAP 650 mg", drops).losses == []


def test_sample_chart_has_no_losses(cfg):
    result = clean_text((ROOT / "sample_chart.txt").read_text(), cfg)
    assert result.fact_check is not None
    assert result.fact_check.status == "ok", result.fact_check.to_dict()
    assert result.fact_check.total > 20


def test_boilerplate_rule_eating_a_lab_is_flagged(cfg):
    cfg["boilerplate"] = list(cfg["boilerplate"]) + [r"^Labs:.*$"]
    result = clean_text("Assessment\nLabs: K 2.9 critical\nPlan: replete", cfg)
    report = result.fact_check
    assert report.status == "review"
    assert report.losses[0].stage_id == "boilerplate"
    assert report.losses[0].display == "K 2.9"


def test_history_keeps_counts_only(cfg):
    cfg["boilerplate"] = list(cfg["boilerplate"]) + [r"^Labs:.*$"]
    hist = clean_text("Labs: K 2.9 critical", cfg).to_history_dict("test")
    assert hist["fact_check"]["lost_rule"] == 1
    assert "K 2.9" not in str(hist)


def test_can_be_disabled(cfg):
    off = copy.deepcopy(cfg)
    off["fact_check"] = {"enabled": False}
    assert clean_text("K 3.1", off).fact_check is None
    assert clean_text("K 3.1", cfg, mode="abbreviations").fact_check is None


def test_validator_knows_the_group(cfg):
    from chartcleaner.engine import validate_config
    cfg["fact_check"] = {"enabled": "yes"}
    errors, _ = validate_config(cfg)
    assert any("fact_check.enabled" in e for e in errors)


def test_service_payload_includes_report(cfg):
    out = service.clean("Na 141", config=cfg, record=False)
    assert out["fact_check"]["status"] == "ok"
    assert "headline" in out["fact_check"]


async def test_clean_page_shows_fact_check(user, monkeypatch, tmp_path):

    from chartcleaner import store
    from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
    from chartcleaner.engine import load_default_config, save_config

    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    cfg["emr_line_metadata"] = [r"^Printed by .*$"]
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(store, "append_run", lambda _r: None)
    monkeypatch.setattr(store, "load_prefs", lambda: dict(store.DEFAULT_PREFS, auto_clean=False))
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    CLEAN_STATE.update(input="Printed by Dr. Lee: K 6.1 called\nPlan: recheck\n", mode="clean",
                       result=None, result_text="", audit=None)
    try:
        await user.open("/")
        user.find(marker="run-clean").click()
        await user.should_see("1 clinical value(s) removed by rules — review", retries=50)
        await user.should_see("Removed by a removal rule")
    finally:
        CLEAN_STATE.clear()
        CLEAN_STATE.update(before)
        AUTO_LAST.clear()
        AUTO_LAST.update(auto_before)
