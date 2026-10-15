"""Census: one card per bed, for pre-rounds.

Every chart cleaned with a **bed tag** is kept (encrypted) in
:mod:`chartcleaner.recent_charts`. The census takes the newest chart for each
tag, cleans it with the saved rules and reads the same insights the Clean page
shows, cut down to what a pre-rounds glance needs:

* allergies and code status (the safety header; "changed" / "removed by
  cleaning" / "not stated" kept);
* possible contradictions (:mod:`chartcleaner.conflicts`);
* the to-do list, devices past their review day, active antibiotics with
  their day, ICU bundle items the latest note never mentions, stale
  copied-forward lines.

Every value is a chart line or a count of chart lines — nothing is inferred.
One bed failing never hides the others (its card carries the error).
"""

from __future__ import annotations

import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable

__all__ = ["BedCard", "CensusReport", "build", "build_card", "changed_since"]

MAX_TODO = 6
MAX_DELTA = 4


@dataclass
class BedCard:
    tag: str
    ts: str = ""                                             # when the chart was cleaned (ISO)
    allergies: str = ""
    code_status: str = ""
    safety_flags: list[str] = field(default_factory=list)    # "Code status: changed" …
    conflicts: list[str] = field(default_factory=list)       # why each pair was flagged
    todo: list[str] = field(default_factory=list)
    todo_total: int = 0
    devices_review: list[str] = field(default_factory=list)
    antibiotics: list[str] = field(default_factory=list)
    bundle_gaps: list[str] = field(default_factory=list)
    stale: int = 0
    delta: list[str] = field(default_factory=list)     # newest units absent from the previous chart
    delta_total: int = 0
    delta_since: str = ""                              # date/ts of the previous chart compared
    error: str = ""

    @property
    def attention(self) -> int:
        """How many things on this card want a look (sorts the census)."""
        return (2 * len(self.conflicts) + 2 * len(self.safety_flags) + len(self.devices_review)
                + len(self.bundle_gaps) + (1 if self.stale else 0) + (5 if self.error else 0))

    @property
    def updated(self) -> str:
        try:
            return datetime.fromisoformat(self.ts).strftime("%m/%d %H:%M") if self.ts else ""
        except ValueError:
            return ""

    def to_text(self) -> str:
        if self.error:
            return f"{self.tag} — could not read: {self.error}"
        out = [f"{self.tag}" + (f"  (cleaned {self.updated})" if self.updated else "")]
        out.append(f"  Allergies: {self.allergies or 'not stated'} · Code: {self.code_status or 'not stated'}")
        for flag in self.safety_flags:
            out.append(f"  ! {flag}")
        for why in self.conflicts:
            out.append(f"  ! contradiction: {why}")
        if self.antibiotics:
            out.append("  Abx: " + "; ".join(self.antibiotics))
        if self.devices_review:
            out.append("  Review device: " + "; ".join(self.devices_review))
        if self.bundle_gaps:
            out.append("  Not mentioned: " + ", ".join(self.bundle_gaps))
        if self.stale:
            out.append(f"  {self.stale} possibly stale line{'s' if self.stale != 1 else ''}")
        if self.delta_total:
            since = f" since {self.delta_since}" if self.delta_since else ""
            out.append(f"  ~ {self.delta_total} new/changed line{'s' if self.delta_total != 1 else ''}{since}")
            out.extend(f"    + {line}" for line in self.delta)
            if self.delta_total > len(self.delta):
                out.append(f"    … {self.delta_total - len(self.delta)} more")
        for item in self.todo:
            out.append(f"  - [ ] {item}")
        if self.todo_total > len(self.todo):
            out.append(f"  … {self.todo_total - len(self.todo)} more to-do")
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"tag": self.tag, "ts": self.ts, "allergies": self.allergies,
                "code_status": self.code_status, "safety_flags": list(self.safety_flags),
                "conflicts": list(self.conflicts), "todo": list(self.todo), "todo_total": self.todo_total,
                "devices_review": list(self.devices_review), "antibiotics": list(self.antibiotics),
                "bundle_gaps": list(self.bundle_gaps), "stale": self.stale,
                "delta": list(self.delta), "delta_total": self.delta_total,
                "delta_since": self.delta_since, "error": self.error,
                "attention": self.attention}


@dataclass
class CensusReport:
    beds: list[BedCard] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.beds

    def to_text(self) -> str:
        if not self.beds:
            return ""
        return "\n\n".join(b.to_text() for b in self.beds)

    def to_dict(self) -> dict:
        return {"beds": [b.to_dict() for b in self.beds], "text": self.to_text()}


def _clean(text: str, cfg: dict) -> tuple[str, list[str]]:
    """Cleaned text and the lines cleaning removed (for "removed by cleaning")."""
    from . import service, store
    from .engine import Pipeline
    from .safety import removed_lines

    result = Pipeline(cfg, custom_dir=store.CUSTOM_RULES_DIR).run(text, wrap=False,
                                                                  cache=service.result_cache())
    return result.text, removed_lines(result.fact_check)


