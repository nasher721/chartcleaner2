"""Learned rules: the highlight-text → remembered-rule stage.

Covers the pattern builder on the app side and the engine stage that applies
config["learned_rules"] on every run.
"""

import re

import app as cc_app
from chartcleaner.engine import BUILTIN_STAGE_IDS, Pipeline, clean_text, validate_config


def base(**over) -> dict:
    cfg = {
        "emr_line_metadata": [],
        "boilerplate": [],
        "epic_phi_patterns": [],
        "literal_replacements": [],
        "clinical_headers": [],
        "nlp_redaction": {"enabled": False},  # deterministic tests: no model stage
    }
    cfg.update(over)
    return cfg


def clean(text: str, cfg: dict) -> str:
    return clean_text(text, cfg, wrap=False).text


# -- pattern builder (app.py helper) ---------------------------------------------

def test_remove_text_pattern_escapes_regex_characters():
    pat = cc_app.learned_pattern("Cost $5.00 (approx)", cc_app.LEARN_REMOVE_TEXT)
    text = "Cost $5.00 (approx) was noted."
    assert re.subn(pat, "", text, flags=re.IGNORECASE)[0] == " was noted."


def test_remove_text_pattern_joins_multiline_selection():
    pat = cc_app.learned_pattern("line one\nline two", cc_app.LEARN_REMOVE_TEXT)
    text = "start\nline one\nline two\nend"
    assert re.subn(pat, "", text, flags=re.IGNORECASE)[0] == "start\n\nend"


def test_remove_lines_pattern_anchors_and_swallows_newline():
    pat = cc_app.learned_pattern("Editor: John Smith", cc_app.LEARN_REMOVE_LINES)
    text = "keep\nEditor: John Smith\nalso keep"
    assert re.subn(pat, "", text, flags=re.IGNORECASE)[0] == "keep\nalso keep"
    # a mid-line occurrence is not a whole line — untouched
    text2 = "see Editor: John Smith here"
    assert re.subn(pat, "", text2, flags=re.IGNORECASE)[0] == text2


def test_remove_lines_pattern_handles_indentation_and_crlf():
    pat = cc_app.learned_pattern("QA by Dr. Who", cc_app.LEARN_REMOVE_LINES)
    text = "  QA by Dr. Who  \nnext"
    assert re.subn(pat, "", text, flags=re.IGNORECASE)[0] == "next"


# -- engine stage ----------------------------------------------------------------

def test_learned_rules_removed_on_every_run():
    cfg = base(learned_rules=[["quiet hours testing\\.", ""]])
    text = "The quiet hours testing. line sticks around twice: quiet hours testing."
    assert clean(text, cfg) == "The  line sticks around twice:"


def test_learned_rules_replacement():
    cfg = base(learned_rules=[["hypoglycemia", "low glucose"]])
    assert clean("hypoglycemia overnight", cfg) == "low glucose overnight"


def test_learned_rules_match_case_insensitively_by_default():
    cfg = base(learned_rules=[["imp: impression:", "Impression:"]])
    assert clean("IMP: IMPRESSION: pneumonia", cfg) == "Impression: pneumonia"


def test_learned_rules_run_in_stage_order_after_literal_replacements():
    cfg = base(
        literal_replacements=[["\\bTELEMETRY\\b", "Tele"]],
        learned_rules=[["Tele alarms", ""]],
    )
    # learned stage sees the already-replaced text (whitespace final-trim drops the gap)
    assert clean("TELEMETRY alarms loud", cfg) == "loud"
    stages = [s.id for s in Pipeline(cfg, custom_dir=None).stages]
    assert stages.index("learned_rules") == stages.index("literal_replacements") + 1


def test_learned_rules_stage_registered_as_18th_builtin():
    assert "learned_rules" in BUILTIN_STAGE_IDS
    assert len(BUILTIN_STAGE_IDS) == 18


def test_missing_or_empty_learned_rules_is_noop():
    text = "nothing changes here"
    assert clean(text, base()) == text
    assert clean(text, base(learned_rules=[])) == text


def test_default_config_ships_empty_learned_rules():
    from chartcleaner.engine import load_default_config
    assert load_default_config().get("learned_rules") == []


def test_validator_accepts_learned_rules_and_rejects_bad_pairs():
    errs, _ = validate_config(base(learned_rules=[["ok", ""]]))
    assert not errs
    errs, _ = validate_config(base(learned_rules=[["only-pattern"]]))
    assert any("learned_rules" in e for e in errs)


def test_validator_tolerates_old_configs_without_the_key():
    errs, _ = validate_config(base())
    assert not any("learned_rules" in e for e in errs)
