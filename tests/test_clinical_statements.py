"""Deterministic clinical statement extraction (``clinical_statements``):
medication tuples, labeled measurements, findings with polarity / certainty /
temporality / experiencer / laterality — unknown fields stay unknown, and a
sentence with clinical signal that does not parse is an *unresolved* span."""

from __future__ import annotations

import pytest

from chartcleaner.clinical_statements import (
    ClinicalStatement,
    StatementExtraction,
    canonical_subject,
    extract_statements,
)


def _one(text: str, kind: str | None = None) -> ClinicalStatement:
    found = [s for s in extract_statements(text).statements if kind is None or s.kind == kind]
    assert len(found) == 1, found
    return found[0]


# --- medications -------------------------------------------------------------

def test_medication_tuple_keeps_dose_route_frequency_together():
    s = _one("Meds: aspirin 81 mg PO daily.", "medication")
    assert (s.subject, s.value, s.unit, s.route, s.frequency) == ("aspirin", "81", "mg", "PO", "daily")
    assert s.medication_status is None and s.polarity == "positive"


def test_two_medications_in_one_line_keep_their_own_doses():
    out = extract_statements("aspirin 81 mg daily and atorvastatin 40 mg nightly")
    meds = {s.subject: (s.value, s.frequency) for s in out.statements if s.kind == "medication"}
    assert meds == {"aspirin": ("81", "daily"), "atorvastatin": ("40", "nightly")}


def test_medication_status_words_are_captured():
    assert _one("Metoprolol 25 mg BID — held for hypotension.", "medication").medication_status == "held"
    assert _one("Warfarin discontinued 03/13 for INR 4.2.", "medication").medication_status == "discontinued"
    assert _one("Hold warfarin tonight.", "medication").medication_status == "held"
    assert _one("Continue aspirin 81 mg daily.", "medication").medication_status == "continued"
    assert _one("Started vancomycin 1 g IV q12h.", "medication").medication_status == "started"


def test_long_form_route_and_frequency_normalize_to_the_abbreviated_form():
    long = _one("heparin 5000 units subcutaneous twice daily", "medication")
    short = _one("heparin 5000 units SC BID", "medication")
    assert (long.route, long.frequency) == (short.route, short.frequency) == ("SC", "BID")
    assert long.value == short.value == "5000" and long.unit == short.unit == "units"


# --- measurements --------------------------------------------------------------

def test_labeled_measurements_use_canonical_subjects():
    a = _one("Sodium 141 mmol/L", "measurement")
    b = _one("Na 141", "measurement")
    assert a.subject == b.subject == "na" and a.value == b.value == "141"
    assert a.unit == "mmol/L" and b.unit is None


def test_blood_pressure_is_one_measurement():
    out = extract_statements("Vitals: BP 128/76, HR 72.")
    values = {s.subject: s.value for s in out.statements if s.kind == "measurement"}
    assert values == {"bp": "128/76", "hr": "72"}


def test_dates_on_the_line_become_temporality():
    out = extract_statements("03/12: Na 131.\n03/14: Na 138.\n")
    temps = {(s.value, s.temporality) for s in out.statements if s.kind == "measurement"}
    assert temps == {("131", "03/12"), ("138", "03/14")}


def test_a_dose_is_not_also_a_measurement():
    out = extract_statements("aspirin 81 mg daily")
    assert [s.kind for s in out.statements] == ["medication"]


# --- findings ----------------------------------------------------------------

def test_negated_finding():
    s = _one("Denies shortness of breath.", "finding")
    assert s.polarity == "negative" and s.subject == "shortness of breath"
    assert s.certainty == "stated" and s.experiencer == "patient" and s.temporality is None


def test_approved_abbreviations_parse_to_the_same_statement():
    a = _one("Denies shortness of breath.", "finding")
    b = _one("Denies SOB.", "finding")
    assert (a.subject, a.polarity) == (b.subject, b.polarity)
    nad = _one("Exam: NAD.", "finding")
    full = _one("Exam: No acute distress.", "finding")
    assert nad.polarity == full.polarity == "negative" and nad.subject == full.subject


