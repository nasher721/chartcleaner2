"""Near-duplicate folding: whole notes, paragraphs and lines (split from ``stages.py``)."""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import CleanContext

__all__ = [
    "DEFAULT_NOTE_SPLIT",
    "DEFAULT_FUZZY_LINE",
    "fuzz_ratio",
    "run_duplicate_notes",
    "run_fuzzy_dedup",
    "run_fuzzy_line_dedup",
]

DEFAULT_NOTE_SPLIT = (
    r"(?=^(?:Progress Notes by .+|Attestation signed by .+|"
    r"[A-Z][a-zA-Z,.\s\-]+ at \d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s*(?:[APap]\.?[Mm]\.?)?.*)$)"
)

DEFAULT_FUZZY_LINE = {
    "enabled": False,
    "threshold": 92,
    "min_chars": 8,
    "window": 50,
}


def fuzz_ratio(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings using RapidFuzz/TheFuzz."""
    from thefuzz import fuzz
    return fuzz.ratio(a, b)


def run_duplicate_notes(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Fold repeated copy-forward notes based on body similarity.

    A near-duplicate note is folded down to its header plus every line that
    does not literally repeat a line of an earlier note (whitespace and case
    aside), so content that exists only in the later copy is never lost —
    the same guarantee ``delta_engine`` gives at sentence level. Only a note
    with nothing new is dropped outright.
    """
    dcfg = cfg.get("duplicate_note_detection") or {}
    if dcfg.get("enabled") is False:
        return text, 0, {}

    split_pattern = dcfg.get("split_pattern", DEFAULT_NOTE_SPLIT)
    min_body = int(dcfg.get("min_body_chars", 400))
    threshold = int(dcfg.get("similarity_threshold", 90))

    parts = re.compile(split_pattern, re.MULTILINE | re.IGNORECASE).split(text)
    if len(parts) <= 1:
        return text, 0, {}

    from rapidfuzz import fuzz as rf_fuzz, process as rf_process

    kept = [parts[0]]
    seen_bodies: list[str] = []
    seen_bodies_set: set[str] = set()
    # Every line of every earlier segment: each one is in the output already
    # (kept verbatim, or itself a repeat of an earlier line).
    seen_lines: set[str] = {_line_key(ln) for ln in parts[0].splitlines()}
    dropped = 0
    partial = 0
    lines_kept = 0

    for seg in parts[1:]:
        body = _extract_body(seg)
        body_low = body.lower()
        folded = len(body) >= min_body and (
            body_low in seen_bodies_set
            or (seen_bodies and rf_process.extractOne(
                body_low, seen_bodies, scorer=rf_fuzz.ratio, score_cutoff=threshold
            ) is not None))
        if not folded:
            if len(body) >= min_body:
                seen_bodies.append(body_low)
                seen_bodies_set.add(body_low)
            kept.append(seg)
        else:
            unique = _unique_lines(seg, seen_lines)
            if unique is None:
                dropped += 1
            else:
                partial += 1
                lines_kept += unique[1]
                kept.append(unique[0])
        seen_lines.update(_line_key(ln) for ln in seg.splitlines())

    details: dict = {"notes_kept": len(kept)}
    if partial:
        details["notes_partially_folded"] = partial
        details["unique_lines_kept"] = lines_kept
    return "".join(kept), dropped + partial, details


def _line_key(line: str) -> str:
    return " ".join(line.split()).lower()


def _unique_lines(segment: str, seen_lines: set[str]) -> tuple[str, int] | None:
    """The folded form of a near-duplicate *segment*: its header line plus the
    lines no earlier segment has, or ``None`` when every body line repeats."""
    lines = segment.splitlines(keepends=True)
    header_idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if header_idx is None:
        return None
    new = [ln for ln in lines[header_idx + 1:]
           if ln.strip() and _line_key(ln) not in seen_lines]
    if not new:
        return None
    header = lines[header_idx]
    if not header.endswith("\n"):
        header += "\n"
    if not new[-1].endswith("\n"):
        new[-1] += "\n"
    tail = "\n" if segment.endswith("\n\n") else ""
    return header + "".join(new) + tail, len(new)


def _extract_body(segment: str) -> str:
    s = segment.strip()
    if not s or "\n" not in s:
        return ""
    return s.split("\n", 1)[1].strip()


def run_fuzzy_dedup(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Deduplicate repeated paragraphs using fuzzy string matching."""
    fcfg = cfg.get("fuzzy_dedup") or {}
    if fcfg.get("enabled") is False:
        return text, 0, {}

    threshold = int(fcfg.get("threshold", 95))
    min_chars = int(fcfg.get("min_chars", 100))

    from rapidfuzz import fuzz as rf_fuzz, process as rf_process

    paragraphs = text.split("\n\n")
    deduped: list[str] = []
    eligible: list[str] = []  # lowered, stripped candidates that can match future paragraphs
    eligible_set: set[str] = set()
    dropped = 0

    for p in paragraphs:
        p_clean = p.strip()
        if len(p_clean) < min_chars:
            deduped.append(p)
            continue
        cand_low = p_clean.lower()
        if cand_low in eligible_set:
            dropped += 1
            continue
        if eligible and rf_process.extractOne(
            cand_low, eligible, scorer=rf_fuzz.ratio, score_cutoff=threshold
        ) is not None:
            dropped += 1
        else:
            deduped.append(p)
            if len(p_clean) > min_chars:
                eligible.append(cand_low)
                eligible_set.add(cand_low)

    return "\n\n".join(deduped), dropped, {"paragraphs_kept": len(deduped)}


def run_fuzzy_line_dedup(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Collapse near-duplicate lines (e.g. copy-forwarded signature stamps).

    Unlike :func:`run_fuzzy_dedup` (which works on blank-line separated
    paragraphs), this folds individual lines that are nearly identical to a
    recently kept line, keeping only the first. The comparison window bounds the
    cost on long charts; default off so existing output is unchanged.
    """
    fcfg = cfg.get("fuzzy_line_dedup") or {}
    if not fcfg.get("enabled", False):
        return text, 0, {}
    try:
        from rapidfuzz import fuzz as rf_fuzz
    except Exception:
        ctx.log("fuzzy_line_dedup: rapidfuzz unavailable; stage skipped.")
        return text, 0, {}

    threshold = int(fcfg.get("threshold", DEFAULT_FUZZY_LINE["threshold"]))
    min_chars = int(fcfg.get("min_chars", DEFAULT_FUZZY_LINE["min_chars"]))
    window = int(fcfg.get("window", DEFAULT_FUZZY_LINE["window"]))

    lines = text.split("\n")
    if len(lines) <= 1:
        return text, 0, {}

    recent: deque[str] = deque(maxlen=max(1, window))
    dropped = 0
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) < min_chars:
            kept.append(line)
            continue
        low = stripped.lower()
        if low in recent or any(rf_fuzz.ratio(low, cand) >= threshold for cand in recent):
            dropped += 1
        else:
            kept.append(line)
            recent.append(low)

    return "\n".join(kept), dropped, {"lines_kept": len(kept)}
