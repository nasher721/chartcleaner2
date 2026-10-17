"""Rule shapes (line / line start / block / placeholders) and per-rule settings."""

from __future__ import annotations

import re

import pytest

from chartcleaner import quick_rules, rule_examples, rule_meta, rule_shapes
from chartcleaner.engine import Pipeline, load_default_config, validate_config
from chartcleaner.highlight_rules import make_rule
from chartcleaner.regex_risk import risks
from chartcleaner.stage_core import rule_id
from chartcleaner.stages import run_learned

SIGNED = "Electronically signed by Jane Doe, MD on 10/14/2026 08:12"
CHART = """Progress Note
BP 120/80 HR 72
Electronically signed by John Smith, MD on 10/15/2026 09:30 AM
Plan:
Continue lisinopril 10 mg daily.
Electronically signed by Jane Doe, MD on 10/14/2026 08:12
"""


@pytest.fixture()
def cfg():
    out = load_default_config()
    out["nlp_redaction"] = {"enabled": False}
    out["audit"] = {"enabled": False}
    return out


def _apply(shape: dict, text: str) -> str:
    pattern, repl = rule_shapes.build(shape)
    return re.sub(pattern, repl, text, flags=re.IGNORECASE)


# ---- building ----------------------------------------------------------------

def test_plain_text_shape_is_the_highlight_rule():
    assert rule_shapes.build({"kind": "text", "text": "Printed on"}) == make_rule("Printed on")
    assert rule_shapes.build({"kind": "text", "text": "pt", "replacement": "patient",
                              "case_sensitive": True}) == make_rule("pt", "patient", case_sensitive=True)


def test_whole_line_removes_the_line_and_its_break():
    out = _apply({"kind": "line", "text": "signed by"}, CHART)
    assert "signed" not in out and "BP 120/80 HR 72\nPlan:" in out


def test_line_start_only_matches_at_the_start():
    text = "Printed by Epic 10/1\nThe note was Printed by Epic\n"
    assert _apply({"kind": "line_start", "text": "Printed by"}, text) == "The note was Printed by Epic\n"


def test_until_blank_line_heading_and_marker():
    text = "Plan:\nrest\nPatient Education\nhandout given\nvideo\n\nAssessment:\nstable\n"
    assert _apply({"kind": "until", "text": "Patient Education", "until": "blank_line"}, text) \
        == "Plan:\nrest\n\n\nAssessment:\nstable\n"
    assert _apply({"kind": "until", "text": "Patient Education", "until": "next_heading"},
                  text.replace("\n\n", "\n")) == "Plan:\nrest\n\nAssessment:\nstable\n"
    assert _apply({"kind": "until", "text": "Patient Education", "until": {"marker": "video"}}, text) \
        == "Plan:\nrest\nvideo\n\nAssessment:\nstable\n"


def test_until_never_takes_more_than_the_cap():
    text = "Patient Education\n" + "x" * (rule_shapes.MAX_BLOCK_CHARS + 50)
    assert _apply({"kind": "until", "text": "Patient Education", "until": {"marker": "STOP"}}, text) == text


def test_template_placeholders_match_look_alikes():
    shape = {"kind": "line", "text": "Electronically signed by {name}, MD on {date} {time}",
             "template": True}
    out = _apply(shape, CHART)
    assert "Electronically" not in out and "lisinopril 10 mg" in out


def test_name_placeholder_stays_capitalized_in_case_insensitive_rules():
    shape = {"kind": "text", "text": "seen by {name}", "template": True}
    assert _apply(shape, "seen by Jane Doe") == ""
    assert _apply(shape, "seen by the team") == "seen by the team"


def test_case_sensitive_shapes():
    shape = {"kind": "line", "text": "NOTE", "case_sensitive": True}
    assert _apply(shape, "NOTE x\nnote y\n") == "note y\n"


@pytest.mark.parametrize("shape", [
    {"kind": "line", "text": ""}, {"kind": "bogus", "text": "x"},
    {"kind": "line", "text": "x", "replacement": "y"},
    {"kind": "until", "text": "x", "until": "forever"},
    {"kind": "until", "text": "x", "until": {"marker": " "}},
])
def test_bad_shapes_are_refused(shape):
    with pytest.raises(ValueError):
        rule_shapes.build(shape)


