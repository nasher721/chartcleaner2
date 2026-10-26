"""Performance changes must preserve validation and bounded work."""
from __future__ import annotations

import random
import re
from dataclasses import replace
import pytest

from chartcleaner import review_state, validation
from chartcleaner.edit_provenance import EditLedger, line_opcodes
from chartcleaner.engine import Pipeline, load_default_config
from chartcleaner.rule_contracts import RuleContract, set_contract
from chartcleaner.stage_cache import StageCache
from tests.test_validation_shadow import base


def test_warm_shadow_preserves_explicit_rule_contract_and_provenance():
    cfg = base(literal_replacements=[[r"No fever\.", ""]])
    cfg = set_contract(cfg, "literal_replacements", r"No fever\.", "",
                       operation="remove_boilerplate", allowed_losses=("statement:",),
                       required_checks=("statements",), reviewed=True, allow_cross_section=True)
    cache = StageCache()
    source = "Progress Note\nSubjective: No fever.\nPlan: continue care.\n"
    cold = Pipeline(cfg).run(source, cache=cache)
    warm = Pipeline(cfg).run(source, cache=cache)
    assert cold.text == warm.text
    assert cold.validation == warm.validation
    assert [p.to_summary() for p in cold.ledger.proposals] == [p.to_summary() for p in warm.ledger.proposals]
    assert review_state.release_decision(cold, output_text=cold.text) == review_state.release_decision(warm, output_text=warm.text)


def test_edited_result_does_not_reuse_the_pipeline_validation():
    source = "Possible pneumonia."
    result = Pipeline(base()).run(source)
    result.text = "Pneumonia."
    decision = review_state.release_decision(result, output_text=result.text, source=source)
    assert decision.status == "review" and "edited_output_issues" in decision.reasons
    report = result.edited_validation
    assert review_state.release_decision(result, output_text=result.text, source=source) == decision
    assert result.edited_validation is report
    changed = replace(result, text="No pneumonia.")
    review_state.release_decision(changed, output_text=changed.text, source=source)
    assert changed.edited_validation is not report


