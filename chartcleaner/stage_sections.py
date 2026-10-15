"""Timestamp removal and section keep/drop (split from ``stages.py``)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .stage_core import _compiled

if TYPE_CHECKING:
    from .engine import CleanContext

__all__ = [
    "DEFAULT_TIMESTAMPS",
    "DEFAULT_SECTIONS",
    "BUILTIN_TIMESTAMP_PATTERNS",
    "CLOCK_TIME_PATTERN",
    "run_timestamps",
    "run_sections",
]

DEFAULT_TIMESTAMPS = {
    "enabled": False,
    "replacement": "",
    "builtin_patterns": True,
    "extra_patterns": [],
    "remove_clock_times": False,
}

DEFAULT_SECTIONS = {
    "mode": "off",
    "sections": [],
    "boundary_headers": [],
    "keep_preamble": True,
}

BUILTIN_TIMESTAMP_PATTERNS = [
    r"\b\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?(?:\s?(?:Z|[+-]\d{2}:?\d{2}))?\b",
    r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"(?:,?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)?\b",
]

CLOCK_TIME_PATTERN = r"(?<![\d:])\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?\b(?![\d:])"


def run_timestamps(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Strip or replace date/time stamps."""
    options = {**DEFAULT_TIMESTAMPS, **(cfg.get("timestamp_removal") or {})}
    if not options.get("enabled"):
        return text, 0, {}

    repl = str(options.get("replacement") or "")
    patterns = list(options.get("extra_patterns") or [])
    if options.get("builtin_patterns", True):
        patterns = BUILTIN_TIMESTAMP_PATTERNS + patterns

    total = 0
    for p in patterns:
        if not isinstance(p, str) or not p:
            continue
        try:
            r = _compiled(p, re.IGNORECASE)
        except re.error:
            ctx.log(f"bad timestamp pattern skipped: {p!r}")
            continue
        text, k = r.subn(repl, text)
        total += k

    if options.get("remove_clock_times"):
        text, k = re.subn(CLOCK_TIME_PATTERN, repl, text)
        total += k

    return text, total, {}


def run_sections(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Filter document sections by dropping or keeping specified headers."""
    options = {**DEFAULT_SECTIONS, **(cfg.get("section_filter") or {})}
    mode = str(options.get("mode") or "off")
    if mode not in ("drop", "keep"):
        return text, 0, {}

    names = [n for n in (options.get("sections") or []) if isinstance(n, str) and n]
    if not names:
        return text, 0, {}

    boundary = [b for b in (options.get("boundary_headers") or []) if isinstance(b, str) and b]
    if not boundary:
        boundary = [h for h in (cfg.get("clinical_headers") or []) if isinstance(h, str) and h]
    if not boundary:
        boundary = names

    flags = re.IGNORECASE | re.MULTILINE
    try:
        bounds = [(m.start(), m.group(1))
                  for m in re.finditer(rf"^[ \t]*({'|'.join(boundary)})[ \t]*:?[ \t]*$", text, flags)]
        member_re = re.compile(rf"^[ \t]*(?:{'|'.join(names)})[ \t]*:?[ \t]*$", flags)
    except re.error as e:
        ctx.log(f"section_filter: bad header name ({e}); stage skipped.")
        return text, 0, {}

    if not bounds:
        return text, 0, {}

    return _filter_section_bounds(text, bounds, member_re, mode, bool(options.get("keep_preamble", True)))


def _filter_section_bounds(
    text: str, bounds: list[tuple[int, str]], member_re: re.Pattern, mode: str, keep_preamble: bool
) -> tuple[str, int, dict]:
    first_start = bounds[0][0]
    out = text[:first_start] if keep_preamble else ""
    pos = first_start
    removed: list[str] = []

    for i, (start, header) in enumerate(bounds):
        seg_end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        is_member = member_re.match(text[start:seg_end].split("\n", 1)[0] + "\n") is not None
        should_remove = is_member if mode == "drop" else not is_member
        if should_remove:
            removed.append(header.strip())
        else:
            out += text[pos:seg_end]
        pos = seg_end

    out += text[pos:]
    return out, len(removed), {"sections_removed": removed}
