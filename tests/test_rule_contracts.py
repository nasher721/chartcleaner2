"""Rule contracts (``rule_contracts`` + the scope check): whole-match scope,
unknown sections authorize nothing, identity keyed on pattern *and*
replacement (legacy ids untouched), and review that import/edit cannot grant."""

from __future__ import annotations

import re

import pytest

from chartcleaner import rule_contracts, rule_sharing
from chartcleaner.edit_provenance import EditLedger
from chartcleaner.engine import Pipeline, load_default_config, validate_config
from chartcleaner.rule_contracts import (
    ContractExample,
    RuleContract,
    contract_key,
    load_contracts,
    mark_unreviewed,
    remove_contract,
    set_contract,
)
from chartcleaner.stage_core import rule_id
from chartcleaner.validation import ValidationContext, validate_proposal

CHART = ("Objective: Lungs clear. Click to add.\n"
         "Allergies: penicillin (rash).\n"
         "Plan: PT/OT.\n")


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "wrap_output": False, "duplicate_note_detection": {"enabled": False},
           "fuzzy_dedup": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}}}
    cfg.update(over)
    return cfg


def _ctx(source: str, cfg: dict | None = None) -> ValidationContext:
    return ValidationContext.for_source(source, config=cfg or {}, ledger=EditLedger(source), mode="shadow")


# --- the five plan sentinels --------------------------------------------------

def test_match_start_in_scope_but_end_outside_is_rejected():
    ctx = _ctx(CHART)
    start = CHART.index("Click to add.")
    end = CHART.index("(rash)")  # runs from Social History into Allergies
    p = ctx.ledger.propose("boilerplate", "k", start, end, "")
    contract = RuleContract(key="k", operation="remove_boilerplate", sections=("physical_exam",))
    report = validate_proposal(p, contract, ctx)
    assert report.decision == "retain_original"
    codes = [i.code for i in report.issues]
    assert "scope_violation" in codes
    inside = ctx.ledger.propose("boilerplate", "k", start, start + len("Click to add."), "")
    ok = validate_proposal(inside, contract, ctx)
    assert "scope_violation" not in [i.code for i in ok.issues]


def test_unknown_section_cannot_authorize_scoped_removal():
    text = "Lungs clear. Click to add.\nPlan: PT/OT.\n"  # no recognisable section header first
    ctx = _ctx(text)
    start = text.index("Click to add.")
    p = ctx.ledger.propose("boilerplate", "k", start, start + len("Click to add."), "")
    contract = RuleContract(key="k", operation="remove_boilerplate", sections=("physical_exam",))
    report = validate_proposal(p, contract, ctx)
    assert any(i.code == "scope_violation" for i in report.issues)
    assert report.decision == "retain_original"


def test_identical_pattern_different_replacement_has_distinct_contract():
    flags = re.IGNORECASE
    a = contract_key("literal_replacements", r"\bq\.?d\.?\b", "daily", flags)
    b = contract_key("literal_replacements", r"\bq\.?d\.?\b", "", flags)
    assert a != b
    assert contract_key("boilerplate", "x", "", flags) != contract_key("metadata_lines", "x", "", flags)
    assert contract_key("boilerplate", "x", "", re.IGNORECASE) != contract_key("boilerplate", "x", "", 0)
    # The legacy id (history, UI, rule_hits) is still the pattern hash alone.
    assert rule_id(r"\bq\.?d\.?\b") == rule_id(r"\bq\.?d\.?\b")
    assert len(rule_id("x")) == 10 and rule_id("x") not in (a, b)


