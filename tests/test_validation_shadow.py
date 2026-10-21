"""Shadow validation through the execution loop (``cleaning_validation``):
legacy output is unchanged, every decision is traceable, a failed check is
``failed`` (never complete), a warm cache cannot reuse a verdict, and ``off``
reports ``unavailable`` coverage instead of pretending."""

from __future__ import annotations

import json

import pytest

from chartcleaner import validation
from chartcleaner.engine import Pipeline, load_default_config, validate_config
from chartcleaner.stage_cache import IGNORED_KEYS, StageCache

SAMPLE = ("Progress Note\nElectronically signed by Dr. A. Example on 03/14/2024 08:12\n"
          "Subjective: Denies chest pain. No fever.\nObjective: BP 128/76, Na 141 mmol/L.\n"
          "Plan: aspirin 81 mg daily.\nThis message is confidential and intended only for the recipient.\n")


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [r"^Electronically signed.*$"],
           "boilerplate": [r"This message is confidential.*?recipient\."],
           "epic_phi_patterns": [], "literal_replacements": [["mmol/L", ""]],
           "clinical_headers": [], "nlp_redaction": {"enabled": False}, "wrap_output": False,
           "duplicate_note_detection": {"enabled": False}, "fuzzy_dedup": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}}}
    cfg.update(over)
    return cfg


def _mode(cfg: dict, mode: str) -> dict:
    return {**cfg, "cleaning_validation": {"mode": mode, "schema_version": 1}}


# --- the four plan sentinels --------------------------------------------------

def test_shadow_does_not_change_legacy_output():
    cfg = base()
    off = Pipeline(_mode(cfg, "off")).run(SAMPLE)
    shadow = Pipeline(_mode(cfg, "shadow")).run(SAMPLE)
    assert shadow.text == off.text
    assert shadow.validation is not None and shadow.ledger is not None
    assert shadow.ledger.current == shadow.text
    assert all(p.decision == "accept" for p in shadow.ledger.proposals), "shadow only records"
    # ...but the report says what enforcement would have done.
    assert shadow.validation.decision in ("accept", "retain_original", "withhold_release")
    assert {p.stage_id for p in shadow.ledger.proposals} >= {"metadata_lines", "boilerplate",
                                                             "literal_replacements"}
    assert all(p.report is not None for p in shadow.ledger.proposals)


def test_default_mode_is_shadow_without_rewriting_config():
    cfg = base()  # no cleaning_validation key at all
    result = Pipeline(cfg).run(SAMPLE)
    assert result.validation is not None and result.validation.coverage.status != "unavailable"
    assert "cleaning_validation" not in cfg, "the engine reads defaults; it never writes them back"
    assert load_default_config()["cleaning_validation"] == {"mode": "shadow", "schema_version": 1}


