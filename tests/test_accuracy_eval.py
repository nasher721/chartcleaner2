"""Phase 0: the independent cleaning-accuracy benchmark (``accuracy_eval``).

The evaluator scores a candidate edit against literal, independently authored
labels (required statements, forbidden claims, authorized removals). It must
not lean on the production fact extractor: a dropped negative sentence with no
numbers is still a harmful loss, and the same numbers paired with the wrong
drug are still unsupported.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from chartcleaner import accuracy_eval
from chartcleaner.accuracy_eval import (
    AccuracyCase,
    CATEGORIES,
    evaluate_accuracy,
    load_cases,
    load_splits,
    minimal_config,
)
from chartcleaner.fact_check import verify_output

ROOT = Path(__file__).resolve().parent.parent


def _case(**over) -> AccuracyCase:
    base = dict(case_id="t-001", category="sentence_deletion", source="", expected="reject",
                candidate="", label_status="provisional")
    base.update(over)
    return AccuracyCase.from_dict(base)


# --- the four sentinel behaviours -------------------------------------------

def test_harmful_sentence_deletion_counts_as_escape():
    source = "Subjective: Slept well. Denies shortness of breath.\nPlan: continue current care.\n"
    candidate = "Subjective: Slept well.\nPlan: continue current care.\n"
    case = _case(source=source, candidate=candidate, expected="reject",
                 required_statements=["Denies shortness of breath"])
    report = evaluate_accuracy([case], minimal_config(), mode="off")
    totals = report.totals
    assert totals["harmful"] == 1
    assert totals["escapes"] == 1, "a deleted negative sentence with no numbers is a harmful loss"
    assert totals["required"] == 1 and totals["retained"] == 0
    assert report.outcomes[0].escape is True


def test_wrong_drug_same_number_counts_as_unsupported():
    source = "Medications:\naspirin 81 mg daily\natorvastatin 40 mg nightly\n"
    candidate = "Medications:\naspirin 40 mg daily\natorvastatin 81 mg nightly\n"
    # The production value check is satisfied (every number is in the source) —
    # which is exactly why the benchmark must not use it as the oracle.
    assert verify_output(source, candidate).ok
    case = _case(category="medication_relationships", source=source, candidate=candidate,
                 expected="reject", required_statements=["aspirin 81 mg", "atorvastatin 40 mg"],
                 forbidden_claims=["aspirin 40 mg", "atorvastatin 81 mg"])
    report = evaluate_accuracy([case], minimal_config(), mode="off")
    assert report.totals["unsupported"] == 2
    assert report.totals["escapes"] == 1
    assert report.outcomes[0].unsupported == 2


def test_zero_denominator_is_not_perfect_accuracy():
    # Only a harmful case: there is no valid-edit set, so the rejection rate is
    # undefined — not 0 % and not 100 %.
    case = _case(source="No fever.\n", candidate="Febrile.\n", expected="reject",
                 required_statements=["No fever"], forbidden_claims=["Febrile"])
    report = evaluate_accuracy([case], minimal_config(), mode="off")
    assert report.totals["valid"] == 0
    assert report.totals["valid_rejection_rate"] is None
    assert report.totals["clutter_removal_rate"] is None  # no authorized removals either
    assert report.totals["escape_rate"] == 1.0
    empty = evaluate_accuracy([], minimal_config(), mode="off")
    for rate in ("escape_rate", "valid_rejection_rate", "retention_rate", "clutter_removal_rate"):
        assert empty.totals[rate] is None
    assert json.dumps(empty.to_dict())  # None survives serialization as null


def test_split_manifest_has_no_duplicate_cases():
    cases = load_cases()
    splits = load_splits()
    dev, held = set(splits["development"]), set(splits["held-out"])
    assert dev.isdisjoint(held)
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    assert dev | held == set(ids), "every case belongs to exactly one split"
    assert len(dev) == 96 and len(held) == 24


# --- corpus shape -----------------------------------------------------------

def test_corpus_has_twelve_categories_with_valid_and_harmful_cases_in_each_split():
    cases = load_cases()
    assert len(cases) == 120
    assert set(c.category for c in cases) == set(CATEGORIES) and len(CATEGORIES) == 12
    for split in ("development", "held-out"):
        by_cat: dict[str, Counter] = {}
        for c in load_cases(split=split):
            by_cat.setdefault(c.category, Counter())[c.expected] += 1
        assert set(by_cat) == set(CATEGORIES)
        for cat, counts in by_cat.items():
            if split == "development":
                assert counts["accept"] >= 4 and counts["reject"] >= 4, (cat, counts)
            else:
                assert counts["accept"] >= 1 and counts["reject"] >= 1, (cat, counts)


def test_corpus_labels_are_provisional_and_synthetic():
    cases = load_cases()
    assert all(c.label_status == "provisional" for c in cases), \
        "labels become 'reviewed' only after clinician review"
    assert all(c.source and (c.candidate is not None or c.config is not None) for c in cases)


def test_corpus_labels_are_self_consistent():
    """Every harmful candidate shows literal harm; every valid one shows none."""
    report = evaluate_accuracy(load_cases(), minimal_config(), mode="off")
    bad = [o.case_id for o in report.outcomes if o.label_inconsistent]
    assert not bad, f"labels disagree with their own literal evidence: {bad}"
    assert report.totals["errors"] == 0


def test_forbidden_claims_are_absent_from_every_source():
    """Retaining the original must never count as an unsupported claim or exposure error."""
    from chartcleaner.accuracy_eval import present
    for c in load_cases():
        for claim in c.forbidden_claims:
            assert not present(claim, c.source), (c.case_id, claim)
        for phi in c.required_removals:
            assert present(phi, c.source), (c.case_id, phi)


def test_required_removal_left_in_place_is_an_exposure_not_clutter():
    case = _case(category="redaction_collisions", source="Patient: John Smith\nHgb 12.3\n",
                 candidate="Patient: John Smith\nHgb 12.3\n", expected="reject",
                 required_statements=["Hgb 12.3"], required_removals=["John Smith"])
    report = evaluate_accuracy([case], minimal_config(), mode="off")
    assert report.totals["exposed"] == 1 and report.totals["escapes"] == 1
    assert report.totals["phi_removal_rate"] == 0.0
    assert report.totals["authorized"] == 0 and report.totals["clutter_removal_rate"] is None


def test_word_boundaries_in_literal_matching():
    from chartcleaner.accuracy_eval import present
    assert not present("glucose 20", "POC glucose 200 at 0600")
    assert present("glucose 20", "POC glucose 20 at 0600")
    assert not present("QD", "lisinopril QDay")
    assert present("No  Acute\nDistress", "exam: no acute distress noted")


def test_off_mode_reports_unavailable_coverage_and_accepts_everything():
    report = evaluate_accuracy(load_cases(split="development"), minimal_config(), mode="off")
    assert report.totals["valid_rejected"] == 0
    assert report.coverage.get("unavailable", 0) == report.cases


def test_report_is_text_free_and_deterministic():
    cases = load_cases(split="development")
    a = evaluate_accuracy(cases, minimal_config(), mode="off")
    b = evaluate_accuracy(cases, minimal_config(), mode="off")
    assert a.metric_counts() == b.metric_counts()
    assert a.fixture_digest == b.fixture_digest and a.config_digest == b.config_digest
    dumped = json.dumps(a.to_dict())
    for c in cases:
        first_line = c.source.strip().splitlines()[0]
        assert first_line not in dumped, "reports carry counts and digests, never chart text"
    assert a.runtime_ms >= 0 and a.peak_memory_bytes > 0


def test_candidate_decider_hook_drives_decisions():
    case = _case(source="No fever.\n", candidate="Fever.\n", expected="reject",
                 required_statements=["No fever"])
    valid = _case(case_id="t-002", source="Page 1 of 2\nNo fever.\n", candidate="No fever.\n",
                  expected="accept", required_statements=["No fever"],
                  authorized_removals=["Page 1 of 2"])

    def reject_all(case, candidate, cfg, mode):
        return accuracy_eval.Decision("retain_original", "retain_original", "complete")

    report = evaluate_accuracy([case, valid], minimal_config(), mode="enforce", decide=reject_all)
    assert report.totals["escapes"] == 0
    assert report.totals["valid_rejected"] == 1 and report.totals["valid_rejection_rate"] == 1.0
    assert report.totals["removed"] == 0, "retaining the original keeps the clutter"
    assert report.totals["review_burden"] == 2


def test_pipeline_config_cases_run_the_real_pipeline():
    source = "Progress Note\nThis message is confidential and intended only for the recipient.\nNo fever.\n"
    case = _case(source=source, candidate=None, expected="accept",
                 config={"boilerplate": [r"This message is confidential.*?recipient\."]},
                 required_statements=["No fever"],
                 authorized_removals=["confidential and intended"])
    report = evaluate_accuracy([case], minimal_config(), mode="off")
    assert report.totals["removed"] == 1 and report.totals["retained"] == 1


# --- the CLI ----------------------------------------------------------------

def test_evaluator_script_writes_aggregate_report(tmp_path):
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "evaluate_cleaning_accuracy.py"),
         "--split", "development", "--mode", "off", "--report", str(out)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["split"] == "development" and data["mode"] == "off"
    assert data["cases"] == 96
    assert set(data["by_category"]) == set(CATEGORIES)
    assert "fixture_digest" in data and "config_digest" in data
    assert "outcomes" in data and all("source" not in o for o in data["outcomes"])


def test_evaluator_script_rejects_unknown_split_and_mode(tmp_path):
    for args in (["--split", "all"], ["--mode", "loud"]):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "evaluate_cleaning_accuracy.py"),
             *args, "--report", str(tmp_path / "r.json")],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        assert proc.returncode != 0
