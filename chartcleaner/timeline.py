"""The notes inside one chart, in order, with where each starts (Clean page timeline).

Splits the text the way the delta engine and trends do (note headers with
dates) and returns, per note, its title, date, hospital-day label when the
``hospital_day`` stage added one, a one-line preview and its character offsets
in the text — so the UI can jump to a note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .delta_engine import _split_into_notes

__all__ = ["TimelineNote", "build"]

_HOSPITAL_DAY = re.compile(r"\((?:HD|POD)#\s*\d+[^)]*\)")


@dataclass
class TimelineNote:
    index: int
    title: str
    date: str
    day: str
    preview: str
    start: int
    end: int
    chars: int

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def build(text: str) -> list[TimelineNote]:
    """Notes in ``text``; empty for a chart with a single note."""
    notes = _split_into_notes(text)
    if len(notes) < 2:
        return []
    out: list[TimelineNote] = []
    cursor = 0
    for n in notes:
        # Note 1 starts at 0 (it carries any preamble); later notes start at their header.
        start = 0 if n.index == 1 else text.find(n.title, cursor)
        if start < 0:
            start = cursor
        end = start + len(n.raw_text)
        cursor = max(cursor, start + 1)
        body = [ln.strip() for ln in n.raw_text.splitlines()[1:] if ln.strip()]
        day = _HOSPITAL_DAY.search(n.raw_text[:400])
        out.append(TimelineNote(
            index=n.index, title=n.title.strip()[:120],
            date="" if n.date_str.startswith("Day ") else n.date_str,
            day=day.group(0).strip("()") if day else "",
            preview=(body[0] if body else "")[:140],
            start=start, end=min(end, len(text)), chars=len(n.raw_text)))
    return out
