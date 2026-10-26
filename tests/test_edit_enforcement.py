"""Transactional enforcement (``cleaning_validation.mode = enforce``): a bad
edit is rejected while its neighbour is applied, downstream stages read the
retained text, warm and cold runs agree, a crashed validator retains the
original, a failed mandatory redaction withholds release, and a stage's name
never authorizes unrelated loss."""

from __future__ import annotations

import json

import pytest

from chartcleaner import validation
from chartcleaner.engine import Pipeline
from chartcleaner.rule_contracts import RuleContract
from chartcleaner.stage_cache import StageCache
from chartcleaner.validation import decide_edit

CHART = ("Progress Note\n"
         "Dictated but not read.\n"
         "Objective: Na 141 mmol/L, K 4.0 mmol/L.\n"
         "Plan: aspirin 81 mg daily.\n"
         "This message is confidential and intended only for the recipient.\n")


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "wrap_output": False, "duplicate_note_detection": {"enabled": False},
           "fuzzy_dedup": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}},
           "cleaning_validation": {"mode": "enforce", "schema_version": 1}}
    cfg.update(over)
    return cfg


def _mode(cfg: dict, mode: str) -> dict:
    return {**cfg, "cleaning_validation": {"mode": mode, "schema_version": 1}}


# One rule, two matches: the first eats a lab line (bad), the second a footer (fine).
TWO_MATCHES = base(boilerplate=[r"^(?:Objective:[^\n]*|This message is confidential[^\n]*recipient\.)\n"])


# --- the six plan sentinels ---------------------------------------------------

def test_reject_bad_edit_accept_neighboring_good_edit():
    result = Pipeline(TWO_MATCHES).run(CHART)
    assert "Na 141" in result.text, "the edit that removed a lab value was retained"
    assert "confidential" not in result.text, "its neighbour was applied"
    props = [p for p in result.ledger.proposals if p.stage_id == "boilerplate"]
    assert [p.decision for p in props] == ["retain_original", "accept"]
    stat = next(s for s in result.stages if s.id == "boilerplate")
    assert stat.matches == 1, "stage counts reflect accepted edits only"
    assert result.ledger.current == result.text
    assert result.validation.rejected == 1
    assert any(i.code in ("fact_lost", "statement_lost") and i.stage_id == "boilerplate"
               for i in result.validation.issues)
    # Shadow would have shipped both edits.
    shadow = Pipeline(_mode(TWO_MATCHES, "shadow")).run(CHART)
    assert "Na 141" not in shadow.text and shadow.validation.decision == "retain_original"


def test_downstream_rule_reads_retained_text():
    # The literal replacement only matches inside the Objective line; if the
    # boilerplate stage's bad edit were applied, it would have nothing to do.
    cfg = base(boilerplate=[r"^Objective:[^\n]*\n"], literal_replacements=[["mmol/L", "mM"]])
    result = Pipeline(cfg).run(CHART)
    assert "Na 141 mM" in result.text
    lit = next(s for s in result.stages if s.id == "literal_replacements")
    assert lit.matches == 2
    assert lit.chars_before == len(CHART), "it received the retained text, not the rejected candidate"


def test_warm_and_cold_enforcement_are_identical():
    cfg = base(boilerplate=[r"^(?:Objective:[^\n]*|This message is confidential[^\n]*recipient\.)\n"],
               literal_replacements=[["daily", "QD"]], lab_compaction={"enabled": True})
    cache = StageCache()
    cold = Pipeline(cfg).run(CHART, cache=cache)
    warm = Pipeline(cfg).run(CHART, cache=cache)
    plain = Pipeline(cfg).run(CHART)
    assert cold.text == warm.text == plain.text
    assert cold.validation.summary() == warm.validation.summary() == plain.validation.summary()
    assert [p.decision for p in cold.ledger.proposals] == [p.decision for p in warm.ledger.proposals]
    assert cache.hits >= 0  # opaque stages may hit; regex stages never cache under enforcement


def test_regex_stages_bypass_the_cache_when_validation_needs_rule_provenance():
    cache = StageCache()
    Pipeline(TWO_MATCHES).run(CHART, cache=cache)
    assert not any(key.startswith("boilerplate|") for key in cache._items), \
        "enforced regex output embodies verdicts and is never cached"
    Pipeline(_mode(TWO_MATCHES, "shadow")).run(CHART, cache=cache)
    assert not any(key.startswith("boilerplate|") for key in cache._items)


