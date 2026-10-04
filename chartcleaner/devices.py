"""Lines, drains and airways: what is in, since when, and for how many days.

Reads a chart for the devices an ICU team reviews every day — EVD, lumbar
drain, ICP monitor, arterial line, central line, PICC, dialysis catheter,
endotracheal tube, tracheostomy, Foley, NG/OG/feeding tube, chest tube,
surgical drains — and for each one reports:

* the insertion date when the chart states one near the mention
  ("placed 9/12", "inserted on 09/12/2026", "Insertion date: 9/12"), or the
  line day it states ("line day 4", "EVD day #6");
* the day count against the chart's reference date (insertion day = day 1);
* whether the latest mention says it came out ("removed", "d/c'd", "pulled");
* side and site when written ("R IJ", "left radial").

Everything comes from the chart text; nothing is assumed. ``REVIEW_DAYS``
marks devices worth a "still needed?" look (line-associated infection risk
grows with days in place).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .chart_dates import DATE_RE, day_number, parse_date, reference_date

__all__ = ["DEVICES", "REVIEW_DAYS", "Device", "DeviceReport", "build"]

DEVICES: dict[str, str] = {
    "EVD": r"external ventricular drain|ventriculostomy|\bEVD\b",
    "Lumbar drain": r"lumbar drain|\bLD\b(?= (?:placed|in|clamped|open|draining))",
    "ICP monitor": r"\bICP (?:monitor|bolt)|intraparenchymal monitor|\bcamino\b|\bbolt\b|\blicox\b",
    "Arterial line": r"arterial line|\bart(?:erial)?[- ]line\b|\ba-?line\b|\bart line\b",
    "Central line": (r"central (?:venous )?(?:line|catheter)|\bCVC\b|\bCVL\b|triple[- ]lumen|\bTLC\b"
                     r"|\bIJ (?:line|CVC|catheter|TLC)\b|subclavian (?:line|CVC|catheter)"
                     r"|femoral (?:line|CVC|catheter)"),
    "PICC": r"\bPICC\b|peripherally inserted central catheter",
    "Dialysis catheter": r"dialysis catheter|\bHD catheter\b|\bquinton\b|trialysis|temporary HD line",
    "Endotracheal tube": r"endotracheal tube|\bETT\b|\bintubated\b|\bextubated\b",
    "Tracheostomy": r"tracheostomy|\btrach\b",
    "Foley": r"\bfoley\b|indwelling urinary catheter|urinary catheter",
    "Feeding tube": r"\bNGT?\b|\bOGT?\b|nasogastric|orogastric|dobhoff|\bDHT\b|feeding tube|\bPEG\b",
    "Chest tube": r"chest tube|thoracostomy tube",
    "Surgical drain": r"\bJP drain|jackson[- ]pratt|hemovac|subgaleal drain|subdural drain",
}
_CASE_SENSITIVE = {"Lumbar drain", "Feeding tube"}  # LD / NG / OG are capitals in charts

# devices worth a "still needed?" review from this day on
REVIEW_DAYS = {"Foley": 3, "Central line": 7, "Arterial line": 7, "PICC": 14,
               "Dialysis catheter": 7, "EVD": 7, "Lumbar drain": 5, "Endotracheal tube": 7}

_REMOVED = re.compile(r"\b(?:removed|discontinued|d/c'?d|dc'?d|pulled|taken out|out (?:on|today)|"
                      r"self[- ]extubated|extubated|decannulated|no longer (?:in place|present))\b",
                      re.IGNORECASE)
_PLACED = re.compile(r"\b(?:placed|inserted|intubated|insertion(?: date)?|in place since|since|exchanged|"
                     r"re-?sited|replaced)\b[:\s]*(?:on\s+)?", re.IGNORECASE)
_DAY = re.compile(r"\b(?:line |catheter |device |drain )?day\s*#?\s*(\d{1,3})\b", re.IGNORECASE)
_SIDE = re.compile(r"(?<![A-Za-z])(?:(?P<side>left|right|L|R|Lt|Rt)\s+)?"
                   r"(?P<site>IJ|internal jugular|subclavian|femoral|radial|brachial|axillary|"
                   r"frontal|basilic|cephalic)\b", re.IGNORECASE)


@dataclass
class Device:
    name: str
    placed: date | None = None
    day: int | None = None
    removed: bool = False
    site: str = ""
    mentions: int = 0
    last_line: str = ""
    lines: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        limit = REVIEW_DAYS.get(self.name)
        return bool(limit and not self.removed and self.day is not None and self.day >= limit)

    def to_text(self) -> str:
        bits = [self.name + (f" ({self.site})" if self.site else "")]
        if self.removed:
            bits.append("removed")
        elif self.day is not None:
            bits.append(f"day {self.day}")
        if self.placed:
            bits.append(f"placed {self.placed.strftime('%m/%d')}")
        if self.needs_review:
            bits.append("— still needed?")
        return " · ".join(bits)

    def to_dict(self) -> dict:
        return {"name": self.name, "placed": self.placed.isoformat() if self.placed else None,
                "day": self.day, "removed": self.removed, "site": self.site,
                "needs_review": self.needs_review, "mentions": self.mentions,
                "last_line": self.last_line, "text": self.to_text()}


@dataclass
class DeviceReport:
    reference: date
    devices: list[Device]

    @property
    def active(self) -> list[Device]:
        return [d for d in self.devices if not d.removed]

    def to_text(self) -> str:
        if not self.devices:
            return ""
        return "Lines / drains / airway (as of " + self.reference.strftime("%m/%d") + ")\n" + \
            "\n".join("- " + d.to_text() for d in self.devices)

    def to_dict(self) -> dict:
        return {"reference": self.reference.isoformat(),
                "devices": [d.to_dict() for d in self.devices], "text": self.to_text()}


def _compiled() -> list[tuple[str, re.Pattern]]:
    return [(name, re.compile(rx, 0 if name in _CASE_SENSITIVE else re.IGNORECASE))
            for name, rx in DEVICES.items()]


_PATTERNS = _compiled()


def _site(line: str) -> str:
    m = _SIDE.search(line)
    if not m:
        return ""
    side = (m.group("side") or "").lower()
    side = {"left": "L", "lt": "L", "l": "L", "right": "R", "rt": "R", "r": "R"}.get(side, "")
    site = m.group("site")
    site = "IJ" if site.lower() in ("ij", "internal jugular") else site.lower()
    return f"{side} {site}".strip()


def _placed_date(line: str, ref: date) -> date | None:
    for m in _PLACED.finditer(line):
        tail = line[m.end():m.end() + 24]
        d = DATE_RE.match(tail.lstrip())
        if d:
            found = parse_date(d.group(0), ref)
            if found and found <= ref:
                return found
    return None


def build(text: str, ref: date | None = None) -> DeviceReport:
    """Devices mentioned in ``text``, in the order the chart first mentions them."""
    ref = ref or reference_date(text)
    found: dict[str, Device] = {}
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        for name, rx in _PATTERNS:
            if not rx.search(stripped):
                continue
            dev = found.setdefault(name, Device(name))
            dev.mentions += 1
            dev.last_line = stripped[:240]
            if stripped not in dev.lines and len(dev.lines) < 5:
                dev.lines.append(stripped[:240])
            placed = _placed_date(stripped, ref)
            if placed:
                dev.placed = placed
            day = _DAY.search(stripped)
            if day and not placed:
                n = int(day.group(1))
                if 1 <= n <= 365:
                    dev.day = n
            dev.site = _site(stripped) or dev.site
            # the latest mention decides whether it is still in
            dev.removed = bool(_REMOVED.search(stripped))
    for dev in found.values():
        if dev.placed:
            dev.day = day_number(dev.placed, ref)
    return DeviceReport(ref, list(found.values()))
