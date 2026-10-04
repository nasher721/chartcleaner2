"""ICU bundle check: which daily-care items the latest note never mentions.

Reads the most recent note in a chart and, for each item an ICU team runs
through on rounds (the "FAST HUGS BID" list), reports one of:

* **addressed** — a line of the note mentions it (that line, verbatim, is
  shown; "held" or "contraindicated" counts — a stated decision is a decision);
* **not mentioned** — nothing in the note speaks to it;
* **review** — lines/catheters past their "still needed?" day
  (:data:`chartcleaner.devices.REVIEW_DAYS`) and antibiotics on day 3+ with no
  stated duration or stop date;
* **n/a** — the item only applies to some patients (sedation vacation and
  breathing trials to ventilated or sedated patients) and this one isn't.

Nothing is inferred beyond the chart's words: "not mentioned" means only
that, never "not done". The check never edits the chart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["ITEMS", "ADDRESSED", "MISSING", "REVIEW", "NOT_APPLICABLE", "BundleItem",
           "BundleReport", "build", "latest_note"]

ADDRESSED, MISSING, REVIEW, NOT_APPLICABLE = "addressed", "not mentioned", "review", "n/a"

# item -> (case-insensitive terms, case-sensitive terms) that count as the note
# speaking to it. Short abbreviations that are also everyday words or other
# findings (SAT vs "O2 sat", PT vs the PT/INR lab, BG, BM, TF) match only as
# written in capitals.
ITEMS: dict[str, tuple[str, str]] = {
    "VTE prophylaxis": (
        r"\b(?:dvt|vte)\s*(?:ppx|prophylaxis|proph)\b|\bchemo-?prophylaxis\b|\bchemical ppx\b"
        r"|\bheparin\s+(?:5,?000|7,?500|sq|subq|sc|s\.c\.|gtt|drip|infusion)\b"
        r"|\benoxaparin\b|\blovenox\b|\bapixaban\b|\beliquis\b|\brivaroxaban\b|\bxarelto\b"
        r"|\bsequential compression\b|\banticoagulat\w*",
        r"\bSQH\b|\bSCDs?\b"),
    "GI prophylaxis": (
        r"\b(?:gi|stress ulcer)\s*(?:ppx|prophylaxis)\b|\bpantoprazole\b|\bprotonix\b"
        r"|\bfamotidine\b|\bpepcid\b|\bomeprazole\b|\besomeprazole\b|\bH2 ?blocker\b",
        r"\bPPI\b|\bSUP\b"),
    "Nutrition": (
        r"\bdiet\b|\bnpo\b|\btube feed\w*|\bfeeds?\b|\bnutrition\b|\btpn\b|\bdobhoff\b"
        r"|\bnepro\b|\bjevity\b|\bosmolite\b|\bglucerna\b|\bdysphagia\b|\bswallow\w*",
        r"\bTFs?\b|\bPEG\b(?! ?3350)|\bSLP\b"),
    "Glucose control": (
        r"\bglucose\b|\binsulin\b|\bsliding scale\b|\bfsbg\b|\baccu-?chek\w*|\bfingerstick\w*"
        r"|\bhyperglycemi\w*|\bblood sugar",
        r"\bSSI\b|\bBG\b|\bPOC ?G\b"),
    "Bowel regimen": (
        r"\bbowel (?:regimen|reg|protocol)\b|\bsenna\w*|\bdocusate\b|\bcolace\b|\bmiralax\b"
        r"|\bpolyethylene glycol\b|\bpeg ?3350\b|\bbisacodyl\b|\bdulcolax\b|\blactulose\b"
        r"|\bbowel movement|\bconstipat\w*",
        r"\bBMs?\b"),
    "Analgesia / sedation goal": (
        r"\brass\b|\bcpot\b|\bsedat\w*|\banalges\w*|\bpain (?:control|regimen|score)\b"
        r"|\bpropofol\b|\bdexmedetomidine\b|\bprecedex\b|\bfentanyl\b|\bmidazolam\b|\bketamine\b"
        r"|\bacetaminophen\b|\btylenol\b|\boxycodone\b|\bhydromorphone\b|\bmorphine\b",
        ""),
    "Sedation vacation (SAT)": (
        r"\bsedation (?:holiday|vacation|interruption|hold)\b|\bdaily (?:awakening|wake)\w*"
        r"|\bspontaneous awakening\b|\bwake-?up trial\b|\bsedation off\b|\boff sedation\b",
        r"\bSAT\b"),
    "Breathing trial (SBT)": (
        r"\bsbt\b|\bspontaneous breathing trial\b|\bwean\w*|\bextubat\w*|\bpressure support trial\b"
        r"|\bcpap trial\b|\btrach collar trial\b|\bliberat\w* from",
        r"\bPSV\b"),
    "Head of bed": (r"\bhob\b|\bhead of (?:the )?bed\b|\bhead elevat\w*|\belevate(?:d)? (?:the )?head\b",
                    ""),
    "Code status": (
        r"\bcode status\b|\bfull code\b|\bdnr\b|\bdni\b|\bdnar\b|\bcomfort care\b"
        r"|\bcomfort measures\b|\bpolst\b|\bmolst\b|\bgoals of care\b|\bgoc\b",
        r"\bCMO\b|\bAND\b(?= order)"),
    "Mobility": (
        r"\bphysical therapy\b|\bmobiliz\w*|\bmobility\b|\bout of bed\b|\bambulat\w*"
        r"|\bactivity\b(?=:| as tolerated| level)|\bbed ?rest\b|\bdangle\w*",
        r"\bPT/OT\b|\bPT\b(?= (?:eval|consult|following|recs|to see|/))|\bOT\b(?= (?:eval|consult))"
        r"|\bOOB\b"),
}

# items that only apply to ventilated (and, for SAT, continuously sedated) patients
_VENTILATED = re.compile(
    r"\b(?:ventilator|vent settings|mechanical(?:ly)? ventilat\w*|on the vent|AC/VC|PRVC|SIMV"
    r"|PEEP|FiO2)\b", re.IGNORECASE)
_SEDATIVE_DRIP = re.compile(
    r"\b(?:propofol|dexmedetomidine|precedex|midazolam|versed|fentanyl|ketamine)\b"
    r"(?=[^.\n]{0,40}\b(?:gtt|drip|infusion|mcg/kg/(?:min|hr)|mg/(?:hr|kg/hr)|mcg/hr))",
    re.IGNORECASE)

# a mention that is only history / negated doesn't address an item
_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?[A-Za-z][A-Za-z /&]{1,40}:\s*$")
_DURATION = re.compile(r"\b(?:day\s*\d+\s*(?:of|/)\s*\d+|\d+\s*(?:-|to)?\s*\d*\s*days?\s*(?:total|course)"
                       r"|(?:for|x)\s*\d+\s*(?:days?|d)\b|duration|stop date|end date|through \d"
                       r"|until \d|last dose|de-?escalat\w*|narrow\w*)", re.IGNORECASE)


def _compile(insensitive: str, sensitive: str) -> re.Pattern:
    parts = [f"(?i:{insensitive})"] if insensitive else []
    if sensitive:
        parts.append(f"(?:{sensitive})")
    return re.compile("|".join(parts))


_PATTERNS = {name: _compile(*terms) for name, terms in ITEMS.items()}


@dataclass
class BundleItem:
    name: str
    status: str
    line: str = ""                                   # the note line that addresses it
    details: list[str] = field(default_factory=list)  # for "review": what to look at

    def to_text(self) -> str:
        if self.status == REVIEW:
            return f"{self.name}: " + "; ".join(self.details)
        if self.status == ADDRESSED:
            return f"{self.name}: {self.line}"
        return self.name

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "line": self.line,
                "details": list(self.details), "text": self.to_text()}


@dataclass
class BundleReport:
    items: list[BundleItem]
    ventilated: bool = False
    sedated: bool = False

    def by_status(self, status: str) -> list[BundleItem]:
        return [i for i in self.items if i.status == status]

    @property
    def gaps(self) -> list[BundleItem]:
        """Items worth a look: not mentioned, or flagged for review."""
        return [i for i in self.items if i.status in (MISSING, REVIEW)]

    @property
    def empty(self) -> bool:
        return not self.items

    def to_text(self) -> str:
        if not self.items:
            return ""
        out = ["ICU bundle check (latest note)"]
        missing = self.by_status(MISSING)
        if missing:
            out.append("Not mentioned: " + ", ".join(i.name for i in missing))
        out.extend("Review — " + i.to_text() for i in self.by_status(REVIEW))
        addressed = self.by_status(ADDRESSED)
        if addressed:
            out.append("Addressed: " + ", ".join(i.name for i in addressed))
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"ventilated": self.ventilated, "sedated": self.sedated,
                "items": [i.to_dict() for i in self.items],
                "gaps": [i.name for i in self.gaps], "text": self.to_text()}


def latest_note(text: str) -> str:
    """The last note of a multi-note chart (the whole text for a single note)."""
    from .delta_engine import _split_into_notes
    notes = _split_into_notes(text)
    return notes[-1].raw_text if notes else text


def _first_line(rx: re.Pattern, lines: list[str]) -> str:
    """The first non-heading line ``rx`` matches — plan lines (A&P) preferred."""
    hits = [ln.strip() for ln in lines if ln.strip() and not _HEADING.match(ln) and rx.search(ln)]
    return hits[0][:240] if hits else ""


def _drug_words(name: str, line: str) -> str:
    """The part of ``line`` about antibiotic ``name``: from its mention to the next
    drug or sentence end (so "Vancomycin day 3 of 7" doesn't speak for cefepime)."""
    from .micro import _ALIASES, _DRUG
    hits = list(_DRUG.finditer(line))
    for i, m in enumerate(hits):
        if _ALIASES.get(m.group(1).lower()) != name:
            continue
        end = hits[i + 1].start() if i + 1 < len(hits) else len(line)
        words = line[m.start():end]
        cut = re.search(r"[.;](?:\s|$)", words[len(m.group(0)):])
        return words[:len(m.group(0)) + cut.start()] if cut else words
    return line


def build(text: str) -> BundleReport:
    """The bundle check for the latest note of ``text``."""
    from . import devices, micro
    from .problems import logical_lines

    if not text.strip():
        return BundleReport([])
    note = latest_note(text)
    lines = logical_lines(note)
    dev = devices.build(note)
    ett = next((d for d in dev.devices if d.name == "Endotracheal tube"), None)
    trach = next((d for d in dev.devices if d.name == "Tracheostomy"), None)
    ventilated = bool((ett and not ett.removed) or _VENTILATED.search(note)
                      or (trach and not trach.removed and _VENTILATED.search(note)))
    sedated = bool(_SEDATIVE_DRIP.search(note))

    items: list[BundleItem] = []
    for name, rx in _PATTERNS.items():
        if name == "Breathing trial (SBT)" and not ventilated:
            items.append(BundleItem(name, NOT_APPLICABLE))
            continue
        if name == "Sedation vacation (SAT)" and not (ventilated or sedated):
            items.append(BundleItem(name, NOT_APPLICABLE))
            continue
        line = _first_line(rx, lines)
        items.append(BundleItem(name, ADDRESSED if line else MISSING, line))

    stale = [d for d in dev.devices if d.needs_review]
    if stale:
        items.append(BundleItem("Lines / catheters", REVIEW,
                                details=[f"{d.name}{' (' + d.site + ')' if d.site else ''} day {d.day}"
                                         " — still needed?" for d in stale]))
    abx = micro.build(note)
    open_ended = [a for a in abx.antibiotics
                  if not a.stopped and a.day is not None and a.day >= 3
                  and not _DURATION.search(_drug_words(a.name, a.last_line))]
    if open_ended:
        items.append(BundleItem("Antibiotic duration", REVIEW,
                                details=[f"{a.name} day {a.day} — no stop date or duration stated"
                                         for a in open_ended]))
    return BundleReport(items, ventilated=ventilated, sedated=sedated)
