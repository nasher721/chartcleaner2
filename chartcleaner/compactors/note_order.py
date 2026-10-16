"""Sort multi-note clinical exports chronologically or reverse-chronologically (stage ``note_order``).

Epic Chart Review often exports progress notes newest-first, whereas LLMs,
trend readers, and forward copy-forward deduplication reason best when notes
appear oldest-first (Day 1 → Today) — or vice versa when a clinician wants the
latest note at the very top.

Safety rules:
* Any preamble before the first note header (demographics, admission date,
  safety header) stays anchored at the top of the chart.
* Notes are reordered **only** when at least 2 notes are detected and **every**
  note header carries a parseable date. If any note header lacks a date, the
  entire chart stays in its original order.
* Notes with the same date (and clock time, if present) keep their relative
  order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import re
from typing import Any

from ..chart_dates import parse_date, reference_date

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "direction": "oldest_first",
}

_HD = r"(?:[ \t]*\((?:HD|POD)#[^)]*\))?"
_DATE_TOKEN = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?"
    rf"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?"
    rf"|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?)"
    rf"{_HD}"
)

_NOTE_HEADER_RE = re.compile(
    rf"(?im)^(?:"
    rf"(?:Progress|Consult(?:ation)?|Admission|H&P|History\s+and\s+Physical|Discharge|Daily|SOAP|ICU)\s+Notes?(?:\s+by\s+[^\n]+)?|"
    rf"(?:Note\s+Date|Date\s+of\s+Service|DOS)\s*[:#]\s*{_DATE_TOKEN}[^\n]*|"
    rf"{_DATE_TOKEN}[ \t]+(?:[-–—][ \t]+)?(?:Progress\s+Note|Daily\s+Note|SOAP\s+Note|Consult\s+Note|ICU\s+Note|Note)\b[^\n]*|"
    rf"[A-Z][a-zA-Z,.\s\-]+?\s+at\s+{_DATE_TOKEN}[ \t]+\d{{1,2}}:\d{{2}}[^\n]*"
    rf")$"
)

_TIME_RE = re.compile(
    r"\b(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?:\s*(?P<ampm>[APap]\.?[Mm]\.?))?\b"
    r"|\b(?P<mil>(?:[01]\d|2[0-3])[0-5]\d)\s*(?:hrs?|hours?)?\b"
)


@dataclass
class _ParsedNote:
    orig_idx: int
    sort_dt: datetime
    raw_chunk: str


def _extract_time(header_line: str) -> time:
    # Remove dates first so "10/02/2026" year "2026" is not mistaken for military time 20:26
    stripped = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", header_line)
    m = _TIME_RE.search(stripped)
    if not m:
        return time(0, 0)
    if m.group("mil"):
        mil = m.group("mil")
        return time(int(mil[:2]), int(mil[2:]))
    hh = int(m.group("h"))
    mm = int(m.group("m"))
    ampm = (m.group("ampm") or "").replace(".", "").lower()
    if ampm == "pm" and hh < 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    return time(hh, mm)


def _header_datetime(header_line: str, ref: date) -> datetime | None:
    d = parse_date(header_line, ref=ref)
    if d is None:
        return None
    t = _extract_time(header_line)
    return datetime.combine(d, t)


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("note_order") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    direction = options.get("direction") if options.get("direction") in ("oldest_first", "newest_first") else "oldest_first"

    matches = list(_NOTE_HEADER_RE.finditer(text))
    if len(matches) < 2:
        return text, 0, {}

    ref = reference_date(text)
    preamble = text[:matches[0].start()].strip("\n")
    notes: list[_ParsedNote] = []

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if (i + 1) < len(matches) else len(text)
        chunk = text[start:end].strip("\n")
        header_line = m.group(0).strip().split("\n", 1)[0]
        dt = _header_datetime(header_line, ref)
        if dt is None:
            # Safety rule: never reorder if any note header lacks a parseable date.
            return text, 0, {"skipped": "undated_note"}
        notes.append(_ParsedNote(orig_idx=i, sort_dt=dt, raw_chunk=chunk))

    if direction == "oldest_first":
        ordered = sorted(notes, key=lambda n: (n.sort_dt, n.orig_idx))
    else:
        ordered = sorted(notes, key=lambda n: (-n.sort_dt.timestamp(), n.orig_idx))

    if [n.orig_idx for n in ordered] == [n.orig_idx for n in notes]:
        return text, 0, {"notes": len(notes), "reordered": False}

    body = "\n\n".join(n.raw_chunk for n in ordered)
    rebuilt = f"{preamble}\n\n{body}" if preamble else body
    if text.endswith("\n") and not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return rebuilt, len(notes), {"notes": len(notes), "reordered": True, "direction": direction}
