"""Tests for the 'better text cleaning' improvements.

Covers: section-scoped removal, table-safe whitespace, near-duplicate line folding.
Each stage runner is exercised in isolation with a :class:`CleanContext` so the
tests stay independent of the rest of the pipeline ordering.
"""

from __future__ import annotations

import pytest

from chartcleaner.engine import CleanContext, Pipeline, load_default_config
from chartcleaner.stages import run_fuzzy_line_dedup, run_learned, run_regex_pairs, rule_id
from chartcleaner.rule_health import report as health_report
from app_pages.common import learned_pattern, LEARN_REMOVE_BLOCK


def _ctx(cfg: dict) -> CleanContext:
    return CleanContext(cfg)


# ---------------------------------------------------------------------------
# Fuzzy line dedup
# ---------------------------------------------------------------------------

def test_fuzzy_line_dedup_disabled_by_default():
    cfg = {}
    text = "Reviewed by Dr. Smith\nReviewed by Dr. Smith\nReviewed by Dr. Smithe\n"
    out, n, details = run_fuzzy_line_dedup(text, cfg, _ctx(cfg))
    assert n == 0
    assert out == text


def test_fuzzy_line_dedup_folds_near_identical_lines():
    cfg = {"fuzzy_line_dedup": {"enabled": True, "threshold": 90, "min_chars": 5}}
    text = (
        "Reviewed by Dr. Smith on 03/14\n"
        "Reviewed by Dr. Smith on 03/15\n"   # differ by one char -> dropped
        "Plan: continue current management\n"
        "Reviewed by Dr. Smith on 03/14\n"     # within window -> dropped
    )
    out, n, details = run_fuzzy_line_dedup(text, cfg, _ctx(cfg))
    assert n == 2
    assert "Plan: continue current management" in out
    assert out.count("Reviewed by Dr. Smith on 03/14") == 1


def test_fuzzy_line_dedup_keeps_distinct_short_lines():
    cfg = {"fuzzy_line_dedup": {"enabled": True, "threshold": 90, "min_chars": 8}}
    text = "alpha line\nbeta line\ngamma line\n"
    out, n, _ = run_fuzzy_line_dedup(text, cfg, _ctx(cfg))
    assert n == 0
    assert out == text


def test_fuzzy_line_dedup_window_bounds_cost():
    cfg = {"fuzzy_line_dedup": {"enabled": True, "threshold": 90, "min_chars": 4, "window": 2}}
    # Two identical lines separated by 3 distinct lines exceed the window of 2,
    # so the second copy survives.
    text = (
        "stamp A\nkeep one\nkeep two\nkeep three\nstamp A\n"
    )
    out, n, _ = run_fuzzy_line_dedup(text, cfg, _ctx(cfg))
    assert out.count("stamp A") == 2
    assert n == 0


# ---------------------------------------------------------------------------
# Table-safe whitespace
# ---------------------------------------------------------------------------

def test_preserve_tables_keeps_aligned_table():
    cfg = {
        "literal_replacements": [[" {2,}", " "]],
        "stage_options": {"literal_replacements": {"preserve_tables": True}},
    }
    # A fixed-width table row (3+ runs of 2+ spaces) must keep its spacing.
    table = "Na   138   K   4.1   Cl   102\n"
    plain = "Too  many   spaces here.\n"
    text = table + plain
    out, n, _ = run_regex_pairs(text, cfg, _ctx(cfg), "literal_replacements", sid="literal_replacements")
    assert "Na   138   K   4.1   Cl   102" in out
    assert "Too many spaces here." in out


def test_preserve_tables_off_still_collapses():
    cfg = {"literal_replacements": [[" {2,}", " "]]}
    table = "Na   138   K   4.1   Cl   102\n"
    out, n, _ = run_regex_pairs(table, cfg, _ctx(cfg), "literal_replacements", sid="literal_replacements")
    assert "Na 138 K 4.1 Cl 102" in out


def test_preserve_tables_keeps_pipe_and_tab_tables():
    cfg = {
        "literal_replacements": [[" {2,}", " "]],
        "stage_options": {"literal_replacements": {"preserve_tables": True}},
    }
    pipe = "col1 | col2 | col3\n"
    tab = "col1\tcol2\tcol3\n"
    out, _, _ = run_regex_pairs(pipe + tab, cfg, _ctx(cfg), "literal_replacements", sid="literal_replacements")
    assert "col1 | col2 | col3" in out
    assert "col1\tcol2\tcol3" in out


# ---------------------------------------------------------------------------
# Section-scoped removal
# ---------------------------------------------------------------------------

