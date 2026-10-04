"""Split a multi-patient dump (sign-out list, census, rounding sheet) per patient.

A paste of a whole list usually separates patients one of these ways; the
kind found most often (at least twice) wins:

* **bed labels** at the start of a line — ``G20-1``, ``H22-2``, ``Bed 4``,
  ``Rm 12``, ``Room 412B``;
* **patient lines** — ``Patient: …``, ``Name: …``, ``Pt: …``;
* **numbered patients** — ``1)``, ``#2``, ``Patient 3`` at the start of a line
  followed by text;
* **separator lines** — ``-----``, ``=====``, ``_____``, ``*****``.

Text before the first boundary is kept as a "Header" chunk only when it has
real content. Each chunk keeps its own text unchanged so it can be cleaned
like any chart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["PatientChunk", "split", "BOUNDARIES"]

BOUNDARIES: dict[str, re.Pattern] = {
    "bed": re.compile(r"^\s*(?P<label>(?:[A-Z]{1,3}\d{1,3}-\d{1,2}[A-Z]?|(?:Bed|Rm|Room)\s*#?\s*\d{1,4}[A-Z]?))"
                      r"(?=[\s:,.\-]|$)", re.MULTILINE),
    "patient": re.compile(r"^\s*(?:Patient|Pt|Name)\s*[:#]\s*(?P<label>\S.{0,40}?)\s*$", re.MULTILINE | re.IGNORECASE),
    "number": re.compile(r"^\s*(?P<label>(?:#\s?\d{1,2}|\d{1,2}\)|Patient\s+\d{1,2}))(?=\s+\S)", re.MULTILINE),
    "separator": re.compile(r"^\s*(?P<label>[-=_*]{5,})\s*$", re.MULTILINE),
}
# when two kinds tie, prefer the more specific one
_PRIORITY = ("bed", "patient", "number", "separator")


@dataclass
class PatientChunk:
    index: int
    label: str
    text: str
    start: int

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _kind(text: str) -> tuple[str, list[re.Match]] | None:
    best: tuple[str, list[re.Match]] | None = None
    for kind in _PRIORITY:
        hits = list(BOUNDARIES[kind].finditer(text))
        if len(hits) >= 2 and (best is None or len(hits) > len(best[1])):
            best = (kind, hits)
    return best


def split(text: str) -> list[PatientChunk]:
    """Per-patient chunks; a single chunk when no boundary repeats."""
    found = _kind(text)
    if found is None:
        return [PatientChunk(1, "Patient 1", text, 0)] if text.strip() else []
    kind, hits = found
    chunks: list[PatientChunk] = []
    head = text[:hits[0].start()]
    if kind == "separator" and head.strip():
        chunks.append(PatientChunk(0, "", head.strip("\n"), 0))  # the first patient sits above the first line
    elif head.strip() and len(head.strip()) > 20:
        chunks.append(PatientChunk(0, "Header", head.strip("\n"), 0))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        start = m.end() if kind == "separator" else m.start()
        body = text[start:end]
        if not body.strip():
            continue
        label = "" if kind == "separator" else " ".join(m.group("label").split())
        chunks.append(PatientChunk(i + 1, label, body.strip("\n"), start))
    if kind == "separator":
        for n, c in enumerate(chunks, start=1):
            c.label = f"Patient {n}"
    return chunks
