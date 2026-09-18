"""Reversible tokenization: swap PHI for stable codes, restore later.

Inspired by redactable's "reversible tokenization": each distinct PHI string
found by the pipeline's structured patterns is replaced with ``[[T1]]``,
``[[T2]]``, … — the same value always gets the same token, so repeated names
stay consistent and near-duplicate folding still works. The value→token map
is persisted (``store.save_token_map``) so :func:`untokenize` can restore the
original text when the holder of the map wants it back.

Engine integration: the ``tokenize_phi`` stage (default off) collects spans
from config ``epic_phi_patterns`` plus identity-label lines, rewrites them,
and returns the map in ``StageStat.details["token_map"]``. Run history strips
the map (only counts are recorded); the app saves it to ``data/tokens/``.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import store

__all__ = [
    "tokenize",
    "untokenize",
    "tokenize_phi_spans",
    "DEFAULT_TOKEN_CONFIG",
]

DEFAULT_TOKEN_CONFIG = {
    "enabled": False,   # off by default: output must stay shareable unless asked
    "prefix": "T",
}

_TOKEN_RE_TEMPLATE = r"\[\[{prefix}\d+\]\]"


def token_pattern(prefix: str = "T") -> re.Pattern:
    return re.compile(_TOKEN_RE_TEMPLATE.format(prefix=re.escape(prefix or "T")))


def tokenize_phi_spans(text: str, patterns: list[tuple[str, str]] | list[str],
                       labels: list[str] | None = None) -> list[tuple[int, int, str]]:
    """Collect (start, end, matched_text) PHI spans from structured patterns.

    Every occurrence is collected; overlapping matches are resolved
    longest-first. Spans already inside a ``[REDACTED…]`` placeholder are
    skipped.
    """
    spans: list[tuple[int, int, str]] = []
    for pat in patterns:
        p = pat[0] if isinstance(pat, (list, tuple)) and len(pat) == 2 else pat
        if not isinstance(p, str) or not p:
            continue
        try:
            for m in re.finditer(p, text, flags=re.IGNORECASE):
                value = m.group(0).strip()
                if value:
                    spans.append((m.start(), m.end(), value))
        except re.error:
            continue  # invalid patterns are the pipeline's problem, not ours

    # values sitting after identity labels (Patient: Jane Roe)
    if labels:
        for lab in labels:
            try:
                for m in re.finditer(
                        rf"(?im)^\s*{re.escape(lab)}\s*:\s*(.+?)\s*$", text):
                    value = m.group(1).strip()
                    if value and not _JUNK.fullmatch(value):
                        spans.append((m.start(1), m.end(1), value))
            except re.error:
                continue

    # longest first so "John Smith Jr" wins over "John Smith"
    spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    out: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    for start, end, value in spans:
        if any(not (end <= a or start >= b) for a, b in taken):
            continue
        if _PLACEHOLDER.fullmatch(value):
            continue
        taken.append((start, end))
        out.append((start, end, value))
    return out


_JUNK = re.compile(r"^[\W\d]+$")
_PLACEHOLDER = re.compile(r"\[\s*REDACT[^\]]*\]", re.IGNORECASE)


def tokenize(text: str, patterns, labels: list[str] | None = None,
             prefix: str = "T") -> tuple[str, dict[str, str]]:
    """Replace PHI values with [[Tn]] tokens. Returns (new_text, value→token map)."""
    spans = tokenize_phi_spans(text, patterns, labels)
    mapping: dict[str, str] = {}
    out = text
    # Rewrite right-to-left so earlier spans keep their offsets.
    for start, end, value in sorted(spans, key=lambda s: -s[0]):
        tok = mapping.get(value)
        if tok is None:
            tok = f"[[{prefix}{len(mapping) + 1}]]"
            mapping[value] = tok
        out = out[:start] + tok + out[end:]
    return out, mapping


def untokenize(text: str, mapping: dict[str, str], prefix: str = "T") -> tuple[str, int]:
    """Replace [[Tn]] tokens with their original values. Returns (text, count)."""
    reverse = {tok: val for val, tok in mapping.items()}
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        val = reverse.get(m.group(0))
        if val is None:
            return m.group(0)
        count += 1
        return val

    return token_pattern(prefix).sub(repl, text), count


# ---------------------------------------------------------------------------
# CLI/app helper: find the newest saved token map
# ---------------------------------------------------------------------------

def newest_token_map() -> tuple[Path, dict[str, str]] | None:
    maps = store.list_token_maps()
    if not maps:
        return None
    path = Path(maps[0]["file"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return path, dict(data.get("map") or {})
    except (OSError, json.JSONDecodeError):
        return None


def build_map_record(mapping: dict[str, str], source: str = "") -> str:
    return json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "map": mapping,
    }, ensure_ascii=False)
