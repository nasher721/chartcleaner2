"""Daily-note mode: today's chart against yesterday's, in one block to paste.

Give it today's cleaned chart and a previous cleaned chart (usually the last
one with the same bed tag, see :mod:`recent_charts`) and it returns:

* **What's new** — lines of today's chart that the previous chart did not
  have (compared ignoring case and spacing), kept under their section
  headings, in today's order. A changed number makes a line new.
* **Trends** — labs, scores and medication changes across the two charts
  (``trends.build`` on both, each labelled with its date).
* **Lines / drains / airway** and **antibiotics & cultures** as of today.
* **Overnight events** from today's chart.

Everything is copied from the charts; nothing is reworded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .chart_dates import reference_date

__all__ = ["DailyNote", "build", "new_lines"]

_HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S.*|[A-Z][A-Za-z0-9 /&()-]{1,40}:\s*)$")


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().lstrip("-•* ").strip()).casefold()


def new_lines(today: str, previous: str) -> list[str]:
    """Today's lines not in ``previous``, with the heading each sits under."""
    from .delta_engine import _DATE_HEADER_RE
    seen = {_norm(line) for line in previous.split("\n") if line.strip()}
    out: list[str] = []
    heading: str | None = None
    heading_used = False
    for line in today.split("\n"):
        if not line.strip():
            continue
        if _HEADING.match(line):
            heading, heading_used = line.strip(), False
            continue
        if _norm(line) in seen or _DATE_HEADER_RE.match(line.strip()):
            continue
        if heading and not heading_used:
            out.append(heading)
            heading_used = True
        out.append(line.rstrip())
    return out


@dataclass
class DailyNote:
    today_date: str
    previous_date: str
    new: list[str]
    trends: str = ""
    devices: str = ""
    micro: str = ""
    overnight: str = ""

    def to_text(self) -> str:
        parts = [f"Daily update {self.today_date} (compared with {self.previous_date or 'the previous chart'})"]
        for block in (self.overnight, self.devices, self.micro, self.trends):
            if block:
                parts.append(block)
        if self.new:
            parts.append("What's new since the previous chart\n" + "\n".join(self.new))
        else:
            parts.append("What's new since the previous chart\n(nothing — today's chart repeats the previous one)")
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {"today_date": self.today_date, "previous_date": self.previous_date,
                "new": list(self.new), "trends": self.trends, "devices": self.devices,
                "micro": self.micro, "overnight": self.overnight, "text": self.to_text()}


def _dated(text: str, when) -> str:
    """Give a chart a recognisable note header so trends can line two charts up."""
    from .delta_engine import _split_into_notes
    if len(_split_into_notes(text)) > 1 or re.search(r"(?im)^(?:Date of Service|Note Date|DOS)\s*:", text):
        return text
    return f"Date of Service: {when.strftime('%m/%d/%Y')}\n{text}"


def build(today: str, previous: str, cfg: dict | None = None) -> DailyNote:
    """The daily update for ``today`` (cleaned) against ``previous`` (cleaned)."""
    from . import devices, micro, overnight, trends

    t_ref, p_ref = reference_date(today), reference_date(previous)
    note = DailyNote(today_date=t_ref.strftime("%m/%d/%Y"),
                     previous_date=p_ref.strftime("%m/%d/%Y") if previous.strip() else "",
                     new=new_lines(today, previous))
    try:
        if previous.strip():
            combined = _dated(previous, p_ref) + "\n\n" + _dated(today, t_ref)
            note.trends = trends.build(combined, cfg).to_text()
    except Exception:
        note.trends = ""
    for attr, module in (("devices", devices), ("micro", micro), ("overnight", overnight)):
        try:
            setattr(note, attr, module.build(today).to_text())
        except Exception:
            setattr(note, attr, "")
    return note
