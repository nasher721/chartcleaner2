"""Full-pipeline rule trials (``rule_preview.trial_pipeline``): a rule that is
harmless on its own but exposes a finding to a downstream rule is caught, a
failed mandatory counterexample blocks promotion, and known-good outputs are
never auto-approved."""

from __future__ import annotations

import re

import pytest

from chartcleaner import regression_set, rule_preview
from chartcleaner.regression_set import KnownGood
from chartcleaner.rule_contracts import ContractExample, RuleContract, set_contract, load_contracts
from chartcleaner.rule_preview import PipelineTrialReport, trial_pipeline

# A sloppy existing boilerplate rule: it removes the signature line *and the
# line after it* — which only ever was the footer, until a new rule removes
# the footer first.
SLOPPY = r"^Signed electronically\.\n[^\n]*\n"
TEXT = ("Signed electronically.\nFooter: Epic Hyperspace\n"
        "GCS 15, no acute infarct on CT.\nPlan: PT\n")


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [], "boilerplate": [SLOPPY], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "wrap_output": False, "duplicate_note_detection": {"enabled": False},
           "fuzzy_dedup": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}}}
    cfg.update(over)
    return cfg


def test_rule_interaction_deletes_newly_exposed_finding():
    report = trial_pipeline(base(), "metadata_lines", r"^Footer:.*\n", [TEXT], known_good=[])
    assert isinstance(report, PipelineTrialReport)
    assert report.charts_checked == 1 and report.charts_changed == 1
    # On its own the new rule removes only the footer (no clinical value)...
    assert report.direct_facts_lost == 0
    # ...but the full pipeline now loses GCS 15, through the *other* stage.
    assert report.interactions and report.interactions[0]["stage_id"] == "boilerplate"
    assert report.interactions[0]["facts_lost"] == 1
    assert report.promotable is False and report.needs_review is True
    summary = report.summary()
    assert "GCS" not in str(summary) and "Epic" not in str(summary), "summaries are text-free"
    assert summary["interactions"] == 1


def test_same_rule_without_the_sloppy_downstream_rule_is_clean():
    report = trial_pipeline(base(boilerplate=[]), "metadata_lines", r"^Footer:.*\n", [TEXT], known_good=[])
    assert report.charts_changed == 1 and not report.interactions
    assert report.promotable is True
    assert report.needs_review is True, "a difference alone still asks for a look"


def test_missing_mandatory_examples_block_a_destructive_contracted_rule():
    cfg = set_contract(base(boilerplate=[]), "metadata_lines", r"^Footer:.*\n", "",
                       re.IGNORECASE | re.MULTILINE, operation="remove_boilerplate")
    contract = next(iter(load_contracts(cfg).values()))
    report = trial_pipeline(base(boilerplate=[]), "metadata_lines", r"^Footer:.*\n", [TEXT],
                            contract=contract, known_good=[])
    reasons = {f["reason"] for f in report.contract_failures}
    assert "missing_match_example" in reasons and "missing_keep_example" in reasons
    assert report.promotable is False


def test_failed_counterexample_prevents_promotion_and_passing_ones_allow_it():
    examples = (ContractExample("Footer: Epic Hyperspace\n", ""),
                ContractExample("Footnote: see below\n", "Footnote: see below\n", "keep"))
    good = RuleContract(key="k", operation="remove_boilerplate", examples=examples)
    report = trial_pipeline(base(boilerplate=[]), "metadata_lines", r"^Footer:.*\n", [TEXT],
                            contract=good, known_good=[])
    assert not report.contract_failures and report.promotable
    # A keep example the rule *does* change is a failed counterexample.
    bad = RuleContract(key="k", operation="remove_boilerplate", examples=(
        examples[0], ContractExample("Footer: keep this\n", "Footer: keep this\n", "keep")))
    report = trial_pipeline(base(boilerplate=[]), "metadata_lines", r"^Footer:.*\n", [TEXT],
                            contract=bad, known_good=[])
    assert [f["reason"] for f in report.contract_failures] == ["keep_example_changed"]
    assert report.promotable is False
    # ...and so is a match example the rule leaves alone or changes differently.
    wrong = RuleContract(key="k", operation="remove_boilerplate", examples=(
        ContractExample("Footer: x\n", "Footer: x\n"), examples[1]))
    report = trial_pipeline(base(boilerplate=[]), "metadata_lines", r"^Footer:.*\n", [TEXT],
                            contract=wrong, known_good=[])
    assert [f["reason"] for f in report.contract_failures] == ["match_example_mismatch"]


def test_known_good_differences_are_reported_never_approved():
    kg = KnownGood(id="kg1", label="x", ts="", input=TEXT,
                   expected="Footer: Epic Hyperspace\nGCS 15, no acute infarct on CT.\nPlan: PT")
    report = trial_pipeline(base(), "metadata_lines", r"^Footer:.*\n", [], known_good=[kg])
    assert report.regressions_checked == 1 and report.regression_differences == 1
    assert report.needs_review is True
    assert kg.expected.startswith("Footer"), "the stored expectation is untouched"
    assert regression_set.list_ids() == []


def test_formatting_contract_is_not_asked_for_counterexamples():
    contract = RuleContract(key="k", operation="format")
    report = trial_pipeline(base(boilerplate=[]), "literal_replacements", ["  +", " "], [TEXT],
                            contract=contract, known_good=[])
    assert not report.contract_failures


def test_quick_preview_is_unchanged():
    impact = rule_preview.preview(base(boilerplate=[]), "metadata_lines", r"^Footer:.*\n", [TEXT])
    assert impact.charts_changed == 1 and impact.lines_changed == 1


def test_trial_report_counts_new_validation_issues_by_code_and_stage():
    report = trial_pipeline(base(), "metadata_lines", r"^Footer:.*\n", [TEXT], known_good=[])
    assert report.new_issues.get("fact_lost", 0) >= 1
    assert "boilerplate" in report.new_issue_stages


def test_editor_promotion_gate_blocks_only_contracted_rules_with_failures():
    from chartcleaner.learned_editor import promotion_blocker
    pair = [r"Footer: Epic Hyperspace", ""]
    plain = base(boilerplate=[], learned_rules=[pair])
    assert promotion_blocker(plain, pair) is None, "no contract: saved as before"
    contracted = set_contract(plain, "learned_rules", pair[0], pair[1], None, operation="remove_boilerplate")
    blocker = promotion_blocker(contracted, pair)
    assert blocker and "contract example" in blocker
    ok = set_contract(plain, "learned_rules", pair[0], pair[1], None, operation="remove_boilerplate",
                      examples=(ContractExample("Footer: Epic Hyperspace", ""),
                                ContractExample("Footnote: keep", "Footnote: keep", "keep")))
    assert promotion_blocker(ok, pair) is None
