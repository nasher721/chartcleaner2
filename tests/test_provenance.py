"""Stage anchors (new builtins keep their place) and change provenance."""

import json

import pytest

from chartcleaner import engine
from chartcleaner.engine import Pipeline, STAGE_ANCHORS, clean_text, rule_id



def base(**over) -> dict:
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [],
           "nlp_redaction": {"enabled": False}}
    cfg.update(over)
    cfg.setdefault("stage_options", {})["medical_abbreviations"] = {"enabled": False}
    return cfg


def _order(cfg: dict) -> list[str]:
    return [s.id for s in Pipeline(cfg).stages]


def test_missing_stage_without_anchor_is_appended_as_before():
    order = [s for s in engine.BUILTIN_STAGE_IDS if s not in {"caps_normalize", "medical_abbreviations"}]
    resolved = _order(base(stage_order=order))
    assert resolved.index("caps_normalize") > resolved.index("line_length")


def test_missing_stage_with_anchor_goes_right_after_it(monkeypatch):
    monkeypatch.setitem(STAGE_ANCHORS, "caps_normalize", "headers")
    order = [s for s in engine.BUILTIN_STAGE_IDS if s != "caps_normalize"]
    resolved = _order(base(stage_order=order))
    assert resolved.index("caps_normalize") == resolved.index("headers") + 1


def test_anchor_missing_too_falls_back_to_end(monkeypatch):
    monkeypatch.setitem(STAGE_ANCHORS, "caps_normalize", "headers")
    order = [s for s in engine.BUILTIN_STAGE_IDS if s not in {"caps_normalize", "headers"}]
    resolved = _order(base(stage_order=order))
    assert resolved.index("caps_normalize") > resolved.index("line_length")


def test_default_order_unchanged():
    assert _order(base()) == list(engine.BUILTIN_STAGE_IDS)


TEXT = "Printed by Jane on 01/02/2024\nPt has HTN.\nSigned electronically\nHypertension noted.\n"


def _cfg() -> dict:
    cfg = base(
        emr_line_metadata=[r"^Printed by .*$"],
        boilerplate=[r"^Signed electronically$"],
        literal_replacements=[[r"\bPt\b", "Patient"]],
    )
    cfg["stage_options"]["medical_abbreviations"] = {"enabled": True}
    return cfg


def test_output_identical_with_and_without_tracking():
    plain = clean_text(TEXT, _cfg(), wrap=False)
    tracked = clean_text(TEXT, _cfg(), wrap=False, track_changes=True)
    assert plain.text == tracked.text
    assert all("changes" not in s.details for s in plain.stages)


def test_tracked_changes_record_rule_text_and_line():
    stages = {s.id: s for s in clean_text(TEXT, _cfg(), wrap=False, track_changes=True).stages}
    meta = stages["metadata_lines"].details["changes"]
    assert meta == [{"rule": r"^Printed by .*$", "rule_id": rule_id(r"^Printed by .*$"),
                     "before": "Printed by Jane on 01/02/2024", "after": "", "line": 1}]
    lit = stages["literal_replacements"].details["changes"][0]
    assert (lit["before"], lit["after"]) == ("Pt", "Patient")
    abbr = stages["medical_abbreviations"].details["changes"][0]
    assert (abbr["before"], abbr["after"], abbr["source"]) == ("Hypertension", "HTN", "bundled")
    # Line numbers refer to the text as that stage saw it (blank lines were
    # already collapsed by the whitespace stage).
    assert abbr["line"] == 3


def test_backreference_replacements_are_expanded_when_tracking():
    cfg = base(literal_replacements=[[r"(\d+) mg", r"\1mg"]])
    plain = clean_text("dose 5 mg", cfg, wrap=False).text
    tracked = clean_text("dose 5 mg", cfg, wrap=False, track_changes=True)
    assert tracked.text == plain == "dose 5mg"


def test_rule_hits_always_recorded():
    stages = {s.id: s for s in clean_text(TEXT, _cfg(), wrap=False).stages}
    assert stages["metadata_lines"].details["rule_hits"] == {rule_id(r"^Printed by .*$"): 1}


def test_history_never_contains_tracked_text():
    result = clean_text(TEXT, _cfg(), wrap=False, track_changes=True)
    record = json.dumps(result.to_history_dict("test"))
    assert "Jane" not in record and "changes" not in record


def test_tracking_is_capped(monkeypatch):
    from chartcleaner import stages
    monkeypatch.setattr(stages, "MAX_TRACKED_CHANGES", 3)
    cfg = base(literal_replacements=[[r"x", "y"]])
    st = [s for s in clean_text("x" * 10, cfg, wrap=False, track_changes=True).stages
          if s.id == "literal_replacements"][0]
    assert st.matches == 10 and len(st.details["changes"]) == 3
