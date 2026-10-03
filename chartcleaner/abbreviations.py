"""Shorten full medical terms into source abbreviations.

Exact expanded-term duplicates use the first CSV row; later rows never replace
that canonical abbreviation. Matching is a single longest-first pass.
"""

from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE_PATH = Path(__file__).with_name("medical_abbreviations.csv")

# Cap on recorded changes per stage (shared with stages.py), so tracking stays
# cheap on huge charts.
MAX_TRACKED_CHANGES = 2000


_UNCERTAINTY = {"likely", "possibly", "probable", "uncertain"}


def _split_top_level_slash(expanded: str) -> list[str]:
    """Split only whitespace-delimited slashes outside parentheses."""
    parts: list[str] = []
    start = depth = 0
    i = 0
    while i < len(expanded):
        if expanded[i] == "(":
            depth += 1
        elif expanded[i] == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and expanded.startswith(" / ", i):
            parts.append(expanded[start:i].strip())
            start = i + 3
            i += 2
        i += 1
    if parts:
        parts.append(expanded[start:].strip())
    return [part for part in parts if part]


def _candidates(expanded: str, abbreviation: str) -> list[tuple[str, str]]:
    """Return conservative aliases for one source row."""
    result: list[tuple[str, str]] = []
    parenthetical = re.fullmatch(r"(.+?)\s+\(([^()]*)\)$", expanded)
    result.append((expanded, abbreviation))
    if parenthetical:
        base, qualifier = parenthetical.groups()
        if qualifier.casefold() not in _UNCERTAINTY:
            result.append((base, abbreviation))

    if "(s)" in expanded:
        singular = expanded.replace("(s)", "").replace("  ", " ").strip()
        plural = expanded.replace("(s)", "s")
        result.extend(((singular, abbreviation), (plural, abbreviation)))

    # Split only top-level explanatory alternatives whose abbreviation is a single token.
    if "/" not in abbreviation:
        result.extend((part, abbreviation) for part in _split_top_level_slash(expanded))
    return result


def _settings(cfg: dict | None) -> tuple[set[str], list[dict[str, Any]]]:
    group = cfg.get("abbreviations", {}) if isinstance(cfg, dict) else {}
    if not isinstance(group, dict):
        return set(), []
    disabled = {str(x).strip().casefold() for x in group.get("disabled", []) if str(x).strip()}
    custom = []
    for item in group.get("custom", []):
        if not isinstance(item, dict) or not isinstance(item.get("enabled", True), bool):
            continue
        term = item.get("term")
        replacement = item.get("replacement")
        if isinstance(term, str) and term.strip() and isinstance(replacement, str) and replacement:
            custom.append({"term": term.strip(), "replacement": replacement, "enabled": item.get("enabled", True)})
    return disabled, custom


def normalize_settings(group: dict | None) -> dict:
    """Normalize the portable abbreviation settings contract."""
    group = group if isinstance(group, dict) else {}
    disabled = []
    seen = set()
    for value in group.get("disabled", []):
        if isinstance(value, str) and value.strip() and value.casefold() not in seen:
            disabled.append(value.strip())
            seen.add(value.casefold())
    custom = []
    seen = set()
    for item in group.get("custom", []):
        if not isinstance(item, dict):
            continue
        term = item.get("term")
        replacement = item.get("replacement")
        enabled = item.get("enabled", True)
        if (isinstance(term, str) and term.strip() and isinstance(replacement, str)
                and replacement and isinstance(enabled, bool)
                and term.casefold() not in seen):
            custom.append({"term": term.strip(), "replacement": replacement, "enabled": enabled})
            seen.add(term.casefold())
    return {"disabled": disabled, "custom": custom}