def test_check_failure_is_failed_not_complete(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(validation, "_CHECKS", {**validation._CHECKS, "facts": boom})
    result = Pipeline(_mode(base(), "shadow")).run(SAMPLE)
    assert result.validation.coverage.status == "failed"
    assert result.validation.decision != "accept"
    assert any(i.code == "validator_failed" for i in result.validation.issues)
    assert result.text == Pipeline(_mode(base(), "off")).run(SAMPLE).text, "shadow still ships legacy text"
    assert "synthetic" not in json.dumps(result.validation.summary())


def test_warm_cache_revalidates_after_policy_change(monkeypatch):
    cfg = _mode(base(), "shadow")
    cache = StageCache()
    first = Pipeline(cfg).run(SAMPLE, cache=cache)
    assert first.validation.coverage.status != "failed"

    def boom(*a, **k):
        raise RuntimeError("policy changed")

    monkeypatch.setattr(validation, "_CHECKS", {**validation._CHECKS, "facts": boom})
    second = Pipeline(cfg).run(SAMPLE, cache=cache)
    assert cache.hits > 0, "the candidate stage output was reused"
    assert second.text == first.text
    assert second.validation.coverage.status == "failed", "the verdict was not"


def test_off_mode_reports_unavailable():
    result = Pipeline(_mode(base(), "off")).run(SAMPLE)
    assert result.validation is not None
    assert result.validation.coverage.status == "unavailable"
    assert result.validation.coverage.reasons == ("validation_off",)
    assert result.validation.decision == "accept" and not result.validation.issues
    assert result.ledger is None
    assert result.fact_check is not None, "off disables the new validator, not the existing checks"


# --- what the shadow report says ---------------------------------------------

def test_shadow_report_flags_the_harmful_rule_and_names_its_source_span():
    cfg = base(boilerplate=[r"Subjective:.*$"])  # eats the negative findings
    result = Pipeline(_mode(cfg, "shadow")).run(SAMPLE)
    codes = {i.code for i in result.validation.issues}
    assert "negated_statement_removed" in codes
    bad = [i for i in result.validation.issues if i.code == "negated_statement_removed"][0]
    assert bad.stage_id == "boilerplate"
    start, end = bad.source_spans[0]
    assert "Denies chest pain" in SAMPLE[start:end]
    assert result.validation.decision == "retain_original"
    assert result.text == Pipeline(_mode(cfg, "off")).run(SAMPLE).text


def test_run_level_coverage_unions_proposals_and_keeps_unresolved_spans():
    result = Pipeline(_mode(base(), "shadow")).run(SAMPLE)
    cov = result.validation.coverage
    assert cov.input_chars == len(SAMPLE)
    assert cov.processed_chars == len(SAMPLE), "every character was either unchanged or checked"
    # Line removals are unverified until the statement extractor exists.
    assert cov.status == "partial" and cov.unresolved_spans
    assert "statements_unavailable" in cov.reasons


def test_history_dict_keeps_only_the_text_free_validation_summary():
    result = Pipeline(_mode(base(), "shadow")).run(SAMPLE)
    hist = result.to_history_dict("test")
    assert set(hist["validation"]) >= {"decision", "status", "issues", "by_code", "processed_chars"}
    dumped = json.dumps(hist)
    assert "Denies chest pain" not in dumped and "aspirin" not in dumped
    assert "ledger" not in hist


def test_budget_exhaustion_is_explicit_partial_coverage_not_success():
    cfg = _mode(base(literal_replacements=[["a", "a"]]), "shadow")
    cfg["cleaning_validation"]["max_proposals"] = 3
    result = Pipeline(cfg).run(SAMPLE)
    assert len(result.ledger.proposals) > 3
    cov = result.validation.coverage
    assert cov.status == "partial"
    assert "budget_exhausted" in cov.reasons
    assert cov.processed_chars < cov.input_chars
    unvalidated = [p for p in result.ledger.proposals if p.report is None]
    assert unvalidated, "proposals past the budget are recorded but unchecked"


def test_validation_context_is_available_to_custom_rules():
    cfg = _mode(base(), "shadow")
    pipe = Pipeline(cfg)
    captured = {}
    original = pipe._execute_stages

    def spy(text, ctx, tracker=None, cache=None):
        captured["ctx"] = ctx
        return original(text, ctx, tracker, cache)

    pipe._execute_stages = spy
    pipe.run(SAMPLE)
    ctx = captured["ctx"]
    assert ctx.validation_context is not None and ctx.validation_context.mode == "shadow"
    assert ctx.validation_context.source == SAMPLE


# --- configuration ------------------------------------------------------------

def test_cleaning_validation_config_is_validated():
    cfg = load_default_config()
    cfg["cleaning_validation"] = {"mode": "loud", "schema_version": "1"}
    errors, _ = validate_config(cfg)
    assert any("cleaning_validation.mode" in e for e in errors)
    assert any("cleaning_validation.schema_version" in e for e in errors)
    cfg["cleaning_validation"] = {"mode": "enforce", "schema_version": 1, "max_proposals": 0}
    errors, _ = validate_config(cfg)
    assert any("max_proposals" in e for e in errors)
    cfg["cleaning_validation"] = {"mode": "enforce", "schema_version": 1, "max_proposals": 10}
    errors, warnings = validate_config(cfg)
    assert not errors
    assert "cleaning_validation" in IGNORED_KEYS, "no stage runner reads it, so it must not key the cache"


def test_unknown_future_schema_version_is_an_error_not_a_silent_downgrade():
    cfg = load_default_config()
    cfg["cleaning_validation"] = {"mode": "shadow", "schema_version": 99}
    errors, _ = validate_config(cfg)
    assert any("schema_version" in e for e in errors)
