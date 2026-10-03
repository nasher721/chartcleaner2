import csv

from chartcleaner.abbreviation_safety import is_blocked
from chartcleaner.abbreviations import SOURCE_PATH
from chartcleaner.abbreviations import SOURCE_ROW_COUNT, abbreviate


def test_source_is_complete_and_maps_full_cells():
    assert SOURCE_ROW_COUNT == 1068
    cleaned, count, details = abbreviate("Twice daily (bis in die); Clear to auscultation bilaterally.")
    assert cleaned == "BID; CTAB."
    assert count == 2
    assert details["source_rows"] == 1068


def test_longest_first_and_whole_term_boundaries():
    cleaned, count, _ = abbreviate(
        "Hypertension / coronary artery disease / hyperlipidemia; hypertension; antihypertension."
    )
    assert cleaned == "HTN/CAD/HLD; HTN; antihypertension."
    assert count == 2


def test_case_punctuation_whitespace_and_unknown_text():
    text = "  twice DAILY\t, unknown phrase!\n"
    cleaned, count, _ = abbreviate(text)
    assert cleaned == "  BID\t, unknown phrase!\n"
    assert count == 1
    assert abbreviate("ıntravenous İNTRAVENOUS hypertensioné")[0] == "ıntravenous İNTRAVENOUS hypertensioné"


def test_no_cascading_and_uncertainty_is_preserved():
    # "surgery" also has a dictionary entry, but generated output stays intact.
    assert abbreviate("Cardiothoracic surgery")[0] == "CT surgery"
    cleaned, count, _ = abbreviate("Cardiac intensive care unit (likely); Not expanded in source")
    assert cleaned == "CICU (likely); Not expanded in source"
    assert count == 1
    unqualified = abbreviate("Cardiac intensive care unit")[0]
    assert "likely" not in unqualified.casefold()
    assert unqualified != "CICU (likely)"


def test_empty_and_slash_compound_behavior():
    assert abbreviate("") == ("", 0, {"source_rows": 1068, "replacements": {}})
    cleaned, count, _ = abbreviate("Anticoagulation / antiplatelet therapy; suboccipital craniotomy")
    assert cleaned == "AC/AP; SOC"
    assert count == 2


def test_plural_parenthetical_and_parenthesis_slash_aliases():
    assert abbreviate("Beta blockers")[0] == "BB"
    assert abbreviate("Cranial surgery (craniotomy/craniectomy)")[0] == "crani"
    assert abbreviate("Suboccipital craniotomy")[0] == "SOC"


def test_every_defined_source_cell_has_a_mapping():
    expected: dict[str, str] = {}
    with SOURCE_PATH.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            expanded = row["Expanded version"].strip()
            if not expanded or "not expanded in source" in expanded.casefold():
                continue
            qualifier = ""
            if expanded.casefold().endswith(" (likely)"):
                qualifier = " (likely)"
            abbreviation = row["Abbreviation"].strip()
            # Do Not Use rows are never applied from the bundled list.
            mapped = None if is_blocked(abbreviation, expanded) else abbreviation + qualifier
            expected.setdefault(expanded.casefold(), mapped)
    for expanded, abbreviation in expected.items():
        assert abbreviate(expanded)[0] == (abbreviation or expanded)


def test_user_override_disable_and_boundary_matching():
    cfg = {"abbreviations": {"disabled": ["hypertension"], "custom": [
        {"term": "heart failure", "replacement": "HFx", "enabled": True},
    ]}}
    cleaned, count, _ = abbreviate("hypertension; heart failure; antihypertension", cfg)
    assert cleaned == "hypertension; HFx; antihypertension"
    assert count == 1


def test_disabled_custom_and_unicode_custom_term():
    cfg = {"abbreviations": {"custom": [
        {"term": "café syndrome", "replacement": "CS", "enabled": True},
        {"term": "inactive phrase", "replacement": "IP", "enabled": False},
    ]}}
    assert abbreviate("CAFÉ SYNDROME and inactive phrase", cfg)[0] == "CS and inactive phrase"


def test_unicode_custom_matching_uses_the_matched_rule():
    cfg = {"abbreviations": {"custom": [
        {"term": "i custom", "replacement": "IC"},
        {"term": "İ second", "replacement": "IS"},
    ]}}
    assert abbreviate("İ CUSTOM; İ second", cfg)[:2] == ("IC; IS", 2)


def test_disabling_phrase_protects_subterms_and_disabled_override():
    cfg = {"abbreviations": {"disabled": ["Anterior cerebral artery"], "custom": [
        {"term": "heart failure", "replacement": "HFx", "enabled": False},
    ]}}
    assert abbreviate("ANTERIOR CEREBRAL ARTERY; heart failure", cfg)[:2] == (
        "ANTERIOR CEREBRAL ARTERY; heart failure", 0)


def test_editing_bundled_entry_updates_derived_aliases():
    cfg = {"abbreviations": {"custom": [
        {"term": "Beta blocker(s)", "replacement": "CUSTOM-BB"},
    ]}}
    assert abbreviate("Beta blockers", cfg)[0] == "CUSTOM-BB"


def test_custom_rules_are_one_pass_longest_first_and_unicode_safe():
    cfg = {"abbreviations": {"custom": [
        {"term": "foo", "replacement": "bar", "enabled": True},
        {"term": "bar", "replacement": "baz", "enabled": True},
        {"term": "foo bar", "replacement": "LONG", "enabled": True},
        {"term": "i custom", "replacement": "IC", "enabled": True},
    ]}}
    cleaned, count, _ = abbreviate("foo bar; foo; İ CUSTOM", cfg)
    assert cleaned == "LONG; bar; IC"
    assert count == 3
