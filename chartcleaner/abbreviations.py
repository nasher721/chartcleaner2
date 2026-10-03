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

from .abbreviation_safety import is_blocked

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
            custom.append({"term": term.strip(), "replacement": replacement, "enabled": item.get("enabled", True),
                           "acknowledged": item.get("acknowledged") is True})
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
            entry = {"term": term.strip(), "replacement": replacement, "enabled": enabled}
            # Optional fields: a Do Not Use override and the pack an entry came from.
            if item.get("acknowledged") is True:
                entry["acknowledged"] = True
            if isinstance(item.get("pack"), str) and item["pack"].strip():
                entry["pack"] = item["pack"].strip()
            custom.append(entry)
            seen.add(term.casefold())
    out: dict[str, Any] = {"disabled": disabled, "custom": custom}
    scope = _normalize_scope(group.get("scope"))
    if scope:
        out["scope"] = scope
    prefer = group.get("expand_prefer")
    if isinstance(prefer, dict):
        prefer = {k.strip(): v.strip() for k, v in prefer.items()
                  if isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()}
        if prefer:
            out["expand_prefer"] = prefer
    rejected = group.get("rejected_suggestions")
    if isinstance(rejected, list):
        rejected = sorted({r.strip().casefold() for r in rejected if isinstance(r, str) and r.strip()})
        if rejected:
            out["rejected_suggestions"] = rejected
    return out


SCOPE_MODES = ("all", "only", "except")


def _normalize_scope(scope: Any) -> dict | None:
    """``{"mode": "only"|"except", "sections": [...]}``; None means everywhere."""
    if not isinstance(scope, dict) or scope.get("mode") not in ("only", "except"):
        return None
    sections = [x.strip() for x in scope.get("sections", []) if isinstance(x, str) and x.strip()]
    return {"mode": scope["mode"], "sections": sections} if sections else None


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
        replacement: str | None = abbreviation
        if is_blocked(abbreviation, expanded):
            # Do Not Use abbreviations are never applied from the bundled list;
            # a user can still allow one explicitly as an acknowledged custom entry.
            replacement = None
        elif parenthetical and parenthetical.group(2).casefold() in _UNCERTAINTY:
            replacement += " (" + parenthetical.group(2) + ")"
        by_term.setdefault(expanded.casefold(), replacement)
        aliases_by_source.setdefault(expanded.casefold(), set()).add(expanded.casefold())

    for row in rows:
        expanded = row["Expanded version"].strip()
        abbreviation = row["Abbreviation"].strip()
        if not expanded or "not expanded in source" in expanded.casefold():
            continue
        blocked = is_blocked(abbreviation, expanded)
        for alias, replacement in _candidates(expanded, abbreviation):
            key = alias.casefold()
            if key and key not in by_term:
                by_term[key] = None if blocked else replacement
            aliases_by_source.setdefault(expanded.casefold(), set()).add(key)

    for source in disabled:
        for alias in aliases_by_source.get(source, {source}):
            # Match disabled phrases as a unit so shorter terms inside them
            # cannot still be abbreviated (e.g. "artery" inside a disabled ACA).
            by_term[alias] = None

    custom_terms: dict[str, str] = {}
    for item in custom:
        key = item["term"].casefold()
        active = item.get("enabled", True) and (
            item.get("acknowledged") or not is_blocked(item["replacement"], item["term"]))
        for alias in aliases_by_source.get(key, {key}):
            by_term[alias] = item["replacement"] if active else None
            if active:
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


def _abbreviate_span(segment: str, cfg: dict | None, changes: list[dict] | None,
                     offset: int = 0, full: str | None = None) -> tuple[str, dict[str, int], int]:
    """Abbreviate one stretch of text; change positions are reported in ``full``."""
    full = segment if full is None else full
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
            start = offset + match.start()
            changes.append({
                "rule": match.group(0).casefold(),
                "source": "custom" if match.lastgroup else "bundled",
                "before": match.group(0),
                "after": replacement,
                "line": full.count("\n", 0, start) + 1,
                "start": start,
            })
        return replacement

    return pattern.sub(replace, segment), counts, row_count


def _section_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold().replace("&", " and ")).strip()


def scoped_spans(text: str, scope: dict) -> list[tuple[int, int]] | None:
    """Character spans the scope allows; None when the chart has no section headers."""
    from .section_parser import parse_clinical_sections

    sections = parse_clinical_sections(text).sections
    if not sections:
        return None
    wanted = {_section_key(n) for n in scope["sections"]}
    spans = []
    if scope["mode"] == "except" and sections[0].start_pos > 0:
        spans.append((0, sections[0].start_pos))  # text before the first header
    for sec in sections:
        named = _section_key(sec.title) in wanted or _section_key(sec.domain) in wanted
        if named == (scope["mode"] == "only"):
            spans.append((sec.start_pos, sec.end_pos))
    return spans


