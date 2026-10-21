"""Relationship comparison (``statement_compare``): evidence must support the
relationship being asserted — same numbers under another drug or date, a
held drug read as active, a possible diagnosis read as confirmed, a family
history read as the patient's, or a vanished negative sentence are all caught,
while approved abbreviations, whitespace, preserved tuples and explicit
latest-only reductions pass."""

from __future__ import annotations

import json

import pytest

from chartcleaner.clinical_statements import extract_statements
from chartcleaner.edit_provenance import EditLedger
from chartcleaner.engine import Pipeline
from chartcleaner.fact_check import verify_output
from chartcleaner.rule_contracts import RuleContract
from chartcleaner.statement_compare import compare_statements, verify_claims
from chartcleaner.validation import ValidationContext, validate_proposal


def _compare(source: str, candidate: str, operation: str = "reduce", **kw):
    return compare_statements(extract_statements(source), extract_statements(candidate),
                              operation=operation, **kw)


def _codes(report) -> list[str]:
    return [i.code for i in report.issues]


# --- the six plan sentinels -----------------------------------------------------

def test_same_doses_swapped_between_drugs_are_unsupported():
    source = "aspirin 81 mg daily\natorvastatin 40 mg nightly\n"
    claim = "aspirin 40 mg daily; atorvastatin 81 mg nightly"
    assert verify_output(source, claim).ok, "the value-only check is fooled"
    report = verify_claims(source, claim)
    assert report.decision != "accept"
    changed = [i for i in report.issues if i.code == "value_changed"]
    assert len(changed) == 2
    # The conflicting evidence is the source statement for that drug.
    spans = {source[s.start:s.end].strip() for i in changed for s in i.source_spans}
    assert spans == {"aspirin 81 mg daily", "atorvastatin 40 mg nightly"}


def test_prior_day_value_does_not_support_current_day_claim():
    source = "03/12: Na 131.\n03/14: Na 138.\n"
    assert verify_output(source, "Na 131 on 03/14").ok
    report = verify_claims(source, "Na 131 on 03/14")
    assert "temporality_changed" in _codes(report) and report.decision != "accept"
    assert verify_claims(source, "Na 131 on 03/12").decision == "accept"
    assert verify_claims(source, "Na 138 today").decision == "accept", "the latest value is current"
    assert verify_claims(source, "Na 131 today").decision != "accept", "an older one is not"
    assert verify_claims(source, "Na 131").decision == "accept", "an undated mention stays compatible"


def test_held_medication_cannot_become_active():
    source = "Metoprolol 25 mg BID — held for hypotension.\n"
    report = verify_claims(source, "Metoprolol 25 mg BID")
    assert "medication_status_changed" in _codes(report) and report.decision != "accept"
    assert verify_claims(source, "Metoprolol held").decision == "accept"
    cleaning = _compare(source, "Metoprolol 25 mg BID.")
    assert "medication_status_changed" in _codes(cleaning)


def test_possible_diagnosis_cannot_become_confirmed():
    report = verify_claims("Assessment: Possible pneumonia.\n", "Pneumonia.")
    assert "certainty_changed" in _codes(report) and report.decision != "accept"
    assert verify_claims("Assessment: Possible pneumonia.\n", "Possible pneumonia").decision == "accept"
    assert verify_claims("Assessment: Pneumonia.\n", "Possible pneumonia").decision == "accept", \
        "hedging a stated finding does not claim more than the chart"


def test_family_history_cannot_become_patient_history():
    report = verify_claims("Family history of stroke.\n", "History of stroke.")
    assert "experiencer_changed" in _codes(report) and report.decision != "accept"
    assert verify_claims("Family history of stroke.\n", "Family history of stroke").decision == "accept"


def test_deleted_negative_sentence_is_not_invisible():
    source = "Subjective: Slept well. Denies shortness of breath.\nPlan: PT.\n"
    candidate = "Subjective: Slept well.\nPlan: PT.\n"
    report = _compare(source, candidate, operation="remove_boilerplate")
    lost = [i for i in report.issues if i.code == "statement_lost"]
    assert lost and report.decision == "retain_original"
    span = lost[0].source_spans[0]
    assert "Denies shortness of breath" in source[span.start:span.end]


# --- valid changes -------------------------------------------------------------

def test_approved_abbreviations_are_the_same_statement():
    assert _compare("Denies shortness of breath.\n", "Denies SOB.\n", "abbreviate").decision == "accept"
    assert _compare("Exam: No acute distress.\n", "Exam: NAD.\n", "abbreviate").decision == "accept"
    assert _compare("aspirin 81 milligrams by mouth daily", "aspirin 81 mg PO daily", "abbreviate").decision == "accept"


def test_whitespace_and_wrapping_change_nothing():
    src = "Labs: Na 131 on 03/12,   Na 138 on 03/14.\nDenies  fever,   chills.\n"
    cand = "Labs: Na 131 on 03/12, Na 138 on 03/14.\nDenies fever, chills.\n"
    report = _compare(src, cand, "format")
    assert report.decision == "accept" and not report.issues


def test_preserved_dose_route_frequency_and_uncertainty_pass():
    src = "Meds:\n  - Metoprolol tartrate 25 mg PO BID\n  - Lisinopril 10 mg PO daily\nAssessment: Likely viral syndrome.\n"
    cand = "Meds:\n- Metoprolol tartrate 25 mg PO BID\n- Lisinopril 10 mg PO daily\nAssessment: Likely viral syndrome.\n"
    assert _compare(src, cand).decision == "accept"


