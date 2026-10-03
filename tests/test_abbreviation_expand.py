"""Expand mode and section-scoped abbreviation rules."""

import pytest

from chartcleaner import service
from chartcleaner.abbreviations import abbreviate, expand, meanings, normalize_settings, with_custom
from chartcleaner.engine import Pipeline


def test_expand_spells_out_unambiguous_abbreviations():
    text, n, details = expand("Pt w/ HTN s/p CABG.")
    assert text == "Pt with hypertension status post coronary artery bypass grafting."
    assert n == 4 and details["ambiguous"] == {}


def test_expand_capitalizes_at_sentence_start():
    assert expand("HTN controlled. CABG planned.\nEVD clamped.")[0] == (
        "Hypertension controlled. Coronary artery bypass grafting planned.\nExternal ventricular drain clamped.")


def test_ambiguous_abbreviations_are_left_and_counted():
    assert len(meanings("MS")) > 1
    text, n, details = expand("MS stable, MS improving")
    assert text == "MS stable, MS improving" and n == 0
    assert details["ambiguous"] == {"MS": 2}


def test_expand_prefer_resolves_ambiguity():
    cfg = {"abbreviations": {"expand_prefer": {"MS": "mental status"}}}
    assert expand("MS stable", cfg)[0] == "Mental status stable"


def test_spelling_variants_are_not_ambiguous():
    assert expand("new SAH")[0] == "new subarachnoid hemorrhage"


@pytest.mark.parametrize("text", ["a line was drawn", "reg diet", "x 2 days", "[[T1]] token"])
def test_risky_short_forms_and_tokens_are_untouched(text):
    assert expand(text)[0] == text


def test_custom_rule_expands_and_round_trips():
    cfg = with_custom({}, "left side weakness", "LSW")
    shortened = abbreviate("left side weakness noted", cfg)[0]
    assert shortened == "LSW noted"
    assert expand(shortened, cfg)[0] == "Left side weakness noted"  # sentence start


def test_case_sensitive_matching():
    assert expand("htn")[0] == "htn"


def test_pipeline_expand_mode_and_service(tmp_path):
    result = Pipeline({}, mode="expand").run("Pt w/ HTN")
    assert result.text == "Pt with hypertension" and not result.wrapped
    out = service.expand("MS and HTN", config={}, record=False)
    assert out["text"] == "MS and hypertension"
    assert out["ambiguous"] == {"MS": 1} and out["expansions"] == {"HTN": 1}


CHART = "Medications:\nheparin for Hypertension\n\nAssessment and Plan:\nHypertension controlled\n"


def test_scope_only_named_sections():
    cfg = {"abbreviations": {"scope": {"mode": "only", "sections": ["Assessment and Plan"]}}}
    assert abbreviate(CHART, cfg)[0] == CHART.replace("Hypertension controlled", "HTN controlled")


def test_scope_except_named_sections_matches_domain_keys():
    cfg = {"abbreviations": {"scope": {"mode": "except", "sections": ["medications"]}}}
    assert abbreviate(CHART, cfg)[0] == CHART.replace("Hypertension controlled", "HTN controlled")


def test_scope_without_headers():
    only = {"abbreviations": {"scope": {"mode": "only", "sections": ["Plan"]}}}
    text, n, details = abbreviate("Hypertension", only)
    assert (text, n) == ("Hypertension", 0) and details["note"]
    except_ = {"abbreviations": {"scope": {"mode": "except", "sections": ["Plan"]}}}
    assert abbreviate("Hypertension", except_)[0] == "HTN"


def test_scoped_change_positions_point_into_full_text():
    cfg = {"abbreviations": {"scope": {"mode": "only", "sections": ["Assessment and Plan"]}}}
    changes = []
    abbreviate(CHART, cfg, changes=changes)
    assert len(changes) == 1
    c = changes[0]
    assert CHART[c["start"]:c["start"] + len(c["before"])] == "Hypertension"
    assert c["line"] == 5


def test_normalize_settings_keeps_new_keys_and_drops_empty():
    group = normalize_settings({
        "scope": {"mode": "only", "sections": ["Plan", ""]},
        "expand_prefer": {"MS": "mental status", "": "x"},
        "rejected_suggestions": ["Left Side", "left side", " "],
    })
    assert group["scope"] == {"mode": "only", "sections": ["Plan"]}
    assert group["expand_prefer"] == {"MS": "mental status"}
    assert group["rejected_suggestions"] == ["left side"]
    assert normalize_settings({"scope": {"mode": "all", "sections": ["Plan"]}}) == {"disabled": [], "custom": []}