def test_validator_failure_retains_original(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(validation, "_CHECKS", {**validation._CHECKS, "facts": boom})
    cfg = base(boilerplate=[r"This message is confidential[^\n]*recipient\.\n"])
    result = Pipeline(cfg).run(CHART)
    assert "confidential" in result.text, "a crashed check keeps the passage"
    assert result.validation.coverage.status == "failed"
    assert [p.decision for p in result.ledger.proposals if p.stage_id == "boilerplate"] == ["retain_original"]
    assert result.text == CHART.rstrip("\n") or result.text == CHART


def test_required_redaction_rejection_withholds_release():
    # A PHI pattern that also swallows the lab values cannot be accepted — and
    # keeping the original leaves the identifier in place, so release is withheld.
    chart = "MRN: 12345678 Na 141 mmol/L\nPlan: PT\n"
    cfg = base(epic_phi_patterns=[[r"MRN: \d+ Na 141 mmol/L", "[REDACTED_MRN]"]])
    result = Pipeline(cfg).run(chart)
    props = [p for p in result.ledger.proposals if p.stage_id == "phi_patterns"]
    assert props and props[0].decision == "withhold_release"
    assert result.validation.decision == "withhold_release"
    assert result.validation.withheld == 1
    assert "12345678" in result.text, "the text itself is unchanged — release handling is the caller's job"
    # The same failure under a non-redaction stage only retains the passage.
    other = Pipeline(base(boilerplate=[r"MRN: \d+ Na 141 mmol/L\n"])).run(chart)
    assert other.validation.decision == "retain_original" and other.validation.withheld == 0


def test_reduction_stage_name_does_not_authorize_unrelated_loss(tmp_path):
    # A custom script called "reduce_labs" removes a dose line: its name grants
    # nothing — only an operation-specific policy can, and custom scripts have none.
    script = tmp_path / "reduce_labs.py"
    script.write_text('LABEL = "Reduce labs"\nDESCRIPTION = "x"\nPLACEHOLDER = False\n'
                      'def clean(text, ctx):\n'
                      '    ctx.count("removed", 1)\n'
                      '    return text.replace("Plan: aspirin 81 mg daily.\\n", "")\n', encoding="utf-8")
    from chartcleaner import custom_rules
    custom_rules.trust_rule(script)
    cfg = base()
    cfg["stage_order"] = ["custom:reduce_labs"]
    result = Pipeline(cfg, custom_dir=tmp_path).run(CHART)
    assert "aspirin 81 mg daily" in result.text, "the dose line was retained"
    stat = next(s for s in result.stages if s.id == "custom:reduce_labs")
    assert stat.details.get("validation") == "retained_original"
    assert any(i.code in ("fact_lost", "statement_lost") for i in result.validation.issues)
    # ...whereas a genuine by-design stage discloses its intentional losses.
    assert "statement:" in __import__("chartcleaner.rule_contracts", fromlist=["x"]).default_contract("sections").allowed_losses


def test_decide_edit_policy():
    report_bad = validation.ValidationReport(
        (validation.ValidationIssue("fact_lost", "error", "s", None, (), ""),),
        validation.Coverage.from_spans(1, [(0, 1)]), "retain_original")
    report_ok = validation.ValidationReport((), validation.Coverage.from_spans(1, [(0, 1)]), "accept")
    p = __import__("chartcleaner.edit_provenance", fromlist=["x"]).EditLedger("a").propose("s", None, 0, 1, "")
    assert decide_edit(p, report_ok, RuleContract(key="k", operation="redact")) == "accept"
    assert decide_edit(p, report_bad, RuleContract(key="k", operation="redact")) == "withhold_release"
    assert decide_edit(p, report_bad, RuleContract(key="k", operation="remove_boilerplate")) == "retain_original"


def test_off_shadow_and_enforce_differences_are_reported():
    outs = {m: Pipeline(_mode(TWO_MATCHES, m)).run(CHART) for m in ("off", "shadow", "enforce")}
    assert outs["off"].text == outs["shadow"].text != outs["enforce"].text
    assert outs["off"].validation.coverage.status == "unavailable"
    assert outs["shadow"].validation.decision == "retain_original" and outs["shadow"].validation.rejected == 0
    assert outs["enforce"].validation.rejected == 1
    for mode, res in outs.items():
        dumped = json.dumps(res.to_history_dict("t"))
        assert "Na 141" not in dumped