def test_import_cannot_mark_contract_reviewed():
    cfg = set_contract(base(learned_rules=[["Click to add\\.", ""]]), "learned_rules",
                       "Click to add\\.", "", re.IGNORECASE, operation="remove_boilerplate",
                       sections=("physical_exam",), reviewed=True,
                       examples=(ContractExample("Click to add.", ""),
                                 ContractExample("Click here to add ROS", "Click here to add ROS", "keep")))
    key = next(iter(load_contracts(cfg)))
    assert load_contracts(cfg)[key].reviewed is True
    payload = rule_sharing.payload_from_config(cfg, complete=True)
    assert payload["rule_contracts"][key]["reviewed"] is True, "the exporter copies the flag"
    assert "examples" not in payload["rule_contracts"][key], "private examples never leave the machine"
    imported = rule_sharing.apply_import(base(), payload, mode="merge")
    contracts = load_contracts(imported)
    assert key in contracts
    assert contracts[key].reviewed is False, "review is granted locally, never by a file"
    assert contracts[key].examples == ()
    # A hand-edited payload claiming review is treated the same way.
    payload["rule_contracts"][key]["reviewed"] = True
    assert load_contracts(rule_sharing.apply_import(base(), payload))[key].reviewed is False


# --- sidecar round trip --------------------------------------------------------

def test_contract_sidecar_round_trips_and_validates():
    cfg = set_contract(base(boilerplate=[r"Click to add\."]), "boilerplate", r"Click to add\.", "",
                       re.IGNORECASE | re.DOTALL | re.MULTILINE, operation="remove_boilerplate",
                       sections=("physical_exam",), note_types=("progress_note",))
    contracts = load_contracts(cfg)
    assert len(contracts) == 1
    c = next(iter(contracts.values()))
    assert c.operation == "remove_boilerplate" and c.sections == ("physical_exam",)
    assert c.allow_cross_section is False and c.reviewed is False
    assert c.required_checks == ("facts", "meaning", "scope", "statements", "do_not_use")
    errors, warnings = validate_config({**load_default_config(), "rule_contracts": cfg["rule_contracts"]})
    assert not errors, errors
    bad = {**load_default_config(), "rule_contracts": 7}
    assert any("rule_contracts" in e for e in validate_config(bad)[0])
    bad = {**load_default_config(), "rule_contracts": {"schema_version": 1, "contracts": {
        "abc": {"operation": "delete_everything", "sections": "social_history"}}}}
    errors, _ = validate_config(bad)
    assert any("operation" in e for e in errors) and any("sections" in e for e in errors)
    assert "rule_contracts" not in base(), "the sidecar is only written when a contract is saved"
    assert "rule_contracts" not in remove_contract(cfg, c.key)


def test_contract_is_found_at_run_time_by_its_key():
    pattern = r"Click to add\."
    cfg = set_contract(base(boilerplate=[pattern]), "boilerplate", pattern, "",
                       re.IGNORECASE | re.DOTALL | re.MULTILINE, operation="remove_boilerplate",
                       sections=("allergies",))  # wrong section on purpose
    result = Pipeline(cfg).run(CHART)
    props = [p for p in result.ledger.proposals if p.stage_id == "boilerplate"]
    assert props and props[0].rule_key in load_contracts(cfg)
    assert any(i.code == "scope_violation" for i in result.validation.issues)
    assert result.text == Pipeline({**cfg, "rule_contracts": None}).run(CHART).text, "shadow"


def test_case_sensitive_option_changes_the_effective_flags_and_key():
    pattern = r"Click to add\."
    cfg = base(boilerplate=[pattern])
    cfg["stage_options"]["boilerplate"] = {"case_sensitive": True}
    cfg = set_contract(cfg, "boilerplate", pattern, "", None, operation="remove_boilerplate")
    key = next(iter(load_contracts(cfg)))
    assert key == contract_key("boilerplate", pattern, "", re.DOTALL | re.MULTILINE)
    result = Pipeline(cfg).run(CHART)
    assert [p.rule_key for p in result.ledger.proposals if p.stage_id == "boilerplate"] == [key]


