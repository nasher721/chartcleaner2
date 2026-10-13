"""Stale copy-forward lines: time-bound text carried forward unchanged.

The copy-forward view (:mod:`chartcleaner.delta_engine`) drops sentences that
repeat an earlier note. This module looks at the opposite risk: lines in the
**latest** note that were copied word for word from the notes before it even
though they only make sense on one day, such as "plan to extubate tomorrow",
"POD #2", "repeat CT in AM", "Vancomycin day 3 of 7" or "MRI pending".

A line is flagged when it appears unchanged in the latest note and at least
``min_notes - 1`` notes right before it, and it carries one of those time-bound
words. Each flag shows the line verbatim, how many notes in a row carry it and
the date of the first one. Nothing is removed; the reader decides whether the
line is still true.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["MIN_NOTES", "StaleLine", "StaleReport", "build"]

MIN_NOTES = 3  # the latest note plus at least two before it

# words that tie a line to the day it was written
_TIME_BOUND = re.compile(
    r"\b(?:tomorrow|tmrw|tmw|today|tonight|this (?:morning|afternoon|evening)|in (?:the )?am"
    r"|overnight|o/n|last night|yesterday|y/d|currently|pending|awaiting|planned for"
    r"|plan(?:ning)? (?:to|for)|will (?:\w+)|scheduled|pre-?op"
    r"|POD\s*#?\s*\d+|HD\s*#?\s*\d+|(?:hospital|post-?\w+|line|catheter|abx|antibiotic|vent) day\s*#?\s*\d+"
    r"|day\s*#?\s*\d+\s*(?:of|/)\s*\d+|day\s*#\s*\d+)\b",
    re.IGNORECASE)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?[A-Za-z][A-Za-z /&]{1,40}:\s*$")
_BULLET = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+")
_MIN_CHARS = 12


@dataclass
class StaleLine:
    text: str
    notes: int        # notes in a row (ending with the latest) that carry the line
    since: str        # date (or "Day n") of the first of them
    word: str         # the time-bound word that makes it worth a look

    def to_text(self) -> str:
        return f"{self.text}  [same in {self.notes} notes since {self.since}; says “{self.word}”]"

    def to_dict(self) -> dict:
        return {"text": self.text, "notes": self.notes, "since": self.since, "word": self.word}


@dataclass
class StaleReport:
    lines: list[StaleLine] = field(default_factory=list)
    notes_found: int = 0

    @property
    def empty(self) -> bool:
        return not self.lines

    def to_text(self) -> str:
        if not self.lines:
            return ""
        out = ["Possibly stale (copied forward unchanged)"]
        out.extend(f"- {s.to_text()}" for s in self.lines)
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"notes_found": self.notes_found, "lines": [s.to_dict() for s in self.lines],
                "text": self.to_text()}


def _body(line: str) -> str:
    return _BULLET.sub("", line.strip())


def _norm(line: str) -> str:
    return " ".join(_body(line).casefold().split()).rstrip(" .;,")


def _note_lines(raw: str) -> list[str]:
    from .safety import lines_of
    return [ln for ln in lines_of(raw)
            if len(ln.strip()) >= _MIN_CHARS and not _HEADING.match(ln)]


def build(text: str, min_notes: int = MIN_NOTES) -> StaleReport:
    """Time-bound lines of the latest note copied unchanged from the notes before it."""
    from .delta_engine import _split_into_notes

    notes = _split_into_notes(text) if text.strip() else []
    if len(notes) < max(2, min_notes):
        return StaleReport([], len(notes))
    earlier = [{_norm(ln) for ln in _note_lines(n.raw_text)} for n in notes[:-1]]
    out: list[StaleLine] = []
    seen: set[str] = set()
    for line in _note_lines(notes[-1].raw_text):
        key = _norm(line)
        if key in seen:
            continue
        word = _TIME_BOUND.search(line)
        if not word:
            continue
        run = 1
        for lines in reversed(earlier):
            if key not in lines:
                break
            run += 1
        if run < min_notes:
            continue
        seen.add(key)
        first = notes[len(notes) - run]
        out.append(StaleLine(_body(line)[:240], run, first.date_str or f"note {first.index}",
                             word.group(0)))
    return StaleReport(out, len(notes))
