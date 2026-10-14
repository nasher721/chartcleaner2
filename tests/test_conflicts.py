"""Possible contradictions in the latest note (insight "conflicts") and its
service / API / MCP / CLI / note-template exposure."""

from __future__ import annotations

import pytest

from chartcleaner import conflicts, note_templates, service

CHART = """Date of Service: 10/11/2026
Allergies: Penicillin (hives)
Code status: Full code
Exam: R hemiparesis

Date of Service: 10/12/2026
HPI: 67 y/o man w right MCA stroke.
Exam: left hemiparesis, left facial droop.
Assessment: RHP improving.
Diet: regular
Remains NPO.
Code status: Full code
DNR/DNI
No AC given hemorrhagic conversion.
Hep gtt per nomogram.
Pip-tazo 4.5 g IV q8h.
Afebrile. Tmax 38.6 C.
"""


def _kinds(text: str) -> list[str]:
    return [c.kind for c in conflicts.build(text).conflicts]


def test_every_check_fires_on_the_latest_note():
    report = conflicts.build(CHART)
    assert [c.kind for c in report.conflicts] == list(conflicts.KINDS[:1] + conflicts.KINDS[2:])
    side = report.conflicts[0]
    assert side.why.startswith("weakness") and side.lines == ["Exam: left hemiparesis, left facial droop.",
                                                              "Assessment: RHP improving."]
    allergy = next(c for c in report.conflicts if c.kind == "allergy")
    assert allergy.lines == ["Allergies: Penicillin (hives)", "Pip-tazo 4.5 g IV q8h."]
    fever = next(c for c in report.conflicts if c.kind == "fever")
    assert fever.lines == ["Afebrile. Tmax 38.6 C."]  # both halves on one line: quoted once
    text = report.to_text()
    assert text.startswith("Possible contradictions") and "“Hep gtt per nomogram.”" in text


def test_only_the_latest_note_is_compared():
    # "R hemiparesis" sits in the earlier note; the latest says left only
    assert _kinds("Date of Service: 10/11/2026\nR hemiparesis\n\nDate of Service: 10/12/2026\n"
                  "L hemiparesis\n") == []


@pytest.mark.parametrize("text", [
    "R MCA stroke with L hemiparesis",              # lesion and deficit opposite
    "L>R weakness\nR arm weakness",                 # both sides on one line
    "Bilateral SDH\nR SDH larger than L SDH",
    "R/O SDH\nL SDH",                               # R/O is not "right"
    "2 L NC\nR hemiparesis",                        # liters are not "left"
    "h/o L MCA stroke\nR MCA stroke",               # history line ignored
    "no L weakness\nR weakness",                    # negated
    "NPO after midnight\nDiet: regular",            # planned NPO
    "Will make NPO for OR\nTolerating regular diet",
    "Discussed DNR, remains full code\nFull code",  # code talk, not a second status
    "Hep gtt held for procedure\nNo AC",            # held drug is not active
    "Heparin 5000 units SQ q8h\nNo anticoagulation",  # prophylaxis is not therapeutic
    "Allergies: NKDA\nZosyn 4.5 g IV q8h",
    "Allergies: penicillin\nAvoid zosyn",           # med line says avoid
    "Afebrile\nTmax 37.9",
    "R cerebellar hemorrhage\nR arm weakness",      # cerebellar lesions are ipsilateral
])
def test_no_flag(text):
    assert _kinds(text) == []


@pytest.mark.parametrize("text, kind", [
    ("R MCA infarct\nR hemiparesis", "lesion side"),
    ("R frontal ICH\nExam: R arm drift", "lesion side"),
    ("LMCA infarct\nLHP", "lesion side"),
    ("RUE weakness\nLUE drift", "side"),
    ("RSW\nLeft side weakness", "side"),
    ("R EVD in place\nL EVD output 50 mL", "side"),
    ("CT to suction on the right\nL chest tube", "side"),
    ("NPO\nTolerating PO", "diet"),
    ("Comfort care\nFull code", "code status"),
    ("Holding anticoagulation\nApixaban 5 mg BID", "anticoagulation"),
    ("HIT positive\nHeparin gtt", "anticoagulation"),  # no-AC wording absent; caught as allergy
    ("Allergic to sulfa\nBactrim DS 1 tab PO BID", "allergy"),
    ("ALLG: vancomycin (red man)\nVancomycin 1 g IV q12h", "allergy"),
    ("No fevers\nT 101.2 F", "fever"),
])
def test_flag(text, kind):
    found = _kinds(text)
    if text.startswith("HIT"):
        assert found == ["allergy"]
    else:
        assert found == [kind]


def test_empty_text():
    assert conflicts.build("").empty and conflicts.build("").to_text() == ""


def test_service_insight_and_format_output():
    assert "conflicts" in service.INSIGHTS
    data = service.insights(CHART, ["conflicts"])
    assert data["conflicts"]["conflicts"][0]["kind"] == "side"
    out, _ = service.format_output(CHART, insights=True)
    assert out.index("Possible contradictions") < out.index("Date of Service")


def test_note_template_placeholder():
    cfg = {"note_templates": [{"name": "t", "template": "{{conflicts}}"}]}
    assert note_templates.render("t", CHART, cfg).startswith("Possible contradictions")
