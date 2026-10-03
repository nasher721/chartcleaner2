"""Note-type detection."""

from pathlib import Path

import pytest

from chartcleaner.engine import load_default_config, validate_config
from chartcleaner.note_type import detect

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("text,kind", [
    ("DISCHARGE SUMMARY\nHospital Course: improved\nDischarge Medications:\n", "discharge_summary"),
    ("History and Physical\nChief Complaint: fall\nReview of Systems: negative\n", "h_and_p"),
    ("Reason for Consultation: AKI\nRequesting Physician: Dr X\nRecommendations:\n", "consult"),
    ("Operative Report\nPreoperative Diagnosis: x\nEstimated Blood Loss: 20 mL\n", "operative"),
    ("Shift Assessment\nFall Risk: high\nBraden: 14\n", "nursing"),
])
def test_detects_common_note_types(text, kind):
    assert detect(text).note_type == kind


def test_sample_chart_is_a_progress_note():
    assert detect((ROOT / "sample_chart.txt").read_text(encoding="utf-8")).note_type == "progress"


def test_unclear_text_is_not_guessed():
    found = detect("Chief Complaint: cough\nIMPRESSION: pneumonia\n")  # one cue each
    assert found.note_type is None and found.label == "Not sure"


def test_config_can_add_cues_and_types():
    cfg = {"note_profiles": {"detect": {"ed_note": ["ED Course", "Triage"]}}}
    assert detect("Triage: chest pain\nED Course: stable\n", cfg).note_type == "ed_note"


def test_note_profiles_are_validated():
    bad = {**load_default_config(), "note_profiles": {"detect": {"x": ["(unclosed"]}}}
    assert any("note_profiles" in e for e in validate_config(bad)[0])