_LABS_SCOPE = {
    "learned_rules": [["No results found\\.", ""]],
    "stage_options": {"learned_rules": {"scope": {"mode": "only", "sections": ["labs"]}}},
}


def test_scope_only_removes_inside_section():
    cfg = dict(_LABS_SCOPE)
    text = (
        "No results found.\n"
        "Labs:\n"
        "No results found.\n"
        "Sodium 138.\n"
    )
    out, n, _ = run_learned(text, cfg, _ctx(cfg))
    # The occurrence outside the Labs section survives; the one inside is gone.
    assert out.count("No results found.") == 1
    assert n == 1


def test_scope_except_removes_outside_section():
    cfg = {
        "learned_rules": [["No results found\\.", ""]],
        "stage_options": {"learned_rules": {"scope": {"mode": "except", "sections": ["labs"]}}},
    }
    text = (
        "No results found.\n"
        "Labs:\n"
        "No results found.\n"
        "Sodium 138.\n"
    )
    out, n, _ = run_learned(text, cfg, _ctx(cfg))
    # Inside Labs kept; outside removed.
    assert out.count("No results found.") == 1
    assert "Sodium 138." in out


def test_scope_default_removes_everywhere():
    cfg = {"learned_rules": [["No results found\\.", ""]]}
    text = "No results found.\nLabs:\nNo results found.\n"
    out, n, _ = run_learned(text, cfg, _ctx(cfg))
    assert n == 2
    assert "No results found." not in out


def test_scope_and_exceptions_coexist():
    cfg = {
        "learned_rules": [["Dr\\. Smith", ""]],
        "stage_options": {
            "learned_rules": {
                "scope": {"mode": "only", "sections": ["labs"]},
                "exceptions": ["important"],
            }
        },
    }
    text = (
        "Dr. Smith\n"
        "Labs:\n"
        "Dr. Smith is important\n"     # inside scope but matches exception -> kept
        "Dr. Smith\n"                  # inside scope, no exception -> removed
    )
    out, n, _ = run_learned(text, cfg, _ctx(cfg))
    assert "Dr. Smith is important" in out
    # Exactly one plain "Dr. Smith" (the in-scope, non-exception one) is removed.
    assert n == 1


# ---------------------------------------------------------------------------
# Pipeline registration
# ---------------------------------------------------------------------------

def test_fuzzy_line_dedup_registered_and_off_by_default():
    cfg = load_default_config()
    pipeline = Pipeline(cfg)
    ids = [s.id for s in pipeline.stages]
    assert "fuzzy_line_dedup" in ids
    stage = next(s for s in pipeline.stages if s.id == "fuzzy_line_dedup")
    assert stage.enabled is False


def test_new_stage_does_not_change_default_output():
    # The new stage is off by default and must make zero changes to a chart,
    # so existing default output is unaffected.
    cfg = load_default_config()
    text = "Assessment: patient stable, continue current plan.\nLabs: within normal limits.\n"
    result = Pipeline(cfg).run(text)
    fuzzy = next(s for s in result.stages if s.id == "fuzzy_line_dedup")
    assert fuzzy.enabled is False
    assert fuzzy.matches == 0


# ---------------------------------------------------------------------------
# Block rule from a multi-line highlight
# ---------------------------------------------------------------------------

def test_learned_block_pattern_removes_multiline_block():
    # A multi-line selection becomes a flexible (whitespace-tolerant) rule.
    pat = learned_pattern("Header line\nSome detail here\nFinal line", LEARN_REMOVE_BLOCK)
    assert pat
    cfg = {"learned_rules": [[pat, ""]]}
    text = "Before\nHeader line\n  Some detail here\nFinal line\nAfter\n"
    out, n, _ = run_learned(text, cfg, _ctx(cfg))
    assert n >= 1
    assert "Header line" not in out
    assert "After" in out


def test_learned_block_pattern_needs_two_lines():
    assert learned_pattern("single line", LEARN_REMOVE_BLOCK) == ""


# ---------------------------------------------------------------------------
# Rule health (feature 6)
# ---------------------------------------------------------------------------

def _instrumented_run(rule_hits: dict, lines_before: int = 10) -> dict:
    return {
        "ts": "2026-01-01",
        "stages": [{"id": "learned_rules", "skipped": False, "error": None,
                    "details": {"rule_hits": rule_hits, "lines_before": lines_before}}],
    }


def test_rule_health_flags_never_matched():
    cfg = {"learned_rules": [["No results found\\.", ""]]}
    runs = [_instrumented_run({})]  # stage ran, zero hits
    rows = health_report(cfg, runs, min_runs=1)
    assert any(r["status"] == "never matched" for r in rows)