def abbreviate(text: str, cfg: dict | None = None,
               changes: list[dict] | None = None) -> tuple[str, int, dict]:
    """Replace whole expanded medical terms in one non-cascading pass.

    Honors ``abbreviations.scope`` (only/except named sections). When
    ``changes`` is a list, each replacement is recorded in it (and returned
    as ``details["changes"]``) for the app's inspect views.
    """
    group = cfg.get("abbreviations") if isinstance(cfg, dict) else None
    scope = _normalize_scope(group.get("scope")) if isinstance(group, dict) else None
    details: dict[str, Any] = {}
    if scope is None:
        result, counts, row_count = _abbreviate_span(text, cfg, changes)
    else:
        spans = scoped_spans(text, scope)
        if spans is None:
            # No headers: "except" has nothing to skip; "only" has nothing to touch.
            spans = [(0, len(text))] if scope["mode"] == "except" else []
            details["note"] = "No section headers found."
        parts: list[str] = []
        counts = {}
        row_count = SOURCE_ROW_COUNT
        pos = 0
        for start, end in spans:
            parts.append(text[pos:start])
            segment, seg_counts, row_count = _abbreviate_span(text[start:end], cfg, changes, start, text)
            parts.append(segment)
            for key, n in seg_counts.items():
                counts[key] = counts.get(key, 0) + n
            pos = end
        parts.append(text[pos:])
        result = "".join(parts)
        details["scope"] = scope
    details = {"source_rows": row_count, "replacements": counts, **details}
    if changes is not None:
        details["changes"] = changes
    return result, sum(counts.values()), details


_SKIP_INITIAL = {"of", "and", "the", "with", "to", "in", "on", "for", "a", "an", "or", "by"}


def lookup(term: str, cfg: dict | None = None) -> str | None:
    """The abbreviation the current settings would use for ``term`` (or None)."""
    term = term.strip()
    changes: list[dict] = []
    out, _, _ = abbreviate(term, cfg, changes=changes)
    whole = len(changes) == 1 and changes[0]["before"] == term
    return out if whole else None


def suggest(term: str, cfg: dict | None = None) -> str:
    """A starting abbreviation for ``term``: the dictionary's, else initials."""
    term = term.strip()
    known = lookup(term, cfg)
    if known:
        return known
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", term)]
    initials = "".join(w[0] for w in words if w.casefold() not in _SKIP_INITIAL)
    return initials.upper() if len(initials) >= 2 else ""


def with_custom(cfg: dict | None, term: str, replacement: str, *, acknowledged: bool = False) -> dict:
    """A copy of ``cfg`` with ``term`` → ``replacement`` as an enabled custom rule."""
    cfg = dict(cfg or {})
    group = normalize_settings(cfg.get("abbreviations"))
    key = term.strip().casefold()
    group["disabled"] = [t for t in group["disabled"] if t.casefold() != key]
    group["custom"] = [c for c in group["custom"] if c["term"].casefold() != key]
    entry = {"term": term.strip(), "replacement": replacement, "enabled": True}
    if acknowledged:
        entry["acknowledged"] = True
    group["custom"].append(entry)
    cfg["abbreviations"] = normalize_settings(group)
    return cfg


def preview(text: str, cfg: dict | None, term: str, replacement: str,
            *, samples: int = 5, context: int = 40) -> dict:
    """What adding ``term`` → ``replacement`` would change in ``text``.

    Returns ``{"count": n, "samples": [{"before": ..., "after": ...}]}``
    where samples are short snippets around each newly changed place.
    """
    old: list[dict] = []
    new: list[dict] = []
    abbreviate(text, cfg, changes=old)
    abbreviate(text, with_custom(cfg, term, replacement, acknowledged=True), changes=new)
    before = {(c["start"], c["after"]) for c in old}
    changed = [c for c in new if (c["start"], c["after"]) not in before]
    out = []
    for c in changed[:samples]:
        start, end = c["start"], c["start"] + len(c["before"])
        lo, hi = max(0, start - context), min(len(text), end + context)
        head = ("…" if lo else "") + text[lo:start]
        tail = text[end:hi] + ("…" if hi < len(text) else "")
        out.append({"before": (head + c["before"] + tail).replace("\n", " "),
                    "after": (head + c["after"] + tail).replace("\n", " ")})
    return {"count": len(changed), "samples": out}


_PARENTHETICAL = re.compile(r"\s+\([^()]*\)$")


def display_term(term: str) -> str:
    """How a dictionary term reads in running text: first alternative, no
    trailing explanation, and sentence-case words lowercased ("Hypertension"
    → "hypertension"; codes like "A1 segment" keep their case)."""
    text = _PARENTHETICAL.sub("", term.split(" / ")[0]).strip()
    if (len(text) > 1 and text[0].isupper() and text[1].islower()
            and not any(c.isupper() for c in text[1:])):
        text = text[0].lower() + text[1:]
    return text


