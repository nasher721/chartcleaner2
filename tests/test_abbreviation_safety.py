"""Do Not Use checks, preview/suggest helpers, and the matcher's blocking."""

import pytest

from chartcleaner.abbreviation_safety import check, is_blocked, needs_override, report
from chartcleaner.abbreviations import abbreviate, lookup, normalize_settings, preview, suggest, with_custom


@pytest.mark.parametrize("term,abbr", [
    ("units", "U"), ("Units", "u"), ("international units", "IU"), ("daily", "q.d."),
    ("every other day", "QOD"), ("morphine sulfate", "MS"), ("magnesium sulfate", "MgSO4"),
    ("cubic centimeters", "cc"), ("subcutaneous", "SQ"), ("subcutaneous", "sub q"),
    ("at bedtime", "HS"), ("discontinue", "D/C"), ("left ear", "AS"), ("right eye", "OD"),
    ("intranasal", "IN"), ("injection", "IJ"), ("sliding scale insulin", "SSI"),
])
def test_risky_meanings_are_blocked(term, abbr):
    assert is_blocked(abbr, term)
    assert needs_override(check(term, abbr))


@pytest.mark.parametrize("term,abbr", [
    ("internal jugular", "IJ"), ("aortic stenosis", "AS"), ("Alzheimer disease", "AD"),
    ("mental status", "MS"), ("comfort care", "CC"), ("overdose", "OD"),
    ("Supplemental Security Income", "SSI"), ("hypertension", "HTN"),
])
def test_safe_meanings_pass(term, abbr):
    assert not is_blocked(abbr, term)


def test_trailing_and_leading_zero_patterns():
    assert is_blocked("1.0 mg", "one milligram")
    assert is_blocked(".5 mg", "half a milligram")
    assert not is_blocked("0.5 mg", "half a milligram")
    assert not is_blocked("10 mg", "ten milligrams")


def test_warn_level_does_not_need_override():
    issues = check("tranexamic acid", "TXA")
    assert [i.level for i in issues] == ["warn"] and not needs_override(issues)


def test_shared_abbreviation_warns_with_other_meanings():
    issues = check("my new term", "HTN")
    assert issues[0].code == "shared" and "Hypertension" in issues[0].message


def test_bundled_do_not_use_rows_are_not_applied():
    text = "10 Units at bedtime, Subcutaneous; internal jugular line"
    assert abbreviate(text)[0] == "10 Units at bedtime, Subcutaneous; IJ line"


def test_acknowledged_custom_entry_restores_a_blocked_rule():
    cfg = with_custom({}, "Units", "U", acknowledged=True)
    assert abbreviate("10 Units", cfg)[0] == "10 U"
    unacknowledged = {"abbreviations": {"custom": [{"term": "Units", "replacement": "U"}]}}
    assert abbreviate("10 Units", unacknowledged)[0] == "10 Units"


def test_report_lists_blocked_and_allowed():
    rows = {(r["term"], r["status"]) for r in report({})}
    assert ("Units", "blocked") in rows and ("Tranexamic acid", "warning") in rows
    allowed = {(r["term"], r["status"]) for r in report(with_custom({}, "Units", "U", acknowledged=True))}
    assert ("Units", "allowed") in allowed


def test_normalize_settings_keeps_optional_fields():
    group = normalize_settings({"custom": [
        {"term": "a", "replacement": "b", "acknowledged": True, "pack": "Neuro ICU", "junk": 1},
        {"term": "c", "replacement": "d", "acknowledged": "yes", "pack": ""}]})
    assert group["custom"] == [
        {"term": "a", "replacement": "b", "enabled": True, "acknowledged": True, "pack": "Neuro ICU"},
        {"term": "c", "replacement": "d", "enabled": True}]


def test_suggest_prefers_dictionary_then_initials():
    assert suggest("subarachnoid hemorrhage") == "SAH"
    assert suggest("left side weakness") == "LSW"
    assert suggest("weakness") == ""
    assert lookup("left side weakness") is None


def test_preview_counts_only_new_changes():
    text = "Left side weakness noted.\nHypertension. left side weakness again."
    out = preview(text, {}, "left side weakness", "LSW")
    assert out["count"] == 2
    assert out["samples"][0]["after"].startswith("LSW noted.")
    assert preview(text, {}, "hypertension", "HTN")["count"] == 0


def test_with_custom_reenables_disabled_term():
    cfg = with_custom({"abbreviations": {"disabled": ["Hypertension"]}}, "hypertension", "HTN")
    assert cfg["abbreviations"]["disabled"] == []
    assert abbreviate("Hypertension", cfg)[0] == "HTN"


def test_compound_abbreviations_are_checked_part_by_part():
    assert is_blocked("U/hr", "units per hour")
    assert is_blocked("cc/hr", "cubic centimeters per hour")
    assert not is_blocked("AS/AR", "aortic stenosis and regurgitation")
    assert abbreviate("insulin 2 Units per hour")[0] == "insulin 2 Units per hour"


def test_messages_name_the_right_list():
    assert "The Joint Commission's Do Not Use list" in check("units", "U")[0].message
    assert "ISMP's list of error-prone abbreviations" in check("cubic centimeters", "cc")[0].message
