import csv

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
            expected.setdefault(expanded.casefold(), row["Abbreviation"].strip() + qualifier)
    for expanded, abbreviation in expected.items():
        assert abbreviate(expanded)[0] == abbreviation
