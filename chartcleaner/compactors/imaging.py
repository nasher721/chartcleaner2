"""Keep only the Impression of radiology reports (stage ``imaging_impression``).

A report is recognized by a FINDINGS header followed, within the next 80 lines
and before any non-radiology header, by an IMPRESSION (or CONCLUSION) header.
Radiology sub-sections just above FINDINGS (indication, technique, comparison,
clinical history) are part of the report too. Everything from the first of
those headers up to IMPRESSION is removed unless listed in ``keep``; the report
title line and the impression itself always stay. Blocks without an
impression are left untouched.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "keep": []}

KEEPABLE = ("indication", "technique", "comparison", "findings")
_HEADER = re.compile(
    r"^[ \t]*(?P<name>indications?|clinical\s+(?:history|indication)|history|reason\s+for\s+exam"
    r"|technique|comparisons?|findings?|impressions?|conclusions?)[ \t]*:",
    re.IGNORECASE | re.MULTILINE)
_ANY_HEADER = re.compile(r"^[ \t]*[A-Za-z][A-Za-z /&()-]{1,40}:[ \t]*(?:$|\S)", re.MULTILINE)
_MAX_REPORT_LINES = 80


def _kind(name: str) -> str:
    name = name.casefold()
    if name.startswith(("indication", "clinical", "history", "reason")):
        return "indication"
    if name.startswith("comparison"):
        return "comparison"
    if name.startswith("finding"):
        return "findings"
    if name.startswith(("impression", "conclusion")):
        return "impression"
    return "technique"


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("imaging_impression") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    keep = {k for k in options.get("keep") or [] if k in KEEPABLE}
    headers = [(m.start(), _kind(m.group("name"))) for m in _HEADER.finditer(text)]
    cuts: list[tuple[int, int]] = []
    reports: set[int] = set()
    for i, (pos, kind) in enumerate(headers):
        if kind != "findings":
            continue
        impression = next(((p, k) for p, k in headers[i + 1:] if k in ("impression", "findings")), None)
        if impression is None or impression[1] != "impression":
            continue
        imp_pos = impression[0]
        between = text[pos:imp_pos]
        if between.count("\n") > _MAX_REPORT_LINES:
            continue
        radiology_starts = {p for p, _ in headers}
        if any(m.start() + pos not in radiology_starts for m in _ANY_HEADER.finditer(between)):
            continue  # another kind of section sits in between: not a radiology report
        # Pull in the radiology sub-sections directly above FINDINGS.
        start, j = pos, i - 1
        while j >= 0 and headers[j][1] in ("indication", "technique", "comparison"):
            gap = text[headers[j][0]:start]
            if any(m.start() + headers[j][0] not in radiology_starts for m in _ANY_HEADER.finditer(gap)):
                break
            start, j = headers[j][0], j - 1
        sections = [(p, k) for p, k in headers if start <= p < imp_pos] + [(imp_pos, "impression")]
        for (s_pos, s_kind), (e_pos, _) in zip(sections, sections[1:]):
            if s_kind not in keep:
                cuts.append((s_pos, e_pos))
                reports.add(imp_pos)
    if not cuts:
        return text, 0, {}
    out, last = [], 0
    for s, e in sorted(set(cuts)):
        if s < last:
            continue
        out.append(text[last:s])
        last = e
    out.append(text[last:])
    return "".join(out), len(reports), {"reports_trimmed": len(reports)}