def changed_since(newest: str, previous: str | None) -> list[str]:
    """Units (lines/sentences) in ``newest`` absent from ``previous`` — newest first.

    Same sentence semantics as the copy-forward view
    (:mod:`chartcleaner.delta_engine`): only a unit that literally repeats an
    earlier one counts as old, so nothing new is ever dropped. No previous
    chart (or identical texts) means no delta, never an error.
    """
    if not previous or not newest.strip() or newest == previous:
        return []
    from .delta_engine import _norm, _units

    seen = {_norm(u) for p in previous.split("\n\n") for u in _units(p)}
    if not seen:
        return []
    fresh: list[str] = []
    for para in newest.split("\n\n"):
        for unit in _units(para):
            if len(unit) >= 4 and _norm(unit) not in seen:
                seen.add(_norm(unit))
                fresh.append(unit[:240])
    return fresh


def build_card(tag: str, text: str, cfg: dict, *, ts: str = "",
               clean: Callable[[str, dict], tuple[str, list[str]]] | None = None,
               previous: str | None = None, previous_ts: str = "") -> BedCard:
    """The card for one bed: clean ``text`` and read its insights."""
    from . import bundle, conflicts, devices, micro, pending, safety, stale

    card = BedCard(tag, ts)
    try:
        cleaned, removed = (clean or _clean)(text, cfg)
    except Exception as exc:  # one bed failing never hides the others
        card.error = f"{type(exc).__name__}: {exc}"[:200]
        return card

    def attempt(fn):
        try:
            return fn()
        except Exception:
            return None

    report = attempt(lambda: safety.build(cleaned, removed))
    if report is not None:
        for name, attr in (("Allergies", "allergies"), ("Code status", "code_status")):
            item = report.get(name)
            if item is None:
                continue
            if item.status == safety.NOT_STATED:
                continue
            if item.status == safety.REMOVED:
                card.safety_flags.append(f"{name}: removed by a cleaning rule")
                continue
            shown = item.value if name == "Code status" and item.value else item.line
            setattr(card, attr, re.sub(r"^(?:allerg(?:y|ies)|ALLG)\s*:\s*", "", shown, flags=re.IGNORECASE))
            if item.status == safety.CHANGED:
                card.safety_flags.append(f"{name}: changed (earlier: {item.earlier[-1]})"
                                         if item.earlier else f"{name}: changed")
    found = attempt(lambda: conflicts.build(cleaned))
    if found is not None:
        card.conflicts = [f"{c.kind} — {c.why}" for c in found.conflicts]
    todo = attempt(lambda: pending.build(cleaned))
    if todo is not None:
        card.todo = [i.to_text() for i in todo.items[:MAX_TODO]]
        card.todo_total = len(todo.items)
    devs = attempt(lambda: devices.build(cleaned))
    if devs is not None:
        card.devices_review = [d.to_text() for d in devs.devices if d.needs_review]
    abx = attempt(lambda: micro.build(cleaned))
    if abx is not None:
        card.antibiotics = [a.to_text() for a in abx.antibiotics if not a.stopped]
    care = attempt(lambda: bundle.build(cleaned))
    if care is not None:
        card.bundle_gaps = [i.name for i in care.items if i.status == bundle.MISSING]
    old = attempt(lambda: stale.build(cleaned))
    if old is not None:
        card.stale = len(old.lines)
    try:
        fresh = changed_since(text, previous)
    except Exception:
        fresh = []  # the delta must never break a card
    if fresh:
        card.delta = fresh[:MAX_DELTA]
        card.delta_total = len(fresh)
        card.delta_since = previous_ts
    return card


def build(cfg: dict | None = None, *, records: list[dict] | None = None,
          clean: Callable[[str, dict], tuple[str, list[str]]] | None = None,
          on_progress: Callable[[int, int], None] | None = None) -> CensusReport:
    """One card per bed tag (the newest chart for each), beds needing a look first.

    ``records`` — ``recent_charts.load()``-shaped dicts (default: the stored charts).
    """
    from . import recent_charts, service

    if records is None:
        records = recent_charts.load()
    newest: dict[str, dict] = {}
    for rec in records:  # newest first
        tag = recent_charts.clean_tag(rec.get("tag"))
        if tag and tag.casefold() not in newest:
            newest[tag.casefold()] = {**rec, "tag": tag}
    if not newest:
        return CensusReport()
    cfg = cfg if cfg is not None else service.load_active_config()
    cards: list[BedCard] = []
    previous: dict[str, dict] = {}
    for rec in records:  # newest first: the second chart per tag is "yesterday"
        tag = recent_charts.clean_tag(rec.get("tag"))
        if tag and tag.casefold() not in previous and rec.get("text") != newest[tag.casefold()].get("text"):
            previous[tag.casefold()] = rec
    for i, rec in enumerate(newest.values()):
        key = rec["tag"].casefold()
        prev = previous.get(key, {})
        try:
            text = rec.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("stored chart is damaged or unreadable")
            cards.append(build_card(rec["tag"], text, cfg, ts=str(rec.get("ts") or ""), clean=clean,
                                    previous=prev.get("text") if isinstance(prev.get("text"), str)
                                    else None,
                                    previous_ts=str(prev.get("ts") or "")))
        except Exception as exc:  # a corrupt record never hides the other beds
            card = BedCard(rec.get("tag") or "?", str(rec.get("ts") or ""))
            card.error = f"{type(exc).__name__}: {exc}"[:200]
            cards.append(card)
        if on_progress:
            on_progress(i + 1, len(newest))
    cards.sort(key=lambda c: (-c.attention, c.tag.casefold()))
    return CensusReport(cards)