def test_template_flag_without_placeholders_is_literal():
    assert rule_shapes.normalize({"text": "a {b}", "template": True})["template"] is False


def test_describe_reads_like_a_sentence():
    assert rule_shapes.describe({"kind": "line_start", "text": "Printed by"}) \
        == "Remove lines starting with “Printed by”"
    assert "up to the next heading" in rule_shapes.describe(
        {"kind": "until", "text": "Patient Education", "until": "next_heading"})


# ---- generalize ---------------------------------------------------------------

def test_generalize_offers_narrow_to_broad_rules():
    cands = rule_shapes.generalize(SIGNED)
    texts = [c.shape["text"] for c in cands]
    assert texts[0] == SIGNED
    assert "Electronically signed by Jane Doe, MD on {date} {time}" in texts
    assert "Electronically signed by {name}, MD on {date} {time}" in texts
    assert any(c.shape["kind"] == "line_start" and c.shape["text"] == "Electronically signed by"
               for c in cands)
    assert len({c.pattern for c in cands}) == len(cands)


def test_generalized_rules_never_risk_backtracking():
    for text in (SIGNED, "Page 1 of 3", "Reviewed by Smith, John on 2026-10-14", "Printed 10/14/26 8:12 PM"):
        for cand in rule_shapes.generalize(text):
            assert not risks(cand.pattern), cand.pattern
            assert re.search(cand.pattern, text, re.IGNORECASE), cand.label


def test_generalize_multiline_offers_blocks():
    cands = rule_shapes.generalize("Patient Education\nhandout given")
    kinds = [c.shape["kind"] for c in cands]
    assert kinds[0] == "text" and kinds.count("until") == 2


def test_generalize_empty():
    assert rule_shapes.generalize("  ") == []


# ---- per-rule settings ----------------------------------------------------------

def test_add_rule_records_shape_source_and_enables_stage(cfg):
    shape = rule_shapes.normalize({"kind": "line", "text": "signed by"})
    pair = rule_shapes.build(shape)
    cfg["stage_options"] = {"learned_rules": {"enabled": False}}
    out, added = rule_meta.add_rule(cfg, pair, shape=shape, source="builder")
    assert added and out["learned_rules"][-1] == pair
    info = rule_meta.entry(out, pair[0])
    assert info["shape"] == shape and info["source"] == "builder" and info["added"]
    assert out["stage_options"]["learned_rules"]["enabled"] is True
    assert validate_config(out)[0] == []
    again, added = rule_meta.add_rule(out, pair, shape=shape)
    assert not added and again["learned_rules"] == out["learned_rules"]


def test_add_rule_refuses_a_different_replacement(cfg):
    cfg["learned_rules"] = [make_rule("pt", "patient")]
    with pytest.raises(ValueError):
        rule_meta.add_rule(cfg, make_rule("pt", "pt."))


def test_paused_rules_are_skipped(cfg):
    pair = make_rule("Printed on")
    cfg["learned_rules"] = [pair]
    assert run_learned("x Printed on y", cfg, None)[0] == "x  y"
    paused = rule_meta.set_enabled(cfg, [pair[0]], False, archived=True)
    assert rule_meta.entry(paused, pair[0]) == {"enabled": False, "archived": True}
    assert run_learned("x Printed on y", paused, None)[0] == "x Printed on y"
    resumed = rule_meta.set_enabled(paused, [pair[0]], True)
    assert rule_meta.entry(resumed, pair[0]) == {}
    assert "rule_meta" not in resumed["stage_options"]["learned_rules"]


def test_per_rule_section_scope(cfg):
    text = "Medications:\nnoted per pharmacy\n\nAssessment and Plan:\nnoted per pharmacy\n"
    pair = make_rule("noted per pharmacy")
    cfg["learned_rules"] = [pair]
    cfg = rule_meta.update(cfg, pair[0], scope={"mode": "only", "sections": ["medications"]})
    out = run_learned(text, cfg, None)[0]
    assert out.count("noted per pharmacy") == 1 and out.index("noted") > out.index("Assessment")


