"""Lab trends and medication changes across the notes in one chart.

A chart that carries several daily notes is split the way the delta engine
splits it (note headers with dates). For each note this module reads:

* **labs** — the last value of every known lab (``Na 141``, ``K: 3.9``,
  lab-compaction lines like ``BMP: Na 132 (L), K 4.1`` and trends like
  ``Na 138 → 135``), named by the lab-compaction aliases;
* **medications** — every line inside a medication section, normalized the
  way the ``med_normalize`` stage would write it.

and reports::

    Lab trends (09/13 → 09/14 → 09/15)
    Na: 128 → 132 → 135 ↑
    Cr: 1.4 → — → 1.1 ↓

    Medication changes
    09/13 → 09/14: started nimodipine 60 mg PO q4h; stopped cefazolin 2 g IV q8h
    09/14 → 09/15: changed metoprolol 25 mg PO BID → 50 mg PO BID

Only labs seen in two or more notes are listed. Nothing is inferred: a value
missing from a note shows as ``—``. It reads the *cleaned* text, so dates
removed by the cleaner never come back here (notes are then "Note 1, 2, …").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .compactors.labs import LABS, PANEL_ORDER
from .compactors.meds import _STRENGTH, medication_list
from .delta_engine import _split_into_notes
from .fact_check import _is_drug, _mask

__all__ = ["LabTrend", "MedChange", "TrendReport", "build"]

# alias (lowercase) -> canonical short name
_ALIASES: dict[str, str] = {alias: short for short, (_, names) in LABS.items() for alias in names}
_ALIASES.update({short.lower(): short for short in LABS})
# Short aliases that are ordinary words or abbreviations in prose ("Pt", "ca"):
# they count only when written the way labs are (first letter capital; PT in caps).
_CASE_SENSITIVE = {"na": "Na", "k": "K", "cl": "Cl", "ca": "Ca", "cr": "Cr", "mg": "Mg",
                   "pt": "PT", "ag": "AG", "alt": "ALT", "ast": "AST", "alp": "ALP", "alb": "Alb",
                   "hb": "Hb", "glu": "Glu", "plt": "Plt"}
_ORDER = {short: i for i, short in enumerate(LABS)}

_NUM = r"[<>]?\d+(?:\.\d+)?"
_VALUE_CHAIN = re.compile(
    rf"(?P<num>{_NUM})(?![\d/])(?:[ \t]*(?:\((?:H|L|HH|LL|A|C)\)|\*)?[ \t]*→[ \t]*(?P<more>{_NUM}))*")
_CHAIN_LAST = re.compile(rf"→[ \t]*({_NUM})")
_LABEL_THEN_NUMBER = re.compile(rf"(?<![\w.])(?=({_NUM})(?![\d/]))")
_SEGMENT_BREAK = re.compile(r"[^A-Za-z0-9\- \t]")


def _lab_name(before: str) -> str | None:
    before = before.rstrip(" \t").rstrip(":=").rstrip(" \t")
    if not before or not before[-1].isalnum():
        return None
    words = _SEGMENT_BREAK.split(before)[-1].split()[-3:]
    for n in (3, 2, 1):
        if len(words) < n:
            continue
        cand = " ".join(words[-n:])
        key = cand.lower()
        if key not in _ALIASES:
            continue
        exact = _CASE_SENSITIVE.get(key)
        if exact and cand not in (exact, exact.upper()):
            continue
        return _ALIASES[key]
    return None


def labs_in(text: str) -> dict[str, str]:
    """Last value of each known lab in ``text`` (dates and times masked)."""
    masked = _mask(text)
    found: dict[str, str] = {}
    for m in _LABEL_THEN_NUMBER.finditer(masked):
        name = _lab_name(masked[max(0, m.start() - 40):m.start()])
        if not name:
            continue
        chain = _VALUE_CHAIN.match(masked, m.start())
        tail = _CHAIN_LAST.findall(chain.group(0)) if chain else []
        found[name] = tail[-1] if tail else m.group(1)
    return found


def _drug_key(line: str) -> str:
    """First word(s) before the dose: "metoprolol tartrate 25 mg …" → "metoprolol tartrate"."""
    head = re.split(r"\s(?=\d)|,|\(", line, maxsplit=1)[0]
    return " ".join(head.lower().split()[:3])


def _looks_like_med(line: str) -> bool:
    """A dose ("60 mg") or a known drug name first — prose that ran on below a
    med list ("Pt 2 days post coiling") is not a medication."""
    first = (line.split() or [""])[0].lower()
    return bool(_STRENGTH.search(line)) or _is_drug(first)


@dataclass
class LabTrend:
    name: str
    values: list[str | None]

    def direction(self) -> str:
        nums = [float(v.lstrip("<>")) for v in self.values if v is not None]
        if len(nums) < 2 or not nums[0]:
            return ""
        change = (nums[-1] - nums[0]) / abs(nums[0])
        return "↑" if change > 0.05 else "↓" if change < -0.05 else ""

    def to_text(self) -> str:
        arrow = self.direction()
        return f"{self.name}: " + " → ".join(v or "—" for v in self.values) + (f" {arrow}" if arrow else "")


@dataclass
class MedChange:
    before: str
    after: str
    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    changed: list[tuple[str, str]] = field(default_factory=list)

    def to_text(self) -> str:
        parts = ([f"started {m}" for m in self.started] + [f"stopped {m}" for m in self.stopped]
                 + [f"changed {a} → {b}" for a, b in self.changed])
        return f"{self.before} → {self.after}: " + "; ".join(parts)


@dataclass
class TrendReport:
    notes: list[str]
    labs: list[LabTrend]
    meds: list[MedChange]

    @property
    def empty(self) -> bool:
        return not self.labs and not self.meds

    def to_text(self) -> str:
        if self.empty:
            return ""
        out: list[str] = []
        if self.labs:
            out.append(f"Lab trends ({' → '.join(self.notes)})")
            out.extend(t.to_text() for t in self.labs)
        if self.meds:
            if out:
                out.append("")
            out.append("Medication changes")
            out.extend(c.to_text() for c in self.meds)
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"notes": list(self.notes),
                "labs": [{"name": t.name, "values": list(t.values), "direction": t.direction()}
                         for t in self.labs],
                "meds": [{"before": c.before, "after": c.after, "started": c.started,
                          "stopped": c.stopped, "changed": [list(p) for p in c.changed]}
                         for c in self.meds],
                "text": self.to_text()}


def _note_label(date_str: str, index: int, all_dates: list[str]) -> str:
    if not date_str or date_str.startswith("Day "):
        return f"Note {index}"
    short = re.sub(r"[/-]\d{2,4}$", "", date_str)  # 09/15/2026 → 09/15
    return short if all_dates.count(date_str) == 1 else f"{short} #{index}"


def build(text: str, cfg: dict | None = None) -> TrendReport:
    """Trends across the notes in ``text``; empty when it holds a single note."""
    notes = _split_into_notes(text)
    if len(notes) < 2:
        return TrendReport([], [], [])
    dates = [n.date_str for n in notes]
    labels = [_note_label(n.date_str, n.index, dates) for n in notes]

    per_note = [labs_in(n.raw_text) for n in notes]
    names = {name for labs in per_note for name in labs}
    trends = [LabTrend(name, [labs.get(name) for labs in per_note]) for name in names
              if sum(name in labs for labs in per_note) >= 2]
    trends.sort(key=lambda t: (PANEL_ORDER.index(LABS[t.name][0]), _ORDER[t.name]))

    changes: list[MedChange] = []
    previous: dict[str, str] = {}
    label_before = ""
    for label, note in zip(labels, notes):
        meds: dict[str, str] = {}
        for line in medication_list(note.raw_text, cfg):
            if _looks_like_med(line):
                meds.setdefault(_drug_key(line), line)
        if previous and meds:  # notes without a med list are skipped, not "stopped everything"
            change = MedChange(label_before, label,
                               started=[meds[k] for k in meds if k not in previous],
                               stopped=[previous[k] for k in previous if k not in meds],
                               changed=[(previous[k], meds[k]) for k in meds
                                        if k in previous and previous[k] != meds[k]])
            if change.started or change.stopped or change.changed:
                changes.append(change)
        if meds:
            previous, label_before = meds, label
    return TrendReport(labels, trends, changes)