def _same_meaning(a: str, b: str) -> bool:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio() >= 0.85


def _expandable(abbr: str) -> bool:
    """Safe to expand without a user choice: not one letter, no spaces, and not a
    plain lowercase word (``reg``, ``ext``) that collides with ordinary English."""
    if len(abbr) < 2 or any(c.isspace() for c in abbr):
        return False
    return not (abbr.isalpha() and abbr.islower())


@lru_cache(maxsize=32)
def _expander(settings: str) -> tuple[re.Pattern[str], dict[str, str | None]]:
    options = json.loads(settings)
    disabled = set(options["disabled"])
    meanings: dict[str, dict[str, str]] = {}   # abbr -> {casefolded meaning: meaning}
    with SOURCE_PATH.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            term = row["Expanded version"].strip()
            abbr = row["Abbreviation"].strip()
            if not term or not abbr or "not expanded in source" in term.casefold():
                continue
            if term.casefold() in disabled:
                continue
            meaning = display_term(term)
            found = meanings.setdefault(abbr, {})
            # Spelling variants (hemorrhage / haemorrhage) are one meaning; the
            # first CSV row wins.
            if not any(_same_meaning(meaning, other) for other in found.values()):
                found[meaning.casefold()] = meaning
    chosen: dict[str, str | None] = {}
    for abbr, found in meanings.items():
        if len(found) == 1 and _expandable(abbr):
            chosen[abbr] = next(iter(found.values()))
        elif len(found) > 1:
            chosen[abbr] = None   # ambiguous: matched so it is counted, never changed
    for item in options["custom"]:
        if item["enabled"]:
            chosen[item["replacement"]] = display_term(item["term"])
    for abbr, meaning in options["prefer"].items():
        chosen[abbr] = meaning
    if not chosen:
        return re.compile(r"(?!x)x"), {}
    alternatives = "|".join(re.escape(a) for a in sorted(chosen, key=len, reverse=True))
    # Never touch reversible tokens like [[T1]].
    pattern = re.compile(r"(?<![\w\[])(?:" + alternatives + r")(?![\w\]])")
    return pattern, chosen


def meanings(abbreviation: str) -> list[str]:
    """Distinct dictionary meanings of ``abbreviation`` (for resolving ambiguity)."""
    found: list[str] = []
    with SOURCE_PATH.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if row["Abbreviation"].strip() != abbreviation:
                continue
            term = row["Expanded version"].strip()
            if not term or "not expanded in source" in term.casefold():
                continue
            meaning = display_term(term)
            if not any(_same_meaning(meaning, other) for other in found):
                found.append(meaning)
    return found


_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n\s*)$")


def expand(text: str, cfg: dict | None = None,
           changes: list[dict] | None = None) -> tuple[str, int, dict]:
    """Expand abbreviations back to full terms in one pass (case-sensitive).

    An abbreviation with several dictionary meanings is left as written and
    counted under ``details["ambiguous"]`` unless ``abbreviations.expand_prefer``
    names the meaning to use. One-letter and plain-lowercase abbreviations are
    only expanded when a custom rule or ``expand_prefer`` covers them.
    """
    group = normalize_settings(cfg.get("abbreviations") if isinstance(cfg, dict) else None)
    settings = json.dumps({
        "disabled": sorted(t.casefold() for t in group["disabled"]),
        "custom": [{"term": c["term"], "replacement": c["replacement"], "enabled": c["enabled"]}
                   for c in group["custom"]],
        "prefer": group.get("expand_prefer", {}),
    }, sort_keys=True, ensure_ascii=False)
    pattern, chosen = _expander(settings)
    counts: dict[str, int] = {}
    ambiguous: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        abbr = match.group(0)
        meaning = chosen[abbr]
        if meaning is None:
            ambiguous[abbr] = ambiguous.get(abbr, 0) + 1
            return abbr
        if _SENTENCE_START.search(text, 0, match.start()) and meaning[:1].islower():
            meaning = meaning[0].upper() + meaning[1:]
        counts[abbr] = counts.get(abbr, 0) + 1
        if changes is not None and len(changes) < MAX_TRACKED_CHANGES:
            changes.append({"rule": abbr, "before": abbr, "after": meaning,
                            "line": text.count("\n", 0, match.start()) + 1,
                            "start": match.start()})
        return meaning

    result = pattern.sub(replace, text)
    details: dict[str, Any] = {"expansions": counts, "ambiguous": ambiguous}
    if changes is not None:
        details["changes"] = changes
    return result, sum(counts.values()), details


__all__ = ["SOURCE_PATH", "SOURCE_ROW_COUNT", "SCOPE_MODES", "abbreviate", "display_term", "expand",
           "lookup", "meanings", "normalize_settings", "preview", "scoped_spans", "suggest", "with_custom"]
