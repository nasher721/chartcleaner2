"""Safety header: the facts that hurt most when buried, pinned in one block.

Reads a chart for allergies, code status, isolation precautions, weight,
height and antithrombotics (anticoagulants and antiplatelets). For each one it
reports the **most recent** line that states it, verbatim (wrapped lines
rejoined; an "Allergies" heading takes the lines under it), and:

* **changed** — an earlier note states it differently (code status "Full code"
  then "DNR/DNI", a new allergy). The earlier wording is shown next to the
  latest, so the reader decides which is current;
* **not stated** — nothing in the chart says it. Shown for allergies and code
  status only, where silence itself is worth a look. "Not stated" means only
  that, never "none";
* **removed by cleaning** — the cleaned chart no longer says it, but a line a
  cleaning rule removed did (pass those lines as ``removed``; the Clean page
  takes them from the clinical-facts check). The removed line itself is kept
  out of the text block, since it never went through redaction.

Nothing is inferred beyond the chart's words, and the chart is never edited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["ITEMS", "FOUND", "CHANGED", "NOT_STATED", "REMOVED", "removed_lines", "SafetyItem", "SafetyReport", "build", "lines_of"]

FOUND, CHANGED, NOT_STATED, REMOVED = "found", "changed", "not stated", "removed by cleaning"

# item -> pattern for a line that states it
ITEMS: dict[str, re.Pattern] = {
    "Allergies": re.compile(
        r"\ballerg(?:y|ies|ic)\b|\bNKD?A\b|\bALLG\b|\bno known (?:drug )?allerg", re.IGNORECASE),
    "Code status": re.compile(
        r"\bcode status\b|\bfull code\b|\bDNR\b|\bDNI\b|\bDNAR\b|\bcomfort (?:care|measures)(?: only)?\b"
        r"|\bCMO\b|\bPOLST\b|\bMOLST\b|\bno (?:CPR|code|intubation)\b", re.IGNORECASE),
    "Isolation": re.compile(
        r"\b(?:contact|droplet|airborne|enteric|neutropenic|special contact|enhanced contact)"
        r"(?:\s*(?:\+|plus|/)\s*\w+)?\s+(?:precautions?|isolation)\b|\bisolation\b(?! room\b)",
        re.IGNORECASE),
    "Weight": re.compile(
        r"\b(?:dosing |admit(?:ting|ted)? |admission |actual |current |ideal body |body )?"
        r"(?:weight|wt)\b\s*[:=]?\s*(?:\([^)]{0,20}\)\s*)?\d{1,3}(?:\.\d+)?\s*(?:kg|lbs?|pounds)\b",
        re.IGNORECASE),
    "Height": re.compile(
        r"\b(?:height|ht)\b\s*[:=]?\s*(?:\d{2,3}(?:\.\d+)?\s*(?:cm|in(?:ches)?)\b"
        r"|\d\s*(?:'|ft)\s*\d{1,2}\s*(?:\"|in)?|1\.\d{1,2}\s*m\b)", re.IGNORECASE),
    "Antithrombotics": re.compile(
        r"\bhep(?:arin)?\b(?=[^.\n]{0,30}\b(?:gtt|drip|infusion|nomogram|units?/(?:kg/)?h(?:r|our)?)\b)"
        r"|\bhep(?:arin)? (?:gtt|drip|infusion)\b|\bwarfarin\b|\bcoumadin\b|\bapixaban\b|\beliquis\b"
        r"|\brivaroxaban\b|\bxarelto\b|\bdabigatran\b|\bpradaxa\b|\bedoxaban\b|\bargatroban\b"
        r"|\bbivalirudin\b|\benoxaparin\s+\d+\s*mg\b(?=[^.\n]{0,20}\b(?:BID|q12h?|1\s*mg/kg)\b)"
        r"|\b(?:therapeutic|full[- ]dose) (?:anticoagulation|AC|lovenox|enoxaparin|heparin)\b"
        r"|\banticoagulation\b|\bAC/AP\b|\bAP/AC\b|\bLMWH\b|\baspirin\b|\bASA\s*(?:81|162|325)\b|\bclopidogrel\b|\bplavix\b|\bticagrelor\b"
        r"|\bbrilinta\b|\bprasugrel\b|\bcangrelor\b|\bDAPT\b|\btPA\b|\balteplase\b|\btenecteplase\b",
        re.IGNORECASE),
}
# items whose absence is itself shown ("not stated")
_REQUIRED = ("Allergies", "Code status")
# items shown as the matched value rather than the whole line (vitals lines run long)
_VALUE_ONLY = ("Weight", "Height")
# items that list several lines from the latest note (each drug can matter)
_MULTI = {"Antithrombotics": 4}

_HEADING_ONLY = re.compile(r"^\s*(?:#{1,6}\s*)?(?P<h>allerg(?:y|ies)(?:/adverse reactions)?|ALLG)\s*:?\s*$",
                           re.IGNORECASE)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?[A-Za-z][A-Za-z /&]{1,40}:\s*$")
_BULLET = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+")
# history-only mentions don't state the current fact ("allergic rhinitis", "PMH: ... DNR discussed")
_NOT_A_STATEMENT = re.compile(r"\ballergic (?:rhinitis|conjunctivitis|asthma)\b|\ballergist\b",
                              re.IGNORECASE)

_CODE_VALUES = (
    ("comfort care", re.compile(r"\bcomfort (?:care|measures)\b|\bCMO\b", re.IGNORECASE)),
    ("DNR/DNI", re.compile(r"\bDNR\s*(?:/|and|&|,)\s*DNI\b|\bDNR\b(?=[^.\n]{0,20}\bDNI\b)", re.IGNORECASE)),
    ("DNI", re.compile(r"(?<!/)\bDNI\b|\bno intubation\b", re.IGNORECASE)),
    ("DNR", re.compile(r"\bDNA?R\b|\bno (?:CPR|code)\b", re.IGNORECASE)),
    ("full code", re.compile(r"\bfull code\b", re.IGNORECASE)),
)


@dataclass
class SafetyItem:
    name: str
    status: str
    line: str = ""                                   # the latest statement, verbatim
    value: str = ""                                  # the matched value (weight, code status…)
    earlier: list[str] = field(default_factory=list)  # earlier, different statements
    extra: list[str] = field(default_factory=list)   # more latest-note lines (antithrombotics)
    removed: list[str] = field(default_factory=list)  # lines about it a cleaning rule removed

    def to_text(self) -> str:
        if self.status == NOT_STATED:
            return f"{self.name}: not stated in the chart"
        if self.status == REMOVED:
            return f"{self.name}: removed by a cleaning rule — check the original chart"
        shown = self.value if self.name in _VALUE_ONLY and self.value else self.line
        label = re.match(r"\s*([A-Za-z][A-Za-z /]{1,25}):", shown)
        out = shown if label and ITEMS[self.name].search(label.group(1)) else f"{self.name}: {shown}"
        if self.extra:
            out += "".join(f"\n  {line}" for line in self.extra)
        if self.status == CHANGED:
            out += "".join(f"\n  (earlier: {line})" for line in self.earlier)
        if self.removed:
            out += "\n  (a cleaning rule also removed a line about it — check the original chart)"
        return out

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "line": self.line, "value": self.value,
                "earlier": list(self.earlier), "extra": list(self.extra),
                "removed": list(self.removed), "text": self.to_text()}


@dataclass
class SafetyReport:
    items: list[SafetyItem] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return all(i.status == NOT_STATED for i in self.items)

    @property
    def flagged(self) -> list[SafetyItem]:
        """Items worth a look: changed, removed by cleaning, or not stated."""
        return [i for i in self.items if i.status != FOUND or i.removed]

    @property
    def changed(self) -> list[SafetyItem]:
        return [i for i in self.items if i.status == CHANGED]

    def get(self, name: str) -> SafetyItem | None:
        return next((i for i in self.items if i.name == name), None)

    def to_text(self) -> str:
        if self.empty:
            return ""
        out = ["Safety header"]
        out.extend(i.to_text() for i in self.items)
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"items": [i.to_dict() for i in self.items],
                "changed": [i.name for i in self.changed], "text": self.to_text()}


def _statements(lines: list[str]) -> list[str]:
    """Lines with an "Allergies" heading folded into the lines under it."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _HEADING_ONLY.match(line)
        if m:
            body: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _HEADING.match(lines[j]) and len(body) < 6:
                body.append(_BULLET.sub("", lines[j].strip()))
                j += 1
            if body:
                head = m.group("h").strip()
                out.append(f"{head if head.isupper() else head.title()}: " + "; ".join(body))
                i = j
                continue
        out.append(line)
        i += 1
    return out