def test_repeated_statements_on_the_same_event_fold_without_loss():
    src = "Plan: Continue ceftriaxone 1 g IV daily.\n\nPlan: Continue ceftriaxone 1 g IV daily.\n"
    cand = "Plan: Continue ceftriaxone 1 g IV daily.\n"
    assert _compare(src, cand).decision == "accept"
    # ...but distinct dates stay distinct even when the text otherwise repeats.
    src = "03/12: Na 131.\n03/14: Na 131.\n"
    report = _compare(src, "03/12: Na 131.\n")
    assert "statement_lost" in _codes(report)


def test_explicit_latest_only_reduction_is_an_intentional_loss():
    src = "03/12: Na 131.\n03/14: Na 138.\n"
    cand = "Na 138 (03/14).\n"
    strict = _compare(src, cand)
    assert "statement_lost" in _codes(strict)
    policy = _compare(src, cand, allowed_losses=("prior_values",))
    assert "statement_lost" not in _codes(policy)
    assert "intentional_loss" in _codes(policy) and policy.decision == "accept"
    # The policy does not cover losing the *latest* value.
    report = _compare(src, "Na 131 (03/12).\n", allowed_losses=("prior_values",))
    assert "statement_lost" in _codes(report)


def test_conflicting_source_statements_are_kept_as_conflicts_not_resolved():
    src = "Day 1: No fever.\nDay 2: Fever overnight.\n"
    both = _compare(src, src)
    assert both.decision == "accept" and not both.issues, "the parser picks no winner"
    one = _compare(src, "Day 1: No fever.\n")
    assert "statement_lost" in _codes(one)


# --- generated text (summary / Q&A) --------------------------------------------

def test_summary_omission_is_not_a_failure_but_an_unsupported_claim_is():
    src = "aspirin 81 mg daily\natorvastatin 40 mg nightly\nDenies chest pain.\n"
    ok = verify_claims(src, "On aspirin 81 mg daily.")
    assert ok.decision == "accept" and not ok.blocking
    bad = verify_claims(src, "On aspirin 81 mg daily. Reports chest pain.")
    assert "polarity_changed" in _codes(bad) and bad.decision != "accept"


def test_unchecked_prose_is_visibly_unchecked():
    src = "aspirin 81 mg daily\n"
    report = verify_claims(src, "The patient seems to be doing somewhat better overall.")
    assert report.coverage.recognized_statements == 0
    assert report.coverage.status in ("partial", "unavailable")
    assert report.decision == "accept", "nothing was claimed that the chart contradicts"
    summary = report.summary()
    assert "score" not in summary, "no numerical accuracy score for prose nobody checked"
    assert json.dumps(report.to_dict())


def test_claims_with_signal_that_do_not_parse_are_unresolved():
    report = verify_claims("Na 141.\n", "Na 141. Denies.")
    assert report.coverage.unresolved_spans
    assert report.coverage.status == "partial"


# --- wired into proposal validation ----------------------------------------------

def test_proposal_validation_uses_statements_for_removals():
    source = "Subjective: Slept well. Denies shortness of breath.\nPlan: PT.\n"
    ledger = EditLedger(source)
    ctx = ValidationContext.for_source(source, config={}, ledger=ledger, mode="shadow")
    start = source.index(" Denies")
    p = ledger.propose("boilerplate", "k", start, source.index("\nPlan"), "")
    report = validate_proposal(p, RuleContract(key="k", operation="remove_boilerplate", legacy=True), ctx)
    assert "statement_lost" in _codes(report) and report.decision == "retain_original"
    # A recognized, checked removal is resolved: no "unverified" marker any more.
    assert "deletion_unverified" not in _codes(report)
    assert report.coverage.recognized_statements >= 1


def test_pipeline_run_reports_statement_losses_by_stage():
    cfg = {"emr_line_metadata": [], "boilerplate": [r"Denies shortness of breath\."], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "wrap_output": False, "duplicate_note_detection": {"enabled": False},
           "fuzzy_dedup": {"enabled": False}, "stage_options": {"medical_abbreviations": {"enabled": False}}}
    result = Pipeline(cfg).run("Subjective: Slept well. Denies shortness of breath.\nPlan: PT.\n")
    lost = [i for i in result.validation.issues if i.code == "statement_lost"]
    assert lost and lost[0].stage_id == "boilerplate"


# --- summaries and Q&A carry the richer report additively ----------------------

CHART = "Meds: aspirin 81 mg daily. Metoprolol 25 mg BID — held for hypotension.\nDenies chest pain.\n"


class FakeClient:
    def __init__(self, reply: str):
        self.reply = reply

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["fake"]

    def generate(self, prompt: str, model: str = "fake", system: str | None = None, **kw) -> str:
        return self.reply


def _llm_cfg() -> dict:
    return {"local_llm": {"enabled": True, "model": "fake", "base_url": "http://127.0.0.1:11434",
                          "prompt_preset": "one_liner"}}


def test_summary_result_carries_claims_report_without_changing_facts():
    from chartcleaner.summarizer import summarize
    res = summarize(CHART, _llm_cfg(), client=FakeClient("On aspirin 81 mg daily and metoprolol 25 mg BID."))
    assert res.facts is not None and res.facts.ok, "the value-only check still passes (legacy type kept)"
    assert res.claims is not None
    assert "medication_status_changed" in _codes(res.claims), "the held drug read as active is caught"


def test_qa_result_and_service_payload_carry_claims():
    from chartcleaner import service
    payload = service.ask("What is the patient on?", CHART, config=_llm_cfg(),
                          client=FakeClient("Aspirin 81 mg daily. Denies chest pain."))
    assert payload["claims"] is not None and payload["claims"]["decision"] == "accept"
    assert payload["facts"] is not None
