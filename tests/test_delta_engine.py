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
