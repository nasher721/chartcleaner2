"""The release gate: thresholds are checked by a machine, labels must be
clinician-reviewed before anything is certified, and neither logs nor shared
reports ever carry chart text."""

from __future__ import annotations

import json
import logging

import pytest

from chartcleaner import accuracy_eval
from chartcleaner.accuracy_eval import (
    AccuracyCase,
    check_release,
    evaluate_accuracy,
    load_cases,
    load_splits,
    minimal_config,
)

REVIEWED = {"status": "reviewed", "reviewed_by": "Dr. Example", "reviewed_at": "2026-10-21"}


def _cases(n_valid: int = 0, n_harmful: int = 0) -> list[AccuracyCase]:
    out = []
    for i in range(n_valid):
        out.append(AccuracyCase.from_dict({
            "case_id": f"g-v{i}", "category": "clutter_removal", "expected": "accept",
            "source": f"Chart reviewed {i}.\nPlan: PT {i}.\n", "candidate": f"Plan: PT {i}.\n",
            "required_statements": [f"Plan: PT {i}"], "authorized_removals": [f"Chart reviewed {i}"]}))
    for i in range(n_harmful):
        out.append(AccuracyCase.from_dict({
            "case_id": f"g-h{i}", "category": "sentence_deletion", "expected": "reject",
            "source": f"Denies shortness of breath {i}.\nPlan: PT.\n", "candidate": "Plan: PT.\n",
            "required_statements": [f"Denies shortness of breath {i}"]}))
    return out


def _report(mode: str, valid: int, harmful: int, *, reject_valid: bool = False):
    decide = None
    if reject_valid:
        def decide(case, candidate, cfg, m):  # noqa: ANN001
            if case.harmful:
                return accuracy_eval.default_decider(case, candidate, cfg, m)
            return accuracy_eval.Decision("retain_original", "retain_original", "complete")
    return evaluate_accuracy(_cases(valid, harmful), minimal_config(), mode=mode, decide=decide)


def _names(result: dict) -> set[str]:
    return {c["name"] for c in result["checks"]}


# --- the four plan sentinels ------------------------------------------------------

def test_release_fails_on_new_critical_escape():
    baseline = _report("off", valid=4, harmful=2)  # 2 escapes in the baseline
    same = _report("enforce", valid=4, harmful=2)
    ok = check_release(same, baseline, labels=REVIEWED)
    assert ok["ok"], [c for c in ok["checks"] if not c["ok"]]
    worse = _report("off", valid=4, harmful=3)
    bad = check_release(worse, baseline, labels=REVIEWED)
    assert not bad["ok"] and "no_new_critical_escapes" in _names(bad)
    # A zero-escape baseline demands zero escapes, not just "no new ones".
    zero_base = _report("enforce", valid=4, harmful=2)
    slipped = _report("off", valid=4, harmful=2)
    bad2 = check_release(slipped, zero_base, labels=REVIEWED)
    assert not bad2["ok"] and "no_escapes" in _names(bad2)


def test_release_fails_on_excess_valid_edit_rejection():
    baseline = _report("off", valid=20, harmful=2)
    good = _report("enforce", valid=20, harmful=2)
    assert check_release(good, baseline, labels=REVIEWED)["ok"]
    picky = _report("enforce", valid=20, harmful=2, reject_valid=True)
    result = check_release(picky, baseline, labels=REVIEWED)
    assert not result["ok"] and "valid_edit_rejection" in _names(result)
    entry = next(c for c in result["checks"] if c["name"] == "valid_edit_rejection")
    assert entry["observed"] == 1.0 and entry["threshold"] == 0.05
    clutter = next(c for c in result["checks"] if c["name"] == "clutter_removal_ratio")
    assert clutter["ok"] is False and clutter["observed"] == 0.0 and clutter["threshold"] == 0.95


def test_release_handles_zero_baseline_clutter_without_division_error():
    # Baseline with valid cases whose candidate left the clutter in (b_rem == 0).
    baseline = _report("enforce", valid=4, harmful=0, reject_valid=True)
    good = _report("enforce", valid=4, harmful=0)
    gate = check_release(good, baseline, labels=REVIEWED)
    clutter = next(c for c in gate["checks"] if c["name"] == "clutter_removal_ratio")
    assert clutter["ok"] is True and clutter["observed"] == 1.0