_FIELD = re.compile(r"^\s*(?:[-•*]\s*)?[A-Z][A-Za-z /&()]{1,30}:\s*\S")


def lines_of(text: str) -> list[str]:
    """Logical lines (wrapped lines rejoined), keeping "Label: value" fields apart."""
    from .problems import continues
    out: list[str] = []
    for line in text.split("\n"):
        if out and not _FIELD.match(line) and not _FIELD.match(out[-1]) and continues(out[-1], line):
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return out


def _clean(line: str) -> str:
    return _BULLET.sub("", line.strip())[:240]


def _key(name: str, line: str, value: str) -> str:
    """What two statements are compared on: the code-status value, the matched
    weight/height, or the wording of the line (dates, times and spacing ignored)."""
    if name == "Code status":
        return _code_value(line) or _norm(line)
    if name in _VALUE_ONLY:
        return _norm(value)
    return _norm(re.sub(r"^(?:allerg(?:y|ies)[^:]{0,25}):", "", line, flags=re.IGNORECASE))


def _norm(text: str) -> str:
    text = re.sub(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{1,2}:\d{2}\b|\b\d{4}\b(?=\s*(?:h|hrs)?\b)", "", text)
    return " ".join(re.sub(r"[^\w/%.]+", " ", text.casefold()).split())


def _code_value(line: str) -> str:
    for value, rx in _CODE_VALUES:
        if rx.search(line):
            return value
    return ""


def removed_lines(fact_report) -> list[str]:
    """Lines a removal rule or section setting took out, from a
    :class:`~chartcleaner.fact_check.FactReport` (None → [])."""
    if fact_report is None:
        return []
    return list(dict.fromkeys(ln for loss in fact_report.losses for ln in loss.lines))


def build(text: str, removed: list[str] | None = None) -> SafetyReport:
    """The safety header for ``text`` (the latest statement of each item wins).

    ``removed`` — lines cleaning removed (see :func:`removed_lines`): an item the
    cleaned text no longer states but one of them did reads "removed by cleaning".
    """
    from .bundle import latest_note

    if not text.strip():
        return SafetyReport([])
    lines = [ln for ln in _statements(lines_of(text)) if ln.strip()]
    latest_lines = [ln for ln in _statements(lines_of(latest_note(text))) if ln.strip()]
    items: list[SafetyItem] = []
    removed = [ln for ln in (removed or []) if ln.strip()]
    for name, rx in ITEMS.items():
        gone = [_clean(ln) for ln in removed if rx.search(ln) and not _NOT_A_STATEMENT.search(ln)]
        hits = [(ln, m) for ln in lines
                if not _HEADING.match(ln) and not _NOT_A_STATEMENT.search(ln) and (m := rx.search(ln))]
        if not hits:
            if gone:
                items.append(SafetyItem(name, REMOVED, removed=gone[:3]))
            elif name in _REQUIRED:
                items.append(SafetyItem(name, NOT_STATED))
            continue
        line, m = hits[-1]
        value = m.group(0).strip() if name in _VALUE_ONLY else (
            _code_value(line) if name == "Code status" else "")
        latest_key = _key(name, line, value)
        earlier: list[str] = []
        seen = {latest_key}
        for ln, mm in hits[:-1]:
            v = mm.group(0).strip() if name in _VALUE_ONLY else ""
            k = _key(name, ln, v)
            if k and k not in seen:
                seen.add(k)
                earlier.append(_clean(v if name in _VALUE_ONLY else ln))
        if name in ("Weight", "Height", "Antithrombotics"):
            earlier = []  # these change on purpose; only the latest is shown
        extra: list[str] = []
        if name in _MULTI:
            mine = [_clean(ln) for ln in latest_lines if rx.search(ln) and not _HEADING.match(ln)]
            mine = list(dict.fromkeys(mine))
            if mine:
                line = mine[0]
                extra = mine[1:_MULTI[name]]
        items.append(SafetyItem(name, CHANGED if earlier else FOUND, _clean(line), value,
                                earlier[-2:], extra, gone[:3]))
    return SafetyReport(items)
