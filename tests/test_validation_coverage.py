"""Typed validation records (``validation`` + the shared ``RuleContract``):
coverage is a union of processed ranges, missing checks are never successes,
and reports persist counts — not chart text."""

from __future__ import annotations

import json

import pytest

from chartcleaner import validation
from chartcleaner.edit_provenance import EditLedger, SourceSpan
from chartcleaner.rule_contracts import OPERATIONS, RuleContract
from chartcleaner.validation import (
    COVERAGE_STATUSES,
    DECISIONS,
    Coverage,
    ValidationContext,
    ValidationIssue,
    ValidationReport,
    validate_proposal,
)


def _context(source: str, mode: str = "shadow") -> ValidationContext:
    return ValidationContext.for_source(source, config={"x": 1}, ledger=EditLedger(source), mode=mode)


# --- coverage ---------------------------------------------------------------

def test_overlapping_processed_spans_count_once():
    cov = Coverage.from_spans(input_chars=100, processed=[(0, 50), (25, 75), (60, 70)])
    assert cov.processed_chars == 75
    assert cov.status == "partial"
    assert cov.processed_fraction == 0.75


def test_full_processing_without_unresolved_spans_is_complete():
    cov = Coverage.from_spans(input_chars=10, processed=[(0, 10)], recognized=3, checked=3)
    assert cov.status == "complete" and cov.unresolved_spans == ()


def test_unresolved_spans_keep_coverage_partial_even_when_everything_was_processed():
    cov = Coverage.from_spans(input_chars=10, processed=[(0, 10)], unresolved=[(2, 4)],
                              reasons=["deletion_unverified"])
    assert cov.status == "partial"
    assert cov.unresolved_spans == (SourceSpan(2, 4),)


def test_nothing_processed_is_unavailable_and_failure_is_failed():
    assert Coverage.from_spans(input_chars=10, processed=[]).status == "unavailable"
    failed = Coverage.from_spans(input_chars=10, processed=[(0, 10)], failed=True,
                                 reasons=["validator_failed"])
    assert failed.status == "failed"
    assert set(COVERAGE_STATUSES) == {"complete", "partial", "unavailable", "failed"}


def test_empty_input_has_no_fraction_not_a_perfect_one():
    cov = Coverage.from_spans(input_chars=0, processed=[])
    assert cov.processed_fraction is None
    assert cov.status == "unavailable"


# --- records -----------------------------------------------------------------

def test_report_serialization_is_text_free_and_stable():
    issue = ValidationIssue(code="fact_lost", severity="error", stage_id="boilerplate",
                            rule_key="abc", source_spans=(SourceSpan(0, 12),),
                            message="A clinical value was removed")
    report = ValidationReport(issues=(issue,),
                              coverage=Coverage.from_spans(input_chars=12, processed=[(0, 12)]),
                              decision="retain_original")
    data = json.loads(json.dumps(report.to_dict()))
    assert data["decision"] == "retain_original"
    assert data["issues"][0]["code"] == "fact_lost"
    assert data["issues"][0]["source_spans"] == [[0, 12]]
    summary = report.summary()
    assert summary["issues"] == 1 and summary["by_code"] == {"fact_lost": 1}
    assert "message" not in json.dumps(summary)
    assert set(DECISIONS) == {"accept", "retain_original", "withhold_release"}


def test_invalid_enum_values_are_rejected():
    with pytest.raises(ValueError):
        ValidationIssue(code="x", severity="loud", stage_id="s", rule_key=None, source_spans=(), message="")
    with pytest.raises(ValueError):
        ValidationReport(issues=(), coverage=Coverage.from_spans(1, []), decision="maybe")
    with pytest.raises(ValueError):
        RuleContract(key="k", operation="delete_everything")


def test_rule_contract_defaults_are_conservative():
    c = RuleContract(key="k", operation="remove_boilerplate")
    assert c.version == 1
    assert c.allow_cross_section is False
    assert c.sections == () and c.note_types == ()
    assert "facts" in c.required_checks and "meaning" in c.required_checks
    assert c.allowed_losses == ()
    assert set(OPERATIONS) == {"format", "abbreviate", "remove_boilerplate", "redact", "reduce"}
    fmt = RuleContract(key="f", operation="format")
    assert fmt.required_checks == ("content_preserved",), "formatting needs no clinical parsing"


# --- validate_proposal: the adapters over today's checks --------------------

