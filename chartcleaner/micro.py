"""Antibiotic days and culture results from a chart.

**Antibiotics** — each antimicrobial the chart names (generic or common brand
name), with:

* its start date when written near it ("cefepime started 9/12", "since
  9/12", "(9/12 - )"), giving *day N* against the chart's reference date; or
  the day the chart states ("cefepime day 3", "D3 vancomycin");
* stopped when the latest mention says so ("stopped", "discontinued",
  "completed", "d/c").

**Cultures** — blood / urine / sputum / CSF / wound / BAL / tracheal aspirate
/ stool / C. diff, with the date when written, and the result words on the
same line: pending, no growth (to date / at 48 h), negative, positive, and the
organism or Gram stain if named.

Nothing is inferred beyond the words in the chart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .chart_dates import DATE_RE, day_number, parse_date, reference_date

__all__ = ["ANTIMICROBIALS", "Antibiotic", "Culture", "MicroReport", "build"]

# canonical name -> extra spellings (brand names, abbreviations)
ANTIMICROBIALS: dict[str, tuple[str, ...]] = {
    "vancomycin": ("vanc", "vanco"), "cefepime": ("maxipime",), "ceftriaxone": ("rocephin",),
    "cefazolin": ("ancef",), "ceftazidime": (), "ceftaroline": (), "cefuroxime": (),
    "piperacillin-tazobactam": ("piperacillin/tazobactam", "pip-tazo", "pip/tazo", "zosyn"),
    "meropenem": ("merrem",), "ertapenem": ("invanz",), "imipenem": (),
    "ampicillin-sulbactam": ("ampicillin/sulbactam", "unasyn"), "ampicillin": (),
    "nafcillin": (), "oxacillin": (), "penicillin": (), "amoxicillin": (),
    "amoxicillin-clavulanate": ("augmentin", "amox-clav"),
    "metronidazole": ("flagyl",), "azithromycin": ("zithromax",), "doxycycline": (),
    "levofloxacin": ("levaquin",), "ciprofloxacin": ("cipro",), "moxifloxacin": (),
    "linezolid": ("zyvox",), "daptomycin": ("cubicin",), "clindamycin": (),
    "trimethoprim-sulfamethoxazole": ("tmp-smx", "bactrim", "septra"),
    "gentamicin": (), "tobramycin": (), "amikacin": (), "aztreonam": (), "rifampin": (),
    "nitrofurantoin": ("macrobid",), "acyclovir": (), "valacyclovir": (), "ganciclovir": (),
    "fluconazole": ("diflucan",), "micafungin": (), "voriconazole": (), "amphotericin": (),
    "oseltamivir": ("tamiflu",), "cefiderocol": (), "ceftazidime-avibactam": ("avycaz",),
}

_ALIASES = {alias: name for name, spellings in ANTIMICROBIALS.items()
            for alias in (name, *spellings)}
_DRUG = re.compile(r"(?<![A-Za-z])(" + "|".join(
    re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True)) + r")(?![A-Za-z])", re.IGNORECASE)
_START = re.compile(r"\b(?:started|start(?:ed)? on|start date|since|began|initiated|restarted)\b[:\s]*"
                    r"(?:on\s+)?", re.IGNORECASE)
_DAY = re.compile(r"\b(?:day\s*#?\s*(\d{1,2})|D(\d{1,2}))\b")
_STOP = re.compile(r"\b(?:stopped|discontinued|d/c'?d|dc'?d|completed|course complete|"
                   r"finished|held)\b", re.IGNORECASE)

_SPECIMEN = re.compile(
    r"(?<![A-Za-z])(?P<spec>blood|urine|sputum|csf|wound|bal|bronchoalveolar lavage|"
    r"tracheal aspirate|respiratory|stool|c\.?\s?diff(?:icile)?|mrsa nares)\s*"
    r"(?:cx|cultures?|gram stain|pcr|swab)?s?\b(?=.{0,60}?(?:cx|culture|pending|growth|positive|"
    r"negative|grew|growing|gram|ngtd|\bpcr\b|nares))", re.IGNORECASE)
_RESULT = re.compile(
    r"\b(?P<res>no growth(?: to date| at \d+\s*(?:h|hr|hrs|hours|days?))?|ngtd|pending|"
    r"negative|positive|contaminant|grew|growing|gram[- ](?:positive|negative)\s+\w+)\b", re.IGNORECASE)
_ORGANISM = re.compile(
    r"\b(?:mrsa|mssa|e\.?\s?coli|escherichia coli|klebsiella(?: \w+)?|pseudomonas(?: aeruginosa)?|"
    r"enterococcus(?: \w+)?|vre|staph(?:ylococcus)? aureus|coag(?:ulase)?[- ]negative staph\w*|"
    r"strep(?:tococcus)? \w+|candida(?: \w+)?|enterobacter(?: \w+)?|serratia(?: \w+)?|"
    r"proteus(?: \w+)?|acinetobacter(?: \w+)?|c\.?\s?diff(?:icile)?)\b", re.IGNORECASE)


@dataclass
class Antibiotic:
    name: str
    start: date | None = None
    day: int | None = None
    stopped: bool = False
    last_line: str = ""

    def to_text(self) -> str:
        bits = [self.name]
        if self.stopped:
            bits.append("stopped")
        elif self.day is not None:
            bits.append(f"day {self.day}")
        if self.start:
            bits.append(f"started {self.start.strftime('%m/%d')}")
        return " · ".join(bits)

    def to_dict(self) -> dict:
        return {"name": self.name, "start": self.start.isoformat() if self.start else None,
                "day": self.day, "stopped": self.stopped, "last_line": self.last_line,
                "text": self.to_text()}


@dataclass
class Culture:
    specimen: str
    when: date | None
    result: str
    organism: str = ""
    line: str = ""

    def to_text(self) -> str:
        when = f" {self.when.strftime('%m/%d')}" if self.when else ""
        res = self.result or "result not stated"
        return f"{self.specimen} culture{when}: {res}" + (f" — {self.organism}" if self.organism else "")

    def to_dict(self) -> dict:
        return {"specimen": self.specimen, "date": self.when.isoformat() if self.when else None,
                "result": self.result, "organism": self.organism, "line": self.line,
                "text": self.to_text()}


@dataclass
class MicroReport:
    reference: date
    antibiotics: list[Antibiotic]
    cultures: list[Culture] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.antibiotics and not self.cultures

    def to_text(self) -> str:
        out = []
        if self.antibiotics:
            out.append("Antimicrobials (as of " + self.reference.strftime("%m/%d") + ")")
            out.extend("- " + a.to_text() for a in self.antibiotics)
        if self.cultures:
            out.append("Cultures")
            out.extend("- " + c.to_text() for c in self.cultures)
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"reference": self.reference.isoformat(),
                "antibiotics": [a.to_dict() for a in self.antibiotics],
                "cultures": [c.to_dict() for c in self.cultures], "text": self.to_text()}


def _date_after(pattern: re.Pattern, line: str, ref: date) -> date | None:
    for m in pattern.finditer(line):
        d = DATE_RE.match(line[m.end():m.end() + 24].lstrip())
        if d:
            found = parse_date(d.group(0), ref)
            if found and found <= ref:
                return found
    return None


def _specimen_name(raw: str) -> str:
    key = raw.lower().replace(".", "").replace(" ", "")
    if key.startswith("cdiff"):
        return "C. diff"
    return {"csf": "CSF", "bal": "BAL", "bronchoalveolarlavage": "BAL",
            "mrsanares": "MRSA nares"}.get(key, raw.strip().capitalize())


def build(text: str, ref: date | None = None) -> MicroReport:
    ref = ref or reference_date(text)
    abx: dict[str, Antibiotic] = {}
    cultures: dict[tuple[str, date | None], Culture] = {}
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        hits = list(_DRUG.finditer(stripped))
        for i, m in enumerate(hits):
            name = _ALIASES[m.group(1).lower()]
            item = abx.setdefault(name, Antibiotic(name))
            item.last_line = stripped[:240]
            # this drug's own words: from just before it up to the next drug or sentence end
            prev_end = hits[i - 1].end() if i else 0
            next_start = hits[i + 1].start() if i + 1 < len(hits) else len(stripped)
            tail = stripped[m.start():next_start]
            cut = re.search(r"[.;](?:\s|$)", tail[len(m.group(0)):])
            if cut:
                tail = tail[:len(m.group(0)) + cut.start()]
            window = stripped[max(prev_end, m.start() - 20):m.start()] + tail
            start = _date_after(_START, window, ref)
            if start is None:
                paren = re.search(r"\((\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*[-–]\s*\)", window)
                if paren:
                    start = parse_date(paren.group(1), ref)
            if start:
                item.start = start
            else:
                day = _DAY.search(window)
                if day:
                    n = int(day.group(1) or day.group(2))
                    if 1 <= n <= 90:
                        item.day = n
            item.stopped = bool(_STOP.search(window))
        for m in _SPECIMEN.finditer(stripped):
            spec = _specimen_name(m.group("spec"))
            tail = stripped[m.start():]
            dates = [d for d in DATE_RE.finditer(tail[:80])]
            when = parse_date(dates[0].group(0), ref) if dates else None
            res = _RESULT.search(tail)
            org = _ORGANISM.search(tail)
            organism = org.group(0) if org and _specimen_name(org.group(0)) != spec else ""
            result = res.group("res") if res else ""
            if not (result or organism) and any(k[0] == spec for k in cultures):
                continue  # a passing mention ("follow sputum culture") adds nothing
            key = (spec, when)
            old = cultures.get(key)
            if old is None or result or organism:
                cultures[key] = Culture(spec, when, result or (old.result if old else ""),
                                        organism or (old.organism if old else ""), stripped[:240])
    for item in abx.values():
        if item.start:
            item.day = day_number(item.start, ref)
    return MicroReport(ref, list(abx.values()), list(cultures.values()))
