"""What happened overnight: the events since the evening, pulled from the chart.

Two sources, both verbatim from the chart:

1. **Event sections** in the most recent note that has one — "Overnight
   events", "Interval events", "24 hour events", "Events overnight",
   "Interval history" — every line until the next heading.
2. **Timed lines** — lines that start with (or carry) a clock time. With a
   date on the line, those within ``hours`` of the latest timed entry count;
   with a time only, lines in the most recent note timed between the evening
   start (19:00) and the morning (08:00) count.

The handoff summary preset gets this block put in front of the chart so the
model starts from the night's events instead of guessing them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .chart_dates import DATE_RE, parse_date, reference_date

__all__ = ["EVENT_HEADINGS", "OvernightEvent", "OvernightReport", "build"]

EVENT_HEADINGS = (r"overnight events?", r"events? overnight", r"interval events?",
                  r"24[- ]?(?:hour|hr|h) events?", r"interval history", r"overnight",
                  r"o/n events?", r"last 24 hours?")
_HEADING = re.compile(rf"^\s*(?:{'|'.join(EVENT_HEADINGS)})\s*[:\-]?\s*(?P<rest>.*)$", re.IGNORECASE)
# a new section: a short line ending in ":" or a known heading word
_NEXT_HEADING = re.compile(r"^\s*(?:[A-Z][A-Za-z /&]{2,40}:\s*$|(?:Subjective|Objective|Vitals|Labs|"
                           r"Exam|Physical Exam|Assessment|Plan|Assessment\s*(?:&|and)\s*Plan|"
                           r"Medications|Imaging|ROS|Review of Systems)\b\s*:?)")
_TIME = re.compile(r"(?<![\d:/])(?P<h>[01]?\d|2[0-3]):?(?P<m>[0-5]\d)(?:\s*(?P<ap>[AaPp])\.?[Mm]\.?)?"
                   r"(?![\d:/%])(?!\s*(?:mg|mL|ml|cc|units?|mcg|hrs?|hours?|min))")
_TIMED_LINE = re.compile(r"^\s*(?:[-•*]\s*)?(?:(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*[@,]?\s*)?"
                         r"(?P<time>(?:[01]?\d|2[0-3]):?[0-5]\d(?:\s*[AaPp]\.?[Mm]\.?)?)\b\s*[-:–]?\s*(?P<text>\S.*)$")


@dataclass
class OvernightEvent:
    when: str
    text: str
    source: str   # "section" | "timed"

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class OvernightReport:
    events: list[OvernightEvent] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.events

    def to_text(self) -> str:
        if not self.events:
            return ""
        lines = ["Overnight events"]
        for e in self.events:
            lines.append(f"- {e.when + ' ' if e.when else ''}{e.text}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"events": [e.to_dict() for e in self.events], "text": self.to_text()}


def _hour(raw: str) -> int | None:
    m = _TIME.fullmatch(raw.strip())
    if not m:
        return None
    hour = int(m.group("h"))
    ap = (m.group("ap") or "").lower()
    if ap == "p" and hour < 12:
        hour += 12
    elif ap == "a" and hour == 12:
        hour = 0
    return hour


def _last_note(text: str) -> str:
    from .delta_engine import _split_into_notes
    notes = _split_into_notes(text)
    return notes[-1].raw_text if notes else text


def _section_events(text: str) -> list[OvernightEvent]:
    from .delta_engine import _split_into_notes
    notes = _split_into_notes(text) or []
    for note in reversed(notes or []):
        lines = note.raw_text.split("\n")
        for i, line in enumerate(lines):
            m = _HEADING.match(line)
            if not m:
                continue
            out = []
            rest = m.group("rest").strip()
            if rest:
                out.append(OvernightEvent("", rest, "section"))
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    if out:
                        break
                    continue
                if _NEXT_HEADING.match(nxt) or _HEADING.match(nxt):
                    break
                out.append(OvernightEvent("", nxt.strip().lstrip("-•* ").strip(), "section"))
            if out:
                return out
    return []


def _timed_events(text: str, hours: int, evening: int, morning: int) -> list[OvernightEvent]:
    ref = reference_date(text)
    dated: list[tuple[datetime, str, str]] = []
    undated: list[tuple[str, str]] = []
    for line in _last_note(text).split("\n"):
        m = _TIMED_LINE.match(line)
        if not m:
            continue
        hour = _hour(m.group("time"))
        if hour is None:
            continue
        minute = int(re.sub(r"\D", "", m.group("time"))[-2:])
        body = m.group("text").strip()
        if m.group("date"):
            d = parse_date(m.group("date"), ref)
            if d:
                dated.append((datetime(d.year, d.month, d.day, hour, minute), m.group("time"), body))
                continue
        undated.append((m.group("time"), body) if (hour >= evening or hour < morning) else ("", ""))
    out = []
    if dated:
        latest = max(dt for dt, _, _ in dated)
        out += [OvernightEvent(f"{dt.strftime('%m/%d')} {when}", body, "timed")
                for dt, when, body in sorted(dated) if latest - dt <= timedelta(hours=hours)]
    out += [OvernightEvent(when, body, "timed") for when, body in undated if when]
    return out


def build(text: str, *, hours: int = 12, evening: int = 19, morning: int = 8) -> OvernightReport:
    """Overnight events in ``text`` (most recent note first for sections)."""
    events = _section_events(text)
    seen = {e.text for e in events}
    for e in _timed_events(text, hours, evening, morning):
        if e.text not in seen:
            events.append(e)
            seen.add(e.text)
    return OvernightReport(events)
