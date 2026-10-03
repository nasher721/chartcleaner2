"""Safety checks for abbreviation rules (Do Not Use / error-prone lists).

``check`` looks at one proposed term → abbreviation and returns issues:

* ``do_not_use`` — the abbreviation is on The Joint Commission Do Not Use
  list or an ISMP error-prone entry *in the risky meaning* (each entry has a
  ``meaning`` regex matched against the full term, so IJ for "internal
  jugular" passes while IJ for "injection" does not). ``level="block"`` entries need an
  explicit override (the custom entry is saved with ``acknowledged: true``);
  ``level="warn"`` entries only warn.
* ``shared`` — the same abbreviation already stands for other terms in the
  dictionary, so readers may not know which one is meant (warn).

``report`` scans every active rule (bundled + custom). ``is_blocked`` is used
by the abbreviation matcher so bundled Do Not Use rows are not applied unless
the user explicitly allowed them.

The lists live in ``abbreviation_do_not_use.json`` beside this module.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

LIST_PATH = Path(__file__).with_name("abbreviation_do_not_use.json")
CSV_PATH = Path(__file__).with_name("medical_abbreviations.csv")

__all__ = ["Issue", "check", "report", "is_blocked", "needs_override", "LIST_PATH"]


@dataclass(frozen=True)
class Issue:
    code: str           # "do_not_use" | "shared"
    level: str          # "block" | "warn"
    message: str
    source: str = ""
    use_instead: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _key(text: str) -> str:
    return re.sub(r"[.\s]", "", text).casefold()


@lru_cache(maxsize=1)
def _lists() -> tuple[dict[str, list[tuple[re.Pattern | None, dict]]], list[tuple[re.Pattern, dict]]]:
    data = json.loads(LIST_PATH.read_text(encoding="utf-8"))
    exact: dict[str, list[tuple[re.Pattern | None, dict]]] = {}
    for entry in data.get("entries", []):
        meaning = re.compile(entry["meaning"], re.IGNORECASE) if entry.get("meaning") else None
        for abbr in entry["abbr"]:
            exact.setdefault(_key(abbr), []).append((meaning, entry))
    patterns = [(re.compile(p["regex"]), p) for p in data.get("patterns", [])]
    return exact, patterns


def _list_name(entry: dict) -> str:
    if entry["source"] == "The Joint Commission":
        return "The Joint Commission's Do Not Use list"
    return f"{entry['source']}'s list of error-prone abbreviations"


def _do_not_use(abbreviation: str, term: str) -> list[Issue]:
    exact, patterns = _lists()
    issues = []
    # Check the whole abbreviation, then each part of a compound like "U/hr".
    parts = [abbreviation] + [p for p in re.split(r"[/\s,;+-]+", abbreviation) if p]
    entry = next((e for part in parts for meaning, e in exact.get(_key(part), [])
                  if meaning is None or meaning.search(term)), None)
    if entry:
        issues.append(Issue("do_not_use", entry["level"],
                            f"“{abbreviation}” for “{term.strip()}” is on {_list_name(entry)}: "
                            f"{entry['problem']}.",
                            entry["source"], entry.get("use", "")))
    for regex, entry in patterns:
        if regex.search(abbreviation):
            issues.append(Issue("do_not_use", entry["level"], f"{entry['problem']}.",
                                entry["source"], entry.get("use", "")))
    return issues


def is_blocked(abbreviation: str, term: str) -> bool:
    """True when ``term`` → ``abbreviation`` is a ``block``-level Do Not Use use."""
    return any(i.level == "block" for i in _do_not_use(abbreviation, term))


@lru_cache(maxsize=1)
def _bundled_pairs() -> tuple[tuple[str, str], ...]:
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as source:
        rows = []
        for row in csv.DictReader(source):
            term = (row.get("Expanded version") or "").strip()
            abbr = (row.get("Abbreviation") or "").strip()
            if term and abbr and "not expanded in source" not in term.casefold():
                rows.append((term, abbr))
    return tuple(rows)


def _active_pairs(cfg: dict | None) -> list[tuple[str, str, str]]:
    """(term, abbreviation, kind) for every rule currently in effect."""
    from .abbreviations import normalize_settings
    group = normalize_settings((cfg or {}).get("abbreviations") if isinstance(cfg, dict) else {})
    disabled = {t.casefold() for t in group["disabled"]}
    custom = {c["term"].casefold(): c for c in group["custom"]}
    pairs = []
    for term, abbr in _bundled_pairs():
        if term.casefold() in disabled or term.casefold() in custom:
            continue
        pairs.append((term, abbr, "bundled"))
    for item in group["custom"]:
        if item.get("enabled", True):
            pairs.append((item["term"], item["replacement"], "custom"))
    return pairs


def check(term: str, abbreviation: str, cfg: dict | None = None) -> list[Issue]:
    """Issues for saving ``term`` → ``abbreviation`` (block issues first)."""
    issues = _do_not_use(abbreviation, term)
    others = sorted({t for t, a, _k in _active_pairs(cfg)
                     if a == abbreviation and t.casefold() != term.strip().casefold()},
                    key=str.casefold)
    if others:
        shown = ", ".join(others[:4]) + (f" and {len(others) - 4} more" if len(others) > 4 else "")
        issues.append(Issue("shared", "warn",
                            f"“{abbreviation}” already stands for: {shown}."))
    return sorted(issues, key=lambda i: i.level != "block")


def needs_override(issues: list[Issue]) -> bool:
    return any(i.level == "block" for i in issues)


def report(cfg: dict | None = None) -> list[dict]:
    """Every Do Not Use hit among the rules in effect (and blocked bundled rows)."""
    from .abbreviations import normalize_settings
    group = normalize_settings((cfg or {}).get("abbreviations") if isinstance(cfg, dict) else {})
    acknowledged = {c["term"].casefold() for c in group["custom"] if c.get("acknowledged")}
    out = []
    for term, abbr, kind in _active_pairs(cfg):
        for issue in _do_not_use(abbr, term):
            blocked = issue.level == "block" and term.casefold() not in acknowledged
            out.append({"term": term, "abbreviation": abbr, "kind": kind,
                        "status": "blocked" if blocked else ("allowed" if issue.level == "block" else "warning"),
                        **issue.to_dict()})
    return out
