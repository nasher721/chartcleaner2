"""Near-duplicate note folding never drops a line only the later copy has."""

from __future__ import annotations

from pathlib import Path

from chartcleaner import stage_dedup
from chartcleaner.engine import Pipeline, load_config

ROOT = Path(__file__).resolve().parent.parent

BODY = "\n".join(
    f"Line {i}: patient stable, continue current management plan as discussed."
    for i in range(12)
)


def _cfg() -> dict:
    return {"duplicate_note_detection": {"enabled": True, "min_body_chars": 400,
                                         "similarity_threshold": 90}}


def test_unique_line_in_folded_note_survives():
    text = (f"Progress Notes by A, MD\n{BODY}\n\n"
            f"Progress Notes by A, MD\n{BODY}\nPlan: recheck sodium zzabc tomorrow.\n")
    out, n, details = stage_dedup.run_duplicate_notes(text, _cfg(), None)
    assert "zzabc" in out
    assert out.count("Line 3:") == 1  # the repeated body is still folded
    assert n == 1
    assert details["notes_partially_folded"] == 1
    assert details["unique_lines_kept"] == 1
    assert out.endswith("Progress Notes by A, MD\nPlan: recheck sodium zzabc tomorrow.\n")


def test_changed_line_in_folded_note_survives():
    later = BODY.replace("Line 5: patient stable", "Line 5: patient febrile")
    text = f"Progress Notes by A, MD\n{BODY}\n\nProgress Notes by A, MD\n{later}\n"
    out, _, _ = stage_dedup.run_duplicate_notes(text, _cfg(), None)
    assert "patient febrile" in out
    assert out.count("Line 6:") == 1


def test_exact_repeat_still_dropped_whole():
    text = f"Progress Notes by A, MD\n{BODY}\n\nProgress Notes by A, MD\n{BODY}\n"
    out, n, details = stage_dedup.run_duplicate_notes(text, _cfg(), None)
    assert out.count("Progress Notes by A, MD") == 1
    assert n == 1
    assert "notes_partially_folded" not in details


def test_pipeline_keeps_line_unique_to_later_copy():
    note = (ROOT / "sample_chart.txt").read_text()
    text = note + "\n\n" + note + "\nPlan: recheck sodium zzabc tomorrow.\n"
    cfg = load_config(str(ROOT / "config.json"))
    cfg["nlp_redaction"] = {"enabled": False}
    pipeline = Pipeline(cfg)
    assert "zzabc" in pipeline.run(text).text
    _, after, _ = pipeline.stage_io(text, "duplicate_notes")
    assert "zzabc" in after