def test_rule_health_ok_when_matched():
    cfg = {"learned_rules": [["No results found\\.", ""]]}
    rid = rule_id(cfg["learned_rules"][0][0])
    runs = [_instrumented_run({rid: 2})]
    rows = health_report(cfg, runs, min_runs=1)
    row = next(r for r in rows if r["pattern"] == "No results found\\.")
    assert row["status"] == "ok"
    assert row["hits"] == 2


def test_rule_health_ignores_uninstrumented_runs():
    cfg = {"learned_rules": [["No results found\\.", ""]]}
    runs = [{"ts": "2026-01-01", "stages": [{"id": "learned_rules", "details": {}}]}]
    rows = health_report(cfg, runs, min_runs=1)
    # No instrumented run -> not enough data, never "never matched".
    assert all(r["status"] == "not enough runs yet" for r in rows)


# ---------------------------------------------------------------------------
# Epic EHR Order Cruft and Attestation Boilerplate Cleaning
# ---------------------------------------------------------------------------

def test_ehr_order_review_and_lab_boilerplate_removed():
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    pipeline = Pipeline(cfg)

    text = (
        "XR CHEST 1V FRONTAL PORT\n"
        "\tTiming: Standing (ONCE)\n"
        "\tReason for Exam: SOB\n"
        "\tSTAT Order Task Reviewed by (Last Name, First Name):: DABNEY, SIDNEY [U1002695]\n"
        "COMPLETE BLOOD COUNT - Abnormal; Notable for the following components:\n"
        "\tHematocrit 38.9 (*)\n"
        "\tAll other components within normal limits\n"
        "Narrative:\n"
        "Unfractionated Heparin Therapeutic Ranges:\n"
        "Standard Intensity Nomogram: 53 to 78 seconds (heparin anti-Xa level of 0.3 to 0.7 U/mL)\n"
        "Lower Intensity Nomogram: 53 to 67 seconds (heparin anti-Xa level of 0.3 to 0.5 U/mL)\n"
        "No Bolus Lower Intensity Nomogram: 53 to 67 seconds (heparin anti-Xa level of 0.3 to 0.5 U/mL)\n"
        " Note: The APTT therapeutic range has been determined for the current lot of laboratory APTT reagent in use throughout the Cleveland Clinic Health System.\n"
        "This test was developed and its performance characteristics determined by the Cleveland Clinic's Robert J. Tomsich Pathology and Laboratory Medicine Institute (RT-PLMI).\n"
        "It has not been cleared or approved by the FDA. RT-PLMI is regulated under CLIA as qualified to perform high-complexity testing. This test is used for clinical purposes. It should not be regarded as investigational or for research.\n"
    )

    res = pipeline.run(text, wrap=False)
    assert "DABNEY, SIDNEY" not in res.text
    assert "STAT Order Task Reviewed by" not in res.text
    assert "All other components within normal limits" not in res.text
    assert "All other components WNL" not in res.text
    assert "Unfractionated Heparin Therapeutic Ranges" not in res.text
    assert "Tomsich Pathology" not in res.text
    assert "HCT 38.9" in res.text
    assert "XR CHEST" in res.text


def test_critical_care_and_attestation_boilerplate_removed():
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    pipeline = Pipeline(cfg)

    text = (
        "Assessment & Plan\n"
        "53 year old male with CIDP presenting with worsening weakness.\n"
        "====\n"
        "STAFF COORDINATION OF CRITICAL CARE\n\n"
        "CCHS Staff Physician note of personal involvement in Care\n"
        "The patient is critically ill because of imminent risk of acute respiratory failure and continues to require intensive support and observation.\n"
        "This patient has a high probability of sudden, clinically significant deterioration, which requires the highest level of physician preparedness to intervene urgently. I managed/supervized life or organ supporting interventions that required frequent physician assessment. I devoted my full attention to the direct care of this patient for the amount of time indicated below.\n"
        "CRITICAL CARE: I personally spent 33 minutes of critical care time involved in the care of this patient.\n"
        "====\n"
        "Please see the documented system-by-system plan in the updated problem list.\n"
        "Plan of care discussed with: Provider, RN, Patient and ICU Team.\n"
        "This is an electronically created document.\n"
        "If printed, please do not remove from the chart or modify printed copy.\n"
    )

    res = pipeline.run(text, wrap=False)
    assert "STAFF COORDINATION OF CRITICAL CARE" not in res.text
    assert "highest level of physician preparedness" not in res.text
    assert "CRITICAL CARE: I personally spent" not in res.text
    assert "system-by-system plan in the updated problem list" not in res.text
    assert "Plan of care discussed with" not in res.text
    assert "electronically created document" not in res.text
    assert "CIDP presenting" in res.text
    assert "worsening weakness" in res.text

