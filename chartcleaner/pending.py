"""Pending / to-do list: what the latest note says is still outstanding.

Pulls lines out of the most recent note and sorts each into one group:

* **Pending results** — "pending", "sent", "in process", "awaiting",
  "prelim(inary)", "not yet resulted";
* **Consults** — consults called, placed, pending or still to see the patient,
  and recommendations awaited;
* **If / then** — conditional orders ("if Na < 130 start salt tabs",
  "call if SBP > 160", "notify for…");
* **Follow up** — f/u, follow up, recheck, repeat, trend;
* **Planned** — "tomorrow", "in AM", "scheduled", "will obtain/order/start…";
* **Consider** — "consider", "may need", "rule out".

Every item is a chart line copied verbatim (wrapped lines rejoined), so the
list is as grounded as the chart. A line lands in its first matching group;
copy-forward duplicates collapse to one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["GROUPS", "TodoItem", "PendingReport", "build"]

# group -> pattern (checked in this order; a line goes into the first group it matches)
GROUPS: dict[str, re.Pattern] = {
    "Pending results": re.compile(
        r"\b(?:pending|pnd|in process|in progress|awaiting|await(?:ing)? (?:results?|read|final)"
        r"|not yet (?:resulted|read|back|final)|to be resulted|prelim(?:inary)?(?! diagnosis)"
        r"|(?:cx|cultures?|labs?|studies|swab|panel|level|smear|cytology|pathology|path)\s+(?:were |was )?sent"
        r"|sent (?:for|to (?:lab|path)))\b", re.IGNORECASE),
    "Consults": re.compile(
        r"\bconsult(?:ed|s)?\b(?=[^.\n]{0,60}\b(?:pending|placed|called|paged|requested|to see|recs|"
        r"recommendations|following|will see|in AM|today|tomorrow)\b)"
        r"|\b(?:will|to|please) consult\b|\b(?:await|appreciate|f/u|follow up|pending)\s+\w*\s*(?:recs|recommendations)\b",
        re.IGNORECASE),
    "If / then": re.compile(
        r"\bif\b[^.\n]{2,80}\b(?:then|start|give|call|notify|page|consider|increase|decrease|hold|"
        r"resume|restart|repeat|obtain|recheck|transfuse|bolus|titrate|switch|escalate|will)\b"
        r"|\b(?:call|notify|page)\s+(?:md|team|provider|neurosurgery|nsgy|us|me|resident|fellow|"
        r"if|for|with)\b|\bprn\s+for\b", re.IGNORECASE),
    "Follow up": re.compile(
        r"\bf/u\b|\bfollow[- ]?up\b|\bfollow(?:ing)?\s+(?!(?:simple |complex |\w+ )?(?:commands?|directions?|instructions?)\b)"
        r"[a-z]\w*|\bre-?check\b"
        r"|\brepeat\s+\w+|\btrend(?:ing)?\b(?!s?:)|\bserial\s+\w+|\bq\s?\d{1,2}\s?h(?:r|rs)?\s+(?:checks?|labs?|na|"
        r"sodium|neuro checks?|bmp|cbc)\b", re.IGNORECASE),
    "Planned": re.compile(
        r"\b(?:tomorrow|in (?:the )?am|this (?:afternoon|evening)|tonight|scheduled|planned for|"
        r"plan(?:ning)? (?:for|to)|will (?:obtain|order|get|start|begin|send|need|discuss|place|remove|"
        r"pull|wean|extubate|transition|transfer|re-?image|repeat|check|stop|d/c|discontinue|resume|"
        r"restart|hold|consider|arrange|schedule)|to be (?:done|placed|removed|scheduled)|pre-?op|"
        r"\bOR\b (?:today|tomorrow|on))\b", re.IGNORECASE),
    "Consider": re.compile(
        r"\b(?:consider(?:ing)?|may need|might need|could consider|question of|r/o|rule out|"
        r"low threshold (?:for|to)|discuss(?:ion)? (?:with|re))\b", re.IGNORECASE),
}

# a line that only says something is NOT pending (or a results heading) isn't a to-do
_NOT_TODO = re.compile(r"\b(?:no (?:longer )?pending|nothing pending|none pending|no f/u needed|"
                       r"no follow[- ]?up needed|resulted|finalized)\b", re.IGNORECASE)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?[A-Za-z][A-Za-z /&]{1,40}:\s*$")
_WHEN = re.compile(r"\b(?:tomorrow|in (?:the )?am|this (?:afternoon|evening)|tonight|today|"
                   r"(?:at|by)\s+\d{3,4}\b|(?:at|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|"
                   r"q\s?\d{1,2}\s?h\w*|in \d+\s*(?:h|hr|hrs|hours|days?)\b|on \d{1,2}/\d{1,2})",
                   re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+")


@dataclass
class TodoItem:
    group: str
    text: str
    when: str = ""      # a time hint the line states ("in AM", "q6h", "tomorrow")

    def to_text(self) -> str:
        return self.text + (f"  [{self.when}]" if self.when else "")

    def to_dict(self) -> dict:
        return {"group": self.group, "text": self.text, "when": self.when}


@dataclass
class PendingReport:
    items: list[TodoItem] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.items

    def grouped(self) -> dict[str, list[TodoItem]]:
        out: dict[str, list[TodoItem]] = {}
        for item in self.items:
            out.setdefault(item.group, []).append(item)
        return out

    def to_text(self) -> str:
        if not self.items:
            return ""
        out = ["To do / pending (latest note)"]
        for group, items in self.grouped().items():
            out.append(f"{group}:")
            out.extend(f"- [ ] {i.to_text()}" for i in items)
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"items": [i.to_dict() for i in self.items],
                "groups": {g: [i.text for i in items] for g, items in self.grouped().items()},
                "text": self.to_text()}


def build(text: str) -> PendingReport:
    """The to-do list read from the latest note of ``text``."""
    from .bundle import latest_note
    from .problems import logical_lines

    found: dict[str, list[TodoItem]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    for line in logical_lines(latest_note(text)):
        stripped = line.strip()
        if len(stripped) < 4 or _HEADING.match(line) or _NOT_TODO.search(stripped):
            continue
        body = _BULLET.sub("", stripped)[:240]
        key = re.sub(r"\s+", " ", body.lower())
        if key in seen:
            continue
        for group, rx in GROUPS.items():
            if rx.search(body):
                seen.add(key)
                when = _WHEN.search(body)
                found[group].append(TodoItem(group, body, when.group(0) if when else ""))
                break
    return PendingReport([item for items in found.values() for item in items])