def test_redaction_inspection_does_no_unbounded_alignment(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("unbounded diff")
    monkeypatch.setattr(review_state, "SequenceMatcher", forbidden)
    source = "No fever\n" * 12000
    assert review_state.redaction_candidates(source, source) == []
    output = source + "[REDACTED_NAME]\n"
    items = review_state.redaction_candidates(source + "John Smith\n", output)
    assert len(items) == 1 and items[0]["original"] is None
    assert output[items[0]["start"]:items[0]["end"]] == items[0]["token"]


def test_unresolved_manual_deletions_require_review():
    source = "Patient seems comfortable today."
    result = Pipeline(base()).run(source)
    decision = review_state.release_decision(result, output_text="", source=source)
    assert decision.status == "review" and "edited_output_issues" in decision.reasons


def test_restored_redaction_requires_acknowledgement_without_claiming_deidentification():
    source = "Patient: John Smith\nNo fever"
    result = Pipeline(base(epic_phi_patterns=[["John Smith", "[REDACTED_NAME]"]])).run(source)
    decision = review_state.release_decision(result, output_text=source, source=source)
    assert decision.status == "review" and not decision.deidentification_complete
    acknowledged = review_state.release_decision(
        result, output_text=source, source=source, acknowledgement=decision.acknowledgement_key)
    assert acknowledged.status == "ready" and not acknowledged.deidentification_complete
    moved = review_state.release_decision(result, output_text=source + "\n[REDACTED_NAME]", source=source)
    assert moved.status == "review" and not moved.deidentification_complete
    changed_type = review_state.release_decision(
        result, output_text=result.text.replace("[REDACTED_NAME]", "[REDACTED_ID]"), source=source)
    assert changed_type.deidentification_complete and "redaction_unverified" not in changed_type.reasons


def test_oversized_manual_output_stops_before_expensive_regex_checks(monkeypatch):
    monkeypatch.setattr(validation, "validate_proposal", lambda *args: pytest.fail("unbounded checks"))
    report = validation.validate_candidate("No fever.", "x" * 600_001, {})
    assert report.decision == "retain_original" and report.coverage.status == "unavailable"
    assert "budget_exhausted" in report.coverage.reasons


def test_bounded_diff_keeps_every_line_and_exact_equal_regions():
    rng = random.Random(11)
    for _ in range(40):
        before = [rng.choice(["No fever", "Na 140", "", "Plan"]) for _ in range(800)]
        after = list(before)
        for _ in range(8):
            at = rng.randrange(len(after))
            after[at:at + 1] = ["new", "Na 141"] if rng.random() < .5 else []
        old_at = new_at = 0
        for tag, i1, i2, j1, j2 in line_opcodes(before, after):
            assert (i1, j1) == (old_at, new_at)
            if tag == "equal":
                assert before[i1:i2] == after[j1:j2]
            old_at, new_at = i2, j2
        assert (old_at, new_at) == (len(before), len(after))


def test_statement_batch_preserves_sequential_overlapping_edit_semantics():
    rng = random.Random(17)
    source = "Na 140 mmol/L. No fever.\n" * 12
    for _ in range(35):
        ledger = EditLedger(source)
        proposals = []
        for _ in range(15):
            start = rng.randrange(len(source))
            end = min(len(source), start + rng.randrange(0, 45))
            proposals.append(ledger.propose("s", None, start, end,
                                            rng.choice(["Na 141 mmol/L", "No fever", "", "aspirin 81 mg daily"])))
        batch, sequential = validation.StatementSet(source), validation.StatementSet(source)
        batch.apply_many(proposals)
        for proposal in proposals:
            sequential.apply(proposal)
        assert batch.statements == sequential.statements
        assert batch.version == sequential.version


def test_source_numbers_are_reused_only_within_the_same_source(monkeypatch):
    original = validation._ANY_NUMBER
    scans = []
    class Counter:
        def finditer(self, text):
            scans.append(text)
            return original.finditer(text)
    monkeypatch.setattr(validation, "_ANY_NUMBER", Counter())
    contract = RuleContract("test", "abbreviate")
    for source, expected_issue in (("Dose 123\n" * 1000, False), ("Dose 456\n" * 1000, True)):
        context = validation.ValidationContext.for_source(source, config={})
        proposal = context.ledger.propose("s", None, 0, 4, "Dose 123")
        lines = validation._Lines("Dose", "Dose 123", "Dose", "Dose 123")
        for _ in range(5):
            report = validation._check_introduced(proposal, contract, context, lines)
            assert bool(report.issues) == expected_issue
        assert scans.count(source) == 1


def test_cache_bounds_removed_text_and_avoids_unchanged_output_retention():
    cache = StageCache(max_chars=1000)
    cache.put("large-details", "", 1, {"changes": [{"before": "x" * 1001}]})
    assert cache.get("large-details") is None
    cache.put("large-token-map", "", 1, {"token_map": {"x" * 1001: "[NAME]"}})
    assert cache.get("large-token-map") is None
    text = "x" * 200_000
    for i in range(26):
        cache.put(str(i), text, 0, {}, input_text=text)
    for i in range(26):
        assert cache.get(str(i), input_text=text) == (text, 0, {})
    assert cache.hits == 26 and cache._chars <= cache.max_chars
    cache.clear()
    assert not cache._sizes and cache._chars == 0


def test_long_chart_reuses_candidates_without_changing_validation():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    text = "".join(f"Observation {i}: history of hypertension. Na 140 mmol/L. No fever.\n" for i in range(2800))
    cache = StageCache()
    cold = Pipeline(cfg).run(text, cache=cache, track_changes=True)
    warm = Pipeline(cfg).run(text, cache=cache, track_changes=True)
    assert cache.hits > 0
    assert cold.text == warm.text and cold.validation == warm.validation
    assert cache._chars <= cache.max_chars


def test_section_anchor_preserves_the_existing_regex_matches():
    from chartcleaner.section_parser import _COMBINED_SECTION_RE, SECTION_TAXONOMY
    old_parts = []
    for n, alias in enumerate((a for aliases in SECTION_TAXONOMY.values() for a in aliases), 1):
        old_parts.append(rf"(?P<sec_{n}>^[ \t]*(?:#+[ \t]*|\*\*)?(?:\b{alias}\b)(?:\*\*)?[ \t]*(?::[ \t]*$|:\s+[^\n]+$|$))")
    old = re.compile("|".join(old_parts), re.I | re.M)
    texts = ["text " * 500 + "\n## Labs:\nNa 140\n**Assessment and Plan**: No fever\n",
             "Labs:\nNa 140\ninline Plan: none\n\tVitals: HR 80\nAllergies\nnone",
             "No fever", "Plan:\n\nNo fever", "HPI\nName: Test\nMed List\naspirin 81 mg"]
    for text in texts:
        key = lambda pattern: [(m.span(), m.lastgroup, m.group()) for m in pattern.finditer(text)]
        assert key(old) == key(_COMBINED_SECTION_RE)


def test_benchmark_preserves_timing_and_reuses_warm_cache(monkeypatch):
    from scripts import benchmark_validation as bench
    calls = []
    def run(cfg, text, cache, *, measure_memory=False):
        calls.append((cache, measure_memory))
        return {"ms": 999 if measure_memory else 7, "cpu_ms": 888 if measure_memory else 5,
                "peak_mb": 4 if measure_memory else None, "cache_hits": len(calls), "cache_misses": 0}
    monkeypatch.setattr(bench, "_run", run)
    measured = bench.measure_case(100, "shadow", 3)
    assert measured["cold"]["ms"] == measured["warm"]["ms"] == 7
    assert measured["cold"]["cpu_ms"] == measured["warm"]["cpu_ms"] == 5
    assert measured["cold"]["peak_mb"] == measured["warm"]["peak_mb"] == 4
    assert all(cache is calls[0][0] for cache, _ in calls[:4])
    assert calls[4][0] is not calls[0][0] and calls[5][0] is calls[0][0]
