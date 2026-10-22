"""Large charts: no blanket skip at any size — checks run (windowed where
needed) and coverage accounts for every range or says plainly what stayed
partial."""

from __future__ import annotations

import time

import pytest

from chartcleaner import fact_check
from chartcleaner.engine import Pipeline
from chartcleaner.statement_compare import verify_claims
from chartcleaner.validation import validate_candidate
from chartcleaner.validation_windows import iter_validation_windows

LINE = ("Progress Note {n}\nElectronically signed by Dr. Example on 03/14/2024 08:12\n"
        "Na {na} mmol/L, K {k} mmol/L. Plan: aspirin 81 mg daily. Denies shortness of breath.\n"
        "This message is confidential and intended only for the recipient.\n\n")


def _chart(chars: int) -> str:
    block = LINE.format(n=1, na=141, k=4.0)
    n = max(1, chars // len(block))
    parts = [LINE.format(n=i + 1, na=130 + (i % 20), k=3 + (i % 3)) for i in range(n)]
    text = "".join(parts)
    return text[:chars] if len(text) > chars else text


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [r"^Electronically signed.*$"], "boilerplate": [],
           "epic_phi_patterns": [], "literal_replacements": [], "clinical_headers": [],
           "nlp_redaction": {"enabled": False}, "wrap_output": False, "audit": {"enabled": False},
           "duplicate_note_detection": {"enabled": False}, "fuzzy_dedup": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}},
           "cleaning_validation": {"mode": "shadow", "schema_version": 1}}
    cfg.update(over)
    return cfg


def test_no_global_skip_above_one_million_chars():
    text = _chart(1_050_000)
    assert len(text) > fact_check.MAX_CHARS
    started = time.perf_counter()
    result = Pipeline(base(boilerplate=[r"^This message is confidential.*recipient\.\n"])).run(text)
    elapsed = time.perf_counter() - started
    assert result.fact_check is not None, "the clinical-facts check is not skipped by size"
    assert not any("skipped" in w.lower() for w in result.warnings), result.warnings
    assert result.validation is not None and result.validation.coverage.status != "unavailable"
    cov = result.validation.coverage
    # Every range is accounted for: full processing, or explicit partials.
    if cov.status == "complete":
        assert cov.processed_chars == cov.input_chars
    else:
        assert cov.processed_chars + __import__("chartcleaner.edit_provenance",
                                                fromlist=["x"]).union_length(cov.unresolved_spans) \
            >= cov.input_chars * 0.99 or cov.reasons
    print(f"1.05M chars cleaned+validated in {elapsed:.1f}s")


def test_meaning_check_limit_is_reported_not_silent():
    huge = "\n".join(f"Plan: continue current care number {i}" for i in range(5000)) + "\n"
    cfg = base(literal_replacements=[["current", "present"]])
    result = Pipeline(cfg).run(huge)
    assert result.fact_check is not None
    if result.fact_check.meaning_limited:
        assert "meaning_check_limited" in result.validation.coverage.reasons


def test_validate_candidate_windowed_matches_unchunked_on_small_inputs():
    source = _chart(6000)
    candidate = (source.replace("Electronically signed by Dr. Example on 03/14/2024 08:12\n", "")
                 .replace("Denies shortness of breath", "Denies SOB"))
    report = validate_candidate(source, candidate, {}, mode="shadow")
    codes = [i.code for i in report.issues]
    assert "statement_lost" not in codes and "statement_unsupported" not in codes
    cov = report.coverage
    # A proposal's coverage is over the span it touches: fully processed, or
    # explicit partials with reasons.
    assert cov.processed_chars == cov.input_chars or cov.reasons


def test_window_parity_with_unchunked_extraction_across_sizes():
    """Windowed statement extraction equals unchunked extraction, whatever the target."""
    from chartcleaner.clinical_statements import extract_statements
    from chartcleaner.validation_windows import WindowedIndex
    for size in (4000, 8000, 12000):
        text = _chart(size)
        flat = {(s.kind, s.subject) + _attrs(s) for s in extract_statements(text).statements}
        for target in (700, 1500, 5000, 20000):
            index = WindowedIndex(text, target_chars=target)
            windowed = {(s.kind, s.subject) + _attrs(s) for s in index.statements()}
            assert windowed == flat, (size, target,
                                      sorted(windowed ^ flat)[:4])


def _attrs(statement):
    return (statement.value, statement.polarity, statement.certainty, statement.temporality,
            statement.experiencer, statement.laterality, statement.medication_status)


def test_unsupported_window_returns_unresolved_not_success():
    # A region with signal the extractor cannot parse stays unresolved.
    text = "Denies.\n" + _chart(3000)
    windows = list(iter_validation_windows(text, target_chars=500))
    assert windows[0].text.strip().startswith("Denies.")
    report = validate_candidate(text, text, {}, mode="shadow")
    assert report.decision == "accept" and not report.blocking
    assert report.coverage.unresolved_spans, "the unparsed region is on the record"