def test_edit_and_mark_unreviewed():
    cfg = set_contract(base(), "learned_rules", "a", "", re.IGNORECASE, operation="remove_boilerplate",
                       reviewed=True)
    key = next(iter(load_contracts(cfg)))
    assert load_contracts(mark_unreviewed(cfg))[key].reviewed is False
    moved = rule_contracts.on_rule_edited(cfg, "learned_rules", ["a", ""], ["b", ""])
    new_key = contract_key("learned_rules", "b", "", re.IGNORECASE)
    assert key not in load_contracts(moved) and load_contracts(moved)[new_key].reviewed is False
    assert load_contracts(moved)[new_key].operation == "remove_boilerplate"


def test_legacy_rules_without_contracts_keep_their_default_behaviour():
    cfg = base(boilerplate=[r"Click to add\."])
    result = Pipeline(cfg).run(CHART)
    assert "Click to add" not in result.text and "penicillin (rash)" in result.text
    assert not any(i.code in ("scope_violation", "cross_section") for i in result.validation.issues)


def test_cross_section_match_without_sections_is_flagged_for_explicit_contracts_only():
    ctx = _ctx(CHART)
    start = CHART.index("Click to add.")
    end = CHART.index("(rash)")
    p = ctx.ledger.propose("boilerplate", "k", start, end, "")
    explicit = RuleContract(key="k", operation="remove_boilerplate")
    assert any(i.code == "cross_section" for i in validate_proposal(p, explicit, ctx).issues)
    legacy = rule_contracts.default_contract("boilerplate", "k")
    assert not any(i.code == "cross_section" for i in validate_proposal(p, legacy, ctx).issues)


def test_rule_history_journals_contract_changes_and_reverts_them():
    from chartcleaner import rule_history
    before = base()
    after = set_contract(before, "learned_rules", "a", "", re.IGNORECASE, operation="redact")
    changes = rule_history.diff(before, after)
    assert any(c["kind"] == "contract" for c in changes)
    assert "contract" in rule_history.summarize(changes).lower()
    reverted = rule_history.revert(after, {"changes": changes})
    assert load_contracts(reverted) == {}


def test_unverified_deletion_waits_for_review_only_under_an_explicit_contract():
    text = "Chart reviewed.\nPlan: PT/OT. Na 141.\n"
    ctx = _ctx(text)
    p = ctx.ledger.propose("boilerplate", "k", 0, len("Chart reviewed.\n"), "")
    explicit = RuleContract(key="k", operation="remove_boilerplate")
    report = validate_proposal(p, explicit, ctx)
    assert report.coverage.status == "partial" and report.decision == "retain_original", \
        "a newly contracted destructive rule earns acceptance of unverified deletions by review"
    reviewed = RuleContract(key="k", operation="remove_boilerplate", reviewed=True)
    assert validate_proposal(p, reviewed, ctx).decision == "accept"
    legacy = rule_contracts.default_contract("boilerplate", "k")
    legacy_report = validate_proposal(p, legacy, ctx)
    assert legacy_report.decision == "accept", "a legacy rule has no review to wait for"
    assert legacy_report.coverage.status == "partial", "...but its coverage still says so"
    # A definite finding stops every kind of contract.
    q = ctx.ledger.propose("boilerplate", "k", 0, len(text), "")
    assert validate_proposal(q, legacy, ctx).decision == "retain_original"


def test_signed_export_covers_contract_metadata():
    pytest.importorskip("cryptography")
    from chartcleaner import rule_signing
    cfg = set_contract(base(), "learned_rules", "a", "", re.IGNORECASE, operation="redact")
    key = next(iter(load_contracts(cfg)))
    signed = rule_signing.sign(rule_sharing.payload_from_config(cfg, complete=True), "me")
    assert rule_signing.verify(signed).ok
    tampered = {**signed, "rule_contracts": {key: {**signed["rule_contracts"][key], "operation": "format"}}}
    assert not rule_signing.verify(tampered).ok, "a changed contract breaks the signature"