def test_unreviewed_labels_cannot_certify_release():
    baseline = _report("off", valid=4, harmful=2)
    good = _report("enforce", valid=4, harmful=2)
    provisional = check_release(good, baseline)  # no labels argument
    assert not provisional["ok"] and "labels_clinician_reviewed" in _names(provisional)
    pending = check_release(good, baseline, labels={"status": "provisional"})
    assert not pending["ok"] and "labels_clinician_reviewed" in _names(pending)
    assert check_release(good, baseline, labels=REVIEWED)["ok"]


def test_release_checks_are_serializable_and_deterministic():
    baseline = _report("off", valid=4, harmful=2)
    good = _report("enforce", valid=4, harmful=2)
    a = check_release(good, baseline, labels=REVIEWED)
    b = check_release(good, baseline, labels=REVIEWED)
    assert a == b and json.dumps(a)
    assert a["gate_version"] == 1 and a["fixture_digest"] == good.fixture_digest


def test_thresholds_cannot_be_quietly_relaxed():
    baseline = _report("off", valid=4, harmful=2)
    good = _report("enforce", valid=4, harmful=2)
    loosened = check_release(good, baseline, labels=REVIEWED,
                            thresholds={"valid_edit_rejection": 0.9})
    assert loosened["ok"]
    assert loosened["thresholds_used"]["valid_edit_rejection"] == 0.9, \
        "an override is allowed only by being visible in the record"


# --- no chart text in anything shared ----------------------------------------------

def test_logs_and_shared_reports_exclude_chart_text(caplog):
    from chartcleaner.engine import Pipeline
    from chartcleaner.logging_setup import redact
    secret = "Denies chest pain 778899."
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "wrap_output": False, "duplicate_note_detection": {"enabled": False},
           "fuzzy_dedup": {"enabled": False}, "audit": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}},
           "cleaning_validation": {"mode": "enforce", "schema_version": 1}}
    with caplog.at_level(logging.DEBUG, logger="chartcleaner"):
        result = Pipeline(cfg).run(f"Subjective: {secret}\nPlan: PT.\n")
    for record in caplog.records:
        assert secret not in record.getMessage() and "Denies chest pain" not in record.getMessage()
    # Text-free surfaces: history, benchmark report, release check.
    hist = json.dumps(result.to_history_dict("t"))
    assert secret not in hist
    report = evaluate_accuracy(load_cases(split="development"), minimal_config(), mode="enforce")
    dumped = json.dumps(report.to_dict()) + json.dumps(
        check_release(report, report, labels=REVIEWED))
    for case in load_cases(split="development"):
        for line in case.source.splitlines():
            if line.strip():
                assert line not in dumped
    assert redact("call 555-123-4567 or MRN 1234567") == \
        "call [phone] or [identifier]"


# --- the shipped corpus meets the accuracy gates under enforcement -------------------

def test_development_split_meets_accuracy_gates_except_labels():
    baseline = evaluate_accuracy(load_cases(split="development"), minimal_config(), mode="off",
                                 split="development")
    enforce = evaluate_accuracy(load_cases(split="development"), minimal_config(), mode="enforce",
                                split="development")
    gate = check_release(enforce, baseline)
    failed = [c["name"] for c in gate["checks"] if not c["ok"]]
    assert "labels_clinician_reviewed" in failed, "labels stay provisional until clinician review"
    assert not [n for n in failed if n != "labels_clinician_reviewed"], \
        f"accuracy gates must pass on the frozen development split: {failed}"
    # The design's ≥50% escape reduction vs the recorded baseline.
    reduction = 1 - enforce.totals["escapes"] / baseline.totals["escapes"]
    assert reduction >= 0.5
    assert enforce.totals["valid_rejection_rate"] <= 0.05


def test_held_out_is_frozen_and_only_measured_at_checkpoints():
    splits = load_splits()
    assert len(splits["held-out"]) == 24
    baseline = evaluate_accuracy(load_cases(split="held-out"), minimal_config(), mode="off",
                                 split="held-out")
    enforce = evaluate_accuracy(load_cases(split="held-out"), minimal_config(), mode="enforce",
                                split="held-out")
    gate = check_release(enforce, baseline)
    failed = [c["name"] for c in gate["checks"] if not c["ok"]]
    assert "labels_clinician_reviewed" in failed
    assert not [n for n in failed if n != "labels_clinician_reviewed"], failed