def test_certainty_cues():
    assert _one("Possible pneumonia.", "finding").certainty == "possible"
    assert _one("Concern for sepsis.", "finding").certainty == "possible"
    assert _one("Plan: rule out PE.", "finding").certainty == "possible"
    assert _one("Pneumonia confirmed.", "finding").certainty == "confirmed"
    assert _one("Pneumonia.", "finding").certainty == "stated"


def test_temporality_cues():
    assert _one("History of stroke in 2019.", "finding").temporality == "past"
    assert _one("s/p appendectomy 2015.", "finding").temporality == "past"
    assert _one("Fever, resolved.", "finding").temporality == "past"
    assert _one("Acute infarct.", "finding").temporality is None


def test_family_history_is_a_different_experiencer():
    fam = _one("Family history of stroke.", "finding")
    assert fam.experiencer == "family" and fam.subject == "stroke"
    assert _one("History of stroke.", "finding").experiencer == "patient"


def test_laterality_is_parsed_and_normalized():
    assert _one("Left leg weakness 3/5.", "finding").laterality == "L"
    assert _one("R hemiparesis.", "finding").laterality == "R"
    assert _one("Bilateral crackles.", "finding").laterality == "B"
    assert _one("Leg weakness.", "finding").laterality is None


def test_a_negated_list_negates_each_item():
    out = extract_statements("Denies fever, chills, cough.")
    findings = {s.subject: s.polarity for s in out.statements if s.kind == "finding"}
    assert findings == {"fever": "negative", "chills": "negative", "cough": "negative"}


def test_plain_boilerplate_is_not_a_statement():
    out = extract_statements("Chart reviewed.\nPage 1 of 3\nElectronically signed by Dr. Example\n")
    assert out.statements == ()
    assert out.unresolved_spans == (), "no clinical signal, nothing left unresolved"


def test_a_negation_without_clinical_content_is_unresolved_not_a_finding():
    text = "Dictated but not read.\n"
    out = extract_statements(text)
    assert out.statements == (), "'read' is not a clinical finding"
    assert out.unresolved_spans == ((0, len("Dictated but not read.")),), "...but the 'not' is signal nobody checked"


def test_clinical_signal_that_does_not_parse_is_unresolved():
    out = extract_statements("Denies.\nPlan: PT.\n")
    assert out.statements == ()
    assert out.unresolved_spans and out.unresolved_spans[0].start == 0


# --- spans, offsets, sections ---------------------------------------------------

def test_source_spans_honor_the_offset_and_point_at_the_sentence():
    text = "Subjective: Denies chest pain. Slept well.\n"
    out = extract_statements(text, source_offset=100, note_id="n1")
    s = out.statements[0]
    span = s.source_spans[0]
    assert span.start >= 100 and text[span.start - 100:span.end - 100].strip() == "Denies chest pain."
    assert s.note_id == "n1"


def test_section_is_attributed_from_the_text_when_not_given():
    text = "Objective: BP 128/76.\nPlan: aspirin 81 mg daily.\n"
    out = extract_statements(text)
    by = {s.subject: s.section for s in out.statements}
    assert by["bp"] == "physical_exam" and by["aspirin"] == "assessment_and_plan"
    given = extract_statements("BP 128/76", section="vitals")
    assert given.statements[0].section == "vitals"


def test_statement_dict_is_json_safe_and_unknowns_are_null():
    import json
    s = _one("Pneumonia.", "finding")
    data = json.loads(json.dumps(s.to_dict()))
    assert data["laterality"] is None and data["temporality"] is None and data["value"] is None


def test_canonical_subject_strips_articles_and_whitespace():
    assert canonical_subject("  the  Left   MCA infarct ") == canonical_subject("left mca infarct")