def test_deleting_a_line_with_a_lab_value_is_a_fact_loss():
    source = "Na 141 mmol/L\nPlan: PT\n"
    ctx = _context(source)
    p = ctx.ledger.propose("boilerplate", "r", 0, 14, "")
    report = validate_proposal(p, RuleContract(key="r", operation="remove_boilerplate"), ctx)
    assert report.decision == "retain_original"
    assert [i.code for i in report.issues] == ["fact_lost", "statement_lost"]
    assert report.issues[0].source_spans == (SourceSpan(0, 14),)
    # The removed text was recognized and judged: processing and relation
    # coverage are both complete for this proposal.
    assert report.coverage.status == "complete"
    assert report.coverage.processed_chars == report.coverage.input_chars == 14


def test_dropping_a_negation_is_a_meaning_violation():
    source = "Exam: No fever overnight.\n"
    ctx = _context(source)
    p = ctx.ledger.propose("literal_replacements", "r", 6, 8, "")
    report = validate_proposal(p, RuleContract(key="r", operation="remove_boilerplate"), ctx)
    assert any(i.code == "negation_lost" and i.severity == "critical" for i in report.issues)
    assert report.decision == "retain_original"


def test_deleting_a_negative_sentence_without_numbers_is_detected_not_approved():
    source = "Denies shortness of breath.\nPlan: PT\n"
    ctx = _context(source)
    p = ctx.ledger.propose("boilerplate", "r", 0, 28, "")
    report = validate_proposal(p, RuleContract(key="r", operation="remove_boilerplate"), ctx)
    # No recognized numeric fact — but the statement extractor recognizes the
    # negated finding, so its loss is a definite finding, not a clean accept.
    assert any(i.code == "statement_lost" for i in report.issues)
    assert "deletion_unverified" not in [i.code for i in report.issues], "recognized, so not unverified"
    assert report.coverage.recognized_statements == 1
    assert report.decision == "retain_original"


def test_deleting_text_with_signal_but_no_recognized_statement_stays_unverified():
    source = "Dictated but not read.\nPlan: PT\n"
    ctx = _context(source)
    p = ctx.ledger.propose("boilerplate", "r", 0, 23, "")
    report = validate_proposal(p, RuleContract(key="r", operation="remove_boilerplate"), ctx)
    assert report.coverage.status == "partial"
    assert report.coverage.unresolved_spans == (SourceSpan(0, 23),)
    assert any(i.code == "deletion_unverified" for i in report.issues)
    assert not report.blocking, "unverified is not a definite violation"
    assert report.decision == "retain_original", "an explicit destructive contract waits for review"


def test_whitespace_only_formatting_is_accepted_without_clinical_parsing():
    source = "Na   141\tmmol/L\n"
    ctx = _context(source)
    p = ctx.ledger.propose("whitespace", None, 0, 15, "Na 141 mmol/L")
    report = validate_proposal(p, RuleContract(key="ws", operation="format"), ctx)
    assert report.decision == "accept" and not report.issues
    assert report.coverage.status == "complete"
    assert report.coverage.reasons == ("content_preserved",)


def test_format_contract_that_changes_content_is_rejected():
    source = "Na 141 mmol/L\n"
    ctx = _context(source)
    p = ctx.ledger.propose("whitespace", None, 0, 13, "Na 14 mmol/L")
    report = validate_proposal(p, RuleContract(key="ws", operation="format"), ctx)
    assert report.decision == "retain_original"
    assert any(i.code == "content_changed" for i in report.issues)


def test_validator_crash_is_failed_coverage_not_success(monkeypatch):
    source = "Na 141 mmol/L\n"
    ctx = _context(source)
    p = ctx.ledger.propose("boilerplate", "r", 0, 13, "")

    def boom(*a, **k):
        raise RuntimeError("synthetic validator failure")

    monkeypatch.setattr(validation, "_CHECKS", {**validation._CHECKS, "facts": boom})
    report = validate_proposal(p, RuleContract(key="r", operation="remove_boilerplate"), ctx)
    assert report.coverage.status == "failed"
    assert report.decision == "retain_original"
    assert any(i.code == "validator_failed" for i in report.issues)
    assert "synthetic" not in json.dumps(report.summary())


def test_context_digests_and_versions_are_recorded():
    ctx = _context("abc", mode="shadow")
    assert ctx.mode == "shadow" and len(ctx.source_digest) == 64 and len(ctx.config_digest) == 64
    from chartcleaner.clinical_statements import StatementIndex
    assert isinstance(ctx.statement_index, StatementIndex), "built lazily from the source"
    assert ctx.statement_index is ctx.statement_index
    assert ctx.check_versions and all(isinstance(v, int) for v in ctx.check_versions.values())
    with pytest.raises(ValueError):
        ValidationContext.for_source("abc", config={}, ledger=EditLedger("abc"), mode="loud")