@lru_cache(maxsize=32)
def _matcher(settings: str = "") -> tuple[re.Pattern[str], dict[str, str | None], int, dict[str, str]]:
    options = json.loads(settings) if settings else {"disabled": [], "custom": []}
    disabled = set(options.get("disabled", []))
    custom = options.get("custom", [])
    with SOURCE_PATH.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))

    # Exact source cells win over any aliases derived below, including aliases
    # created by an earlier row.
    by_term: dict[str, str | None] = {}
    aliases_by_source: dict[str, set[str]] = {}
    for row in rows:
        expanded = row["Expanded version"].strip()
        abbreviation = row["Abbreviation"].strip()
        if not expanded or "not expanded in source" in expanded.casefold():
            continue
        parenthetical = re.fullmatch(r"(.+?)\s+\(([^()]*)\)$", expanded)
        replacement = abbreviation
        if parenthetical and parenthetical.group(2).casefold() in _UNCERTAINTY:
            replacement += " (" + parenthetical.group(2) + ")"
        by_term.setdefault(expanded.casefold(), replacement)
        aliases_by_source.setdefault(expanded.casefold(), set()).add(expanded.casefold())

    for row in rows:
        expanded = row["Expanded version"].strip()
        abbreviation = row["Abbreviation"].strip()
        if not expanded or "not expanded in source" in expanded.casefold():
            continue
        for alias, replacement in _candidates(expanded, abbreviation):
            key = alias.casefold()
            if key and key not in by_term:
                by_term[key] = replacement
            aliases_by_source.setdefault(expanded.casefold(), set()).add(key)

    for source in disabled:
        for alias in aliases_by_source.get(source, {source}):
            # Match disabled phrases as a unit so shorter terms inside them
            # cannot still be abbreviated (e.g. "artery" inside a disabled ACA).
            by_term[alias] = None

    custom_terms: dict[str, str] = {}
    for item in custom:
        key = item["term"].casefold()
        for alias in aliases_by_source.get(key, {key}):
            by_term[alias] = item["replacement"] if item.get("enabled", True) else None
            if item.get("enabled", True):
                custom_terms[alias] = item["term"] if alias == key else alias

    parts = []
    custom_groups = {}
    for term in sorted(by_term, key=len, reverse=True):
        if term in custom_terms:
            name = f"custom_{len(custom_groups)}"
            custom_groups[name] = by_term[term]
            parts.append(f"(?P<{name}>(?i:{re.escape(custom_terms[term])}))")
        else:
            parts.append(f"(?ai:{re.escape(term)})")
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)") if parts else re.compile(r"(?!x)x")
    return pattern, by_term, len(rows), custom_groups


SOURCE_ROW_COUNT = _matcher()[2]


def abbreviate(text: str, cfg: dict | None = None,
               changes: list[dict] | None = None) -> tuple[str, int, dict]:
    """Replace whole expanded medical terms in one non-cascading pass.

    When ``changes`` is a list, each replacement is recorded in it (and
    returned as ``details["changes"]``) for the app's inspect views.
    """
    disabled, custom = _settings(cfg)
    settings = json.dumps({"disabled": sorted(disabled), "custom": custom}, sort_keys=True, ensure_ascii=False)
    pattern, replacements, row_count, custom_groups = _matcher(settings)
    counts: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        replacement = (custom_groups[match.lastgroup] if match.lastgroup
                       else replacements[match.group(0).casefold()])
        if replacement is None:
            return match.group(0)
        counts[replacement] = counts.get(replacement, 0) + 1
        if changes is not None and len(changes) < MAX_TRACKED_CHANGES:
            changes.append({
                "rule": match.group(0).casefold(),
                "source": "custom" if match.lastgroup else "bundled",
                "before": match.group(0),
                "after": replacement,
                "line": text.count("\n", 0, match.start()) + 1,
            })
        return replacement

    result = pattern.sub(replace, text)
    details = {"source_rows": row_count, "replacements": counts}
    if changes is not None:
        details["changes"] = changes
    return result, sum(counts.values()), details


__all__ = ["SOURCE_PATH", "SOURCE_ROW_COUNT", "abbreviate", "normalize_settings"]
