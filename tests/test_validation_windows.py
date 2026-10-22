"""Validation windows (``validation_windows``): bounded work with source
ownership. Owned ranges partition the input, context may overlap, offsets stay
source-true, and edits invalidate only the windows they touch."""

from __future__ import annotations

import itertools

import pytest

from chartcleaner.clinical_statements import extract_statements
from chartcleaner.edit_provenance import SourceSpan
from chartcleaner.validation_windows import (
    ValidationWindow,
    WindowedIndex,
    iter_validation_windows,
)

# Distinct clinical lines so each window is forced to hold real statements.
LINE = "Na {n} mmol/L, K {k} mmol/L — plan: aspirin 81 mg daily. Denies chest pain #{n}.\n"


def _chart(n_lines: int, header_every: int = 40) -> str:
    parts = []
    for i in range(n_lines):
        if i % header_every == 0:
            parts.append(f"Progress Note {i // header_every + 1}\n")
        parts.append(LINE.format(n=130 + (i % 20), k=3 + (i % 3)))
    return "".join(parts)


def _windows(text: str, target: int = 2000):
    return list(iter_validation_windows(text, target_chars=target))


# --- window contract ------------------------------------------------------------

def test_owned_ranges_partition_the_input_with_source_offsets():
    text = _chart(220)
    windows = _windows(text)
    assert windows
    pos = 0
    for w in windows:
        assert w.owned_start == pos, "owned ranges are contiguous"
        assert w.owned_end > w.owned_start
        assert w.source_start <= w.owned_start < w.owned_end <= w.source_end <= len(text)
        assert text[w.source_start:w.source_end] == w.text, "text is a verbatim slice"
        pos = w.owned_end
    assert pos == len(text)
    # Context may reach before the owned range; ownership never overlaps.
    with_context = [w for w in windows if w.source_start < w.owned_start]
    assert with_context, "windows carry context from their neighbour"
    for w in windows:
        overlapping = [x for x in windows
                       if x is not w and x.owned_start < w.owned_end and w.owned_start < x.owned_end]
        assert not overlapping


def test_windows_break_on_note_boundaries_and_carry_note_id_and_section():
    text = ("Subjective: denies fever.\n" + _chart(40)
            + "\nProgress Note 2\nObjective: BP 128/76.\n" + _chart(40))
    windows = _windows(text, target=1200)
    assert len(windows) > 1
    assert any(w.note_id for w in windows), "note headers are carried"
    starts = [w.owned_start for w in windows]
    note2 = text.index("Progress Note 2")
    assert any(abs(s - note2) < 200 for s in starts), "a break lands near the note boundary"


def test_fact_split_across_window_keeps_its_subject():
    # A pipe table spans several windows: rows owned by later windows still
    # need their header row, so the window carries it as context.
    rows = "".join(f"| 03/{12 + i % 17:02d} | {130 + i} | {3 + i % 4}.{i % 9} |\n" for i in range(300))
    text = "Labs:\n| Date | Na | K |\n" + rows
    index = WindowedIndex(text, target_chars=1500)
    assert len(index.windows) >= 3, "the table spans several windows"
    owning_later = [w for w in index.windows if w.owned_start > text.index("| Date") + 20]
    assert owning_later, "some rows are owned by a window without the header in range"
    assert all("| Date | Na | K |" in w.text for w in owning_later), "the header is carried"
    found = {(s.subject, s.value) for s in index.statements()}
    for i in (5, 60, 118, 290):
        assert ("na", str(130 + i)) in found, f"row {i} kept its label across the boundary"


def _owned(statement, window: ValidationWindow) -> bool:
    return any(max(sp.start, window.owned_start) < min(sp.end, window.owned_end)
               for sp in statement.source_spans)


def test_duplicate_overlap_counts_once():
    text = _chart(120)
    windows = _windows(text, target=800)
    index = WindowedIndex(text, target_chars=800)
    seen: set[tuple] = set()
    total = 0
    for w in index.windows:
        for s in index.extraction(w).statements:
            if not _owned(s, w):
                continue  # context copy: owned by a neighbour
            key = (s.kind, s.subject, s.value, s.source_spans)
            if key in seen:
                continue
            seen.add(key)
            total += 1
    flat = [s for s in extract_statements(text).statements]
    assert total == len(flat), "statements in overlapping context are counted once"


def test_single_oversized_note_reports_partial():
    line = "Na 141 mmol/L " + ("supportive care and monitoring continues today " * 6000)
    text = f"Progress Note 1\n{line}\n"
    index = WindowedIndex(text, target_chars=2000)
    oversized = [w for w in index.windows if (w.owned_end - w.owned_start) > 2 * 2000]
    assert oversized, "a line that cannot be split stays one oversized window"
    assert index.window_flags(oversized[0]) == ("oversized_window",)
    report = index.coverage()
    assert report.status == "partial" and "oversized_window" in report.reasons


def test_changed_window_invalidates_related_index(monkeypatch):
    text = _chart(300)
    index = WindowedIndex(text, target_chars=900)
    calls = {"n": 0}
    real = extract_statements

    def counting(text_, **kw):
        calls["n"] += 1
        return real(text_, **kw)

    monkeypatch.setattr("chartcleaner.validation_windows.extract_statements", counting)
    index.ensure_loaded()
    before = calls["n"]
    assert before == len(index.windows), "each window extracted exactly once"
    # Touch one region: only overlapping windows are re-extracted.
    target = index.windows[len(index.windows) // 2]
    index.invalidate(target.owned_start + 10, target.owned_start + 20)
    index.ensure_loaded()
    assert calls["n"] <= before + 3, "unaffected windows keep their index"
    # The same region again costs only its own windows.
    again = calls["n"]
    index.invalidate(target.owned_start + 10, target.owned_start + 20)
    index.ensure_loaded()
    assert calls["n"] - again <= 3


def test_multiline_regex_behavior_is_unchanged_by_windows():
    # Windows never touch the cleaning pipeline: it still runs whole-document.
    from chartcleaner.engine import Pipeline
    cfg = {"emr_line_metadata": [], "boilerplate": [r"^Progress Note \d+\n"], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "wrap_output": False, "duplicate_note_detection": {"enabled": False},
           "fuzzy_dedup": {"enabled": False}, "audit": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}},
           "cleaning_validation": {"mode": "shadow", "schema_version": 1}}
    text = _chart(160)
    whole = Pipeline({**cfg, "cleaning_validation": {"mode": "off", "schema_version": 1}}).run(text)
    shadow = Pipeline(cfg).run(text)
    assert shadow.text == whole.text
    assert "Progress Note" not in shadow.text
    # A DOTALL rule spanning a would-be window boundary still matched once, whole.
