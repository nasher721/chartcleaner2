"""Tests for copy-forward delta compression engine."""

import pytest
from chartcleaner.delta_engine import extract_note_deltas, format_delta_timeline


def test_delta_single_note():
    text = "Single clinical progress note without subsequent days."
    res = extract_note_deltas(text)
    assert res.notes_found == 1
    assert res.compression_ratio == 0.0
    assert res.compact_text == text


def test_delta_multi_day_progress_notes():
    # Day 1
    day1 = (
        "Progress Notes by Dr. Smith on 10/12/2026\n\n"
        "Patient admitted with acute pancreatitis. NPO, IV hydration at 150 cc/hr.\n\n"
        "Pain controlled on PCA. Lipase 840, WBC 12.1. Abdominal ultrasound pending.\n\n"
        "Assessment & Plan: Acute pancreatitis, likely gallstone. Continue aggressive IVF."
    )
    # Day 2: Copy-forwarded Day 1 with slight changes (lipase down, ultrasound resulted)
    day2 = (
        "Progress Notes by Dr. Smith on 10/13/2026\n\n"
        "Patient admitted with acute pancreatitis. NPO, IV hydration at 150 cc/hr.\n\n"
        "Pain improved today. Lipase decreased to 320, WBC 9.4. Ultrasound shows cholelithiasis without cholecystitis.\n\n"
        "Assessment & Plan: Resolving pancreatitis. Advance diet to clear liquids. General surgery consult for cholecystectomy."
    )

    full_chart = f"{day1}\n\n{day2}"
    res = extract_note_deltas(full_chart)

    assert res.notes_found == 2
    assert len(res.deltas) == 1
    delta = res.deltas[0]
    assert delta.date_str == "10/13/2026"
    assert delta.baseline_similarity > 50.0  # High similarity due to copy-forward

    # Check that identical paragraph was pruned
    assert "## Baseline Admission Note" in res.compact_text
    assert "Longitudinal Clinical Updates" in res.compact_text
    assert res.compression_ratio > 0.0


def test_delta_never_drops_new_sentences_numbers_or_short_lines():
    text = (
        "Note Date: 10/01/2026\n"
        "Patient stable overnight. Neuro exam unchanged from prior with no focal deficits.\n"
        "Plan continue nimodipine and monitor sodium closely. ICP 12.\n\n"
        "Note Date: 10/02/2026\n"
        "Patient stable overnight. Neuro exam unchanged from prior with no focal deficits.\n"
        "Plan continue nimodipine and monitor sodium closely. ICP 25.\n"
        "New fever to 38.6 overnight, cultures sent.\n\n"
        "Na 128.\n"
    )
    res = extract_note_deltas(text)
    update = res.compact_text.split("Clinical Update (10/02/2026)")[1]
    for fact in ("ICP 25", "New fever to 38.6 overnight, cultures sent", "Na 128"):
        assert fact in update
    assert "Patient stable overnight" not in update  # copied sentence pruned
    assert "Note Date: 10/02/2026" not in update     # the header is not "new content"


def test_delta_fully_copied_note_says_so():
    note = "Patient stable overnight with no new complaints today.\nContinue current plan."
    res = extract_note_deltas(f"Note Date: 10/01/2026\n{note}\n\nNote Date: 10/02/2026\n{note}\n")
    assert "Every sentence repeats an earlier note" in res.compact_text
    assert res.compression_ratio > 0


def test_delta_keeps_preamble_and_respects_wrapper():
    note = "Patient stable overnight with no new complaints today."
    text = (f"<patient_chart>\nName: [REDACTED]\nProblem list: SAH\n\nNote Date: 10/01/2026\n{note}\n\n"
            f"Note Date: 10/02/2026\n{note}\nNew fever 38.6.\n</patient_chart>")
    res = extract_note_deltas(text)
    assert res.notes_found == 2
    assert res.compact_text.startswith("<patient_chart>\n## Baseline Admission Note\nName: [REDACTED]")
    assert res.compact_text.endswith("New fever 38.6.\n</patient_chart>")
    assert res.compact_text.count("</patient_chart>") == 1
