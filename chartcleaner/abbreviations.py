"""Shorten full medical terms into source abbreviations.

Exact expanded-term duplicates use the first CSV row; later rows never replace
that canonical abbreviation. Matching is a single longest-first pass.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

SOURCE_PATH = Path(__file__).with_name("medical_abbreviations.csv")


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


@lru_cache(maxsize=1)
def _matcher() -> tuple[re.Pattern[str], dict[str, str], int]:
    with SOURCE_PATH.open(newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))

    # Exact source cells win over any aliases derived below, including aliases
    # created by an earlier row.
    by_term: dict[str, str] = {}
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

    for row in rows:
        expanded = row["Expanded version"].strip()
        abbreviation = row["Abbreviation"].strip()
        if not expanded or "not expanded in source" in expanded.casefold():
            continue
        for alias, replacement in _candidates(expanded, abbreviation):
            key = alias.casefold()
            if key and key not in by_term:
                by_term[key] = replacement

    terms = sorted(by_term, key=len, reverse=True)
    pattern = re.compile(
        r"(?<!\w)(?ai:" + "|".join(re.escape(term) for term in terms) + r")(?!\w)",
    )
    return pattern, by_term, len(rows)


SOURCE_ROW_COUNT = _matcher()[2]


def abbreviate(text: str) -> tuple[str, int, dict]:
    """Replace whole expanded medical terms in one non-cascading pass."""
    pattern, replacements, row_count = _matcher()
    counts: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        replacement = replacements[match.group(0).casefold()]
        counts[replacement] = counts.get(replacement, 0) + 1
        return replacement

    result, count = pattern.subn(replace, text)
    return result, count, {"source_rows": row_count, "replacements": counts}


__all__ = ["SOURCE_PATH", "SOURCE_ROW_COUNT", "abbreviate"]