def test_paused_rule_in_full_pipeline(cfg):
    pair = make_rule("Printed on")
    cfg["learned_rules"] = [pair]
    cfg = rule_meta.set_enabled(cfg, [pair[0]], False)
    assert "Printed on" in Pipeline(cfg).run("BP 120/80\nPrinted on 10/14\n").text


def test_prune_drops_settings_of_deleted_rules(cfg):
    pair = make_rule("Printed on")
    cfg["learned_rules"] = [pair]
    cfg = rule_meta.update(cfg, pair[0], note="footer")
    cfg["learned_rules"] = []
    assert validate_config(cfg)[1]  # orphan warning
    assert rule_meta.meta_map(rule_meta.prune(cfg)) == {}


def test_update_rejects_unknown_fields(cfg):
    with pytest.raises(ValueError):
        rule_meta.update(cfg, "x", colour="red")


@pytest.mark.parametrize("info, needle", [
    ({"enabled": "no"}, "enabled"), ({"tags": "a"}, "tags"),
    ({"scope": {"mode": "inside"}}, "scope"), ({"shape": {"kind": "nope", "text": "x"}}, "shape"),
])
def test_validator_checks_rule_meta(cfg, info, needle):
    pair = make_rule("x")
    cfg["learned_rules"] = [pair]
    cfg["stage_options"] = {"learned_rules": {"rule_meta": {rule_id(pair[0]): info}}}
    errors, _ = validate_config(cfg)
    assert any(needle in e for e in errors)


# ---- plain-text round trip -------------------------------------------------------

def test_text_export_round_trips_shapes_scope_and_paused(cfg):
    items = [
        {"kind": "line_start", "text": "Printed by"},
        {"kind": "line", "text": "Signed by {name} on {date}", "template": True},
        {"kind": "until", "text": "Patient Education", "until": "next_heading",
         "scope": {"mode": "only", "sections": ["assessment_and_plan"]}},
        {"kind": "until", "text": "Disclaimer", "until": {"marker": "End, of note"}},
        {"kind": "text", "text": "Exact", "case_sensitive": True, "enabled": False},
    ]
    out, report = quick_rules.add_shapes(cfg, items)
    assert len(report.added) == 5 and validate_config(out)[0] == []
    text = quick_rules.export_text(out)
    assert "Printed by  # [line starts with]" in text
    assert "Signed by {name} on {date}  # [whole line, template]" in text
    assert "only in: assessment_and_plan" in text and "until “End, of note”" in text
    assert "Exact  # [match case, paused]" in text

    back, reports, errors = quick_rules.apply_text(load_default_config(), text)
    assert errors == [] and len(reports["remove"].added) == 5
    assert back["learned_rules"] == out["learned_rules"]
    for pattern, _r in out["learned_rules"]:
        a, b = rule_meta.entry(out, pattern), rule_meta.entry(back, pattern)
        assert {k: a.get(k) for k in ("shape", "scope", "enabled")} == \
               {k: b.get(k) for k in ("shape", "scope", "enabled")}


def test_quick_add_records_its_source(cfg):
    out, _ = quick_rules.add_removals(cfg, ["Printed on"])
    assert rule_meta.entry(out, make_rule("Printed on")[0])["source"] == "quick_add"


# ---- keep examples -------------------------------------------------------------

def test_keep_examples_fail_when_a_rule_eats_them(cfg):
    pair = make_rule("Printed on")
    cfg["learned_rules"] = [pair]
    rule_examples.record_keep(pair, "Medication list printed today")
    assert rule_examples.check(cfg)["failures"] == []
    broad = rule_shapes.build({"kind": "line", "text": "printed"})
    cfg["learned_rules"].append(broad)
    failures = rule_examples.check(cfg)["failures"]
    assert len(failures) == 1 and failures[0]["kind"] == "keep"
    assert len(rule_examples.for_rule(pair[0])) == 1
    assert rule_examples.forget(pair[0]) == 1 and rule_examples.load() == []


def test_paused_rule_examples_are_skipped(cfg):
    pair = make_rule("Printed on")
    cfg["learned_rules"] = [pair]
    rule_examples.record(pair, "x Printed on y", cfg)
    paused = rule_meta.set_enabled(cfg, [pair[0]], False)
    assert rule_examples.check(paused)["checked"] == 0
