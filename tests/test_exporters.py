"""Exports: Word, Markdown / notes folder, Epic-safe text."""

from datetime import date

import pytest

from chartcleaner.exporters import save_to_vault, to_docx, to_markdown, to_smartphrase
from chartcleaner.ingest import _docx_stdlib

CHART = "<patient_chart>\nAssessment & Plan:\n• Temp 38.4 °C — rising\n“quoted” text… ***\n</patient_chart>"


def test_docx_round_trips_through_the_word_importer(tmp_path):
    path = tmp_path / "out.docx"
    path.write_bytes(to_docx(CHART, title="Bed 4"))
    text = _docx_stdlib(path)
    assert "Bed 4" in text and "Assessment & Plan" in text and "Temp 38.4 °C — rising" in text
    assert "patient_chart" not in text


def test_smartphrase_is_ascii_wrapped_and_keeps_blanks():
    out = to_smartphrase(CHART + "\n\tTabbed\n" + "word " * 30)
    assert out.isascii() and "\t" not in out
    assert "- Temp 38.4 C - rising" in out and '"quoted" text... ***' in out
    assert max(len(line) for line in out.splitlines()) <= 80
    assert "patient_chart" not in out


def test_markdown_front_matter():
    md = to_markdown("Assessment & Plan:\nStable.", note_type="Progress note", today=date(2026, 10, 3))
    assert md.startswith("---\ndate: 2026-10-03\nsource: Chart Cleaner\nnote_type: Progress note\n")
    assert "## Assessment & Plan" in md


def test_vault_save_never_overwrites(tmp_path):
    first = save_to_vault("a", tmp_path, "Bed 4/../x")
    second = save_to_vault("b", tmp_path, "Bed 4/../x")
    assert first.parent == tmp_path and first.name == "Bed 4x.md"
    assert second.name == "Bed 4x (2).md" and first.read_text() == "a"
    with pytest.raises(FileNotFoundError):
        save_to_vault("a", tmp_path / "missing")
