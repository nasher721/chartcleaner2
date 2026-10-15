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
