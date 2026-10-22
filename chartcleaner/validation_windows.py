"""Validation windows: bounded work over large charts, with source ownership.

The cleaning pipeline always runs on the whole document — chunking *it* would
change multiline regex, deduplication and temporal behavior. What gets
windowed is the *validation* work that would otherwise be whole-document per
stage: statement extraction over the source (or a candidate stage output).

:func:`iter_validation_windows` partitions the text into windows of about
``target_chars`` owned characters, cut at note starts, then blank lines, then
line breaks — never mid-line, so a fact is never cut in two by a boundary.
Each window carries up to ``context_chars`` of preceding text as context, so
a structure that spans the boundary (a table whose header row sits in the
previous window) still parses in the window that owns its rows. Owned ranges
partition the input; context may overlap; offsets are always source
coordinates.

:class:`WindowedIndex` caches one extraction per window and invalidates only
the windows an edit touches, so re-validating a changed region costs that
region, not the chart. A line that could not be split is extracted as-is but
flagged ``oversized_window`` — its relation coverage is never claimed
complete.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from .clinical_statements import StatementExtraction, extract_statements
from .edit_provenance import merge_spans

__all__ = ["CONTEXT_CHARS", "TARGET_CHARS", "ValidationWindow", "WindowedIndex",
           "iter_validation_windows"]

TARGET_CHARS = 32_000
CONTEXT_CHARS = 400
# A window more than twice the target is a line that refused to split.
OVERSIZED_FACTOR = 2


@dataclass(frozen=True)
class ValidationWindow:
    text: str            # verbatim slice [source_start, source_end)
    source_start: int
    source_end: int
    owned_start: int     # the part this window owns (source coordinates)
    owned_end: int
    note_id: str = ""
    section: str = ""
    context_note_id: str = ""   # the note the carried context came from

    @property
    def owned_chars(self) -> int:
        return self.owned_end - self.owned_start


def _note_starts(text: str) -> list[int]:
    return [m.start() for m in re.finditer(
        r"(?mi)^\s*(?:#{1,3}\s*)?(?:progress note|history and physical|h&p|note|consult|discharge summary)\b",
        text)] or [0]


def _line_breaks(text: str) -> list[int]:
    # Positions right after each newline (candidate cut points).
    return [0] + [m.end() for m in re.finditer(r"\n", text)]


def _blank_breaks(text: str) -> list[int]:
    return [m.end() for m in re.finditer(r"\n\s*\n", text)]


def _section_spans(text: str):
    try:
        from .stage_core import _section_spans
        return _section_spans(text)
    except Exception:
        return []


def _with_table_header(text: str, owned_start: int, source_start: int) -> int:
    """Extend context back to the header row when the window starts mid-table."""
    if owned_start == 0 or text[owned_start - 1] != "\n":
        return source_start
    line_end = owned_start - 1
    limit = max(0, source_start - 20_000)  # absurdly wide tables stop carrying
    if text.count("|", max(0, line_end - 400), line_end) < 2:
        return source_start  # the previous line is not a table row
    pos = line_end
    while pos > limit:
        line_start = text.rfind("\n", limit, pos) + 1
        if text.count("|", line_start, pos) < 2:
            break  # the run of table lines ended
        pos = line_start - 1 if line_start > 0 else 0
        if pos <= 0:
            return 0
    return min(source_start, pos + 1) if pos + 1 < owned_start else source_start


def iter_validation_windows(text: str, *, target_chars: int = TARGET_CHARS,
                            context_chars: int = CONTEXT_CHARS) -> "list[ValidationWindow]":
    """Windows whose owned ranges partition ``text``, each about ``target_chars``
    owned characters. Cuts prefer note starts, then blank lines, then line
    breaks; a stretch with no cut within ``2 x target`` stays one window."""
    if not text:
        return []
    notes = _note_starts(text)
    note_set = set(notes)
    blanks = _blank_breaks(text)
    lines = _line_breaks(text)
    sections = _section_spans(text)

    def next_cut(last: int) -> int:
        preferred = [c for c in notes + blanks if last < c <= last + target_chars]
        if preferred:
            return max(preferred)
        fallback = [c for c in lines if last < c <= last + target_chars]
        if fallback:
            return max(fallback)
        # No line break nearby: take the next one however far (an oversized
        # window), unless that is past the whole text.
        ahead = [c for c in lines if c > last]
        return ahead[0] if ahead else len(text)

    owned = [0]
    while owned[-1] < len(text):
        owned.append(next_cut(owned[-1]))
    if owned[-1] != len(text):
        owned[-1] = len(text)

    def section_at(pos: int) -> str:
        for start, end, domain in sections:
            if start <= pos < end:
                return domain
        return ""

    out = []
    for i in range(len(owned) - 1):
        owned_start, owned_end = owned[i], owned[i + 1]
        note_number = bisect_right(notes, owned_start)
        source_start = max(0, owned_start - context_chars)
        nl = text.rfind("\n", source_start, owned_start)
        if nl >= source_start and nl + 1 < owned_start:
            source_start = nl + 1  # context ends at a line boundary when one fits
        # A window that starts inside a table owns rows whose header row sits
        # in an earlier window: carry the header, or the rows have no labels.
        source_start = _with_table_header(text, owned_start, source_start)
        context_note = max(1, bisect_right(notes, source_start))
        out.append(ValidationWindow(
            text=text[source_start:owned_end], source_start=source_start, source_end=owned_end,
            owned_start=owned_start, owned_end=owned_end,
            note_id=f"note{note_number or 1}", section=section_at(owned_start),
            context_note_id=f"note{context_note}"))
    return out


def owned_statements(extraction: StatementExtraction, window: ValidationWindow) -> list:
    """The statements of ``extraction`` whose spans this window owns."""
    return [s for s in extraction.statements
            if any(max(sp.start, window.owned_start) < min(sp.end, window.owned_end)
                   for sp in s.source_spans)]


class WindowedIndex:
    """Per-window statement extraction, invalidated by the edits that touch it."""

    def __init__(self, text: str, *, target_chars: int = TARGET_CHARS,
                 context_chars: int = CONTEXT_CHARS, note_id: str = ""):
        self.text = text
        self.target_chars = target_chars
        self.windows: list[ValidationWindow] = list(
            iter_validation_windows(text, target_chars=target_chars, context_chars=context_chars))
        self.note_id = note_id
        self._cache: dict[int, StatementExtraction] = {}
        self._dirty: set[int] = set(range(len(self.windows)))

    def ensure_loaded(self) -> None:
        for i in sorted(self._dirty):
            w = self.windows[i]
            self._cache[i] = extract_statements(w.text, source_offset=w.source_start,
                                                note_id=self.note_id or w.note_id,
                                                section=w.section or "")
        self._dirty.clear()

    def invalidate(self, start: int, end: int) -> None:
        """Mark the windows overlapping ``[start, end)`` for re-extraction."""
        for i, w in enumerate(self.windows):
            if w.source_start < end and start < w.source_end:
                self._dirty.add(i)

    def extraction(self, window: ValidationWindow) -> StatementExtraction:
        self.ensure_loaded()
        for i, w in enumerate(self.windows):
            if w is window:
                return self._cache.get(i) or StatementExtraction((), ())
        raise KeyError("window not in this index")

    def window_flags(self, window: ValidationWindow) -> tuple[str, ...]:
        if window.owned_chars > OVERSIZED_FACTOR * self.target_chars:
            return ("oversized_window",)
        return ()

    def statements(self) -> list:
        """Every statement, each owned exactly once (context copies dropped)."""
        self.ensure_loaded()
        out = []
        for i, w in enumerate(self.windows):
            out.extend(owned_statements(self._cache[i], w))
        return out

    def unresolved(self):
        """Unresolved spans clipped to the window that owns them (counted once),
        plus the whole owned range of any window that could not be split."""
        self.ensure_loaded()
        spans = []
        for i, w in enumerate(self.windows):
            if self.window_flags(w):
                spans.append((w.owned_start, w.owned_end))
                continue
            for span in self._cache[i].unresolved_spans:
                if span.start < w.owned_end and span.end > w.owned_start:
                    spans.append((max(span.start, w.owned_start), min(span.end, w.owned_end)))
        return merge_spans(spans)

    def coverage(self):
        from .validation import Coverage
        self.ensure_loaded()
        reasons = [r for w in self.windows for r in self.window_flags(w)]
        recognized = len(self.statements())
        return Coverage.from_spans(
            len(self.text), [(w.owned_start, w.owned_end) for w in self.windows],
            unresolved=self.unresolved(), recognized=recognized, checked=recognized,
            reasons=tuple(dict.fromkeys(reasons)))
