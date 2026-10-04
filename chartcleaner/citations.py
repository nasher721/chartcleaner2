"""Point every line of an AI summary back to where the chart says it.

For each summary line (headings and blank lines skipped) :func:`cite` finds
the chart line that shares the most of its content words and numbers —
numbers count double, since they are what a reader checks — and returns its
character offsets so the app can jump there. A summary line with nothing
close enough in the chart gets no citation, which is itself a signal to look
twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Citation", "cite", "MIN_SCORE"]

MIN_SCORE = 0.35
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'-]{2,}|\d+(?:\.\d+)?")
_STOP = frozenset("""the and with for was were has have had this that from are not but its
into over under than then also per via all any each few more most other some such only
own same very can will just should now patient patients noted note given continue continued
remains remained stable today yesterday currently current plan assessment""".split())
_HEADING = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*]+\*\*\s*$|[A-Z][A-Za-z /&]{1,40}:\s*$)")


@dataclass
class Citation:
    line: int            # index of the line in the summary
    text: str            # the summary line
    start: int           # offsets of the cited chart line (-1 when none)
    end: int
    source: str          # the cited chart line ("" when none)
    score: float

    @property
    def found(self) -> bool:
        return self.start >= 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _tokens(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for tok in _TOKEN.findall(text):
        low = tok.lower()
        if low in _STOP:
            continue
        out[low] = 2.0 if tok[0].isdigit() else 1.0
    return out


def _chart_lines(chart: str) -> list[tuple[int, int, str, dict[str, float]]]:
    """Logical chart lines (wrapped lines rejoined) with their offsets and tokens."""
    from .problems import continues
    spans: list[list] = []   # [start, end, text, last raw line]
    pos = 0
    for raw in chart.split("\n"):
        stripped = raw.strip()
        if stripped:
            start = pos + raw.find(stripped)
            if spans and spans[-1][1] == pos - 1 - _trail(spans[-1][3]) and continues(spans[-1][3], raw):
                spans[-1][1] = start + len(stripped)
                spans[-1][2] += " " + stripped
                spans[-1][3] = raw
            else:
                spans.append([start, start + len(stripped), stripped, raw])
        pos += len(raw) + 1
    return [(a, b, text, _tokens(text)) for a, b, text, _raw in spans]


def _trail(raw: str) -> int:
    """Trailing whitespace after a line's text (so adjacency survives "  \n")."""
    return len(raw) - len(raw.rstrip())


def cite(summary: str, chart: str, min_score: float = MIN_SCORE) -> list[Citation]:
    """One :class:`Citation` per content line of ``summary`` (offsets index ``chart``)."""
    lines = _chart_lines(chart)
    out: list[Citation] = []
    for i, raw in enumerate(summary.split("\n")):
        text = raw.strip()
        if not text or _HEADING.match(raw):
            continue
        want = _tokens(text)
        total = sum(want.values())
        if not total:
            continue
        best, best_score = None, 0.0
        for entry in lines:
            have = entry[3]
            shared = sum(w for tok, w in want.items() if tok in have)
            score = shared / total
            if score > best_score:
                best, best_score = entry, score
        if best is not None and best_score >= min_score:
            out.append(Citation(i, text, best[0], best[1], best[2], round(best_score, 2)))
        else:
            out.append(Citation(i, text, -1, -1, "", round(best_score, 2)))
    return out
