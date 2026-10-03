"""Suggest abbreviations for long phrases that keep coming back in charts.

``mine`` counts 2–5 word phrases (letters only, never across punctuation or
line breaks), drops ones the dictionary already covers or the user dismissed,
and ranks the rest by how much text an abbreviation would save. Each distinct
line counts once (copied-forward notes don't inflate counts), and phrases with
capitalized inner words (names, headings) are skipped. Nothing is stored:
suggestions are computed from the text handed in.
"""

from __future__ import annotations

import re
from collections import Counter

from .abbreviations import lookup, normalize_settings, suggest

__all__ = ["mine"]

_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from", "had", "has",
    "have", "he", "her", "his", "if", "in", "into", "is", "it", "its", "of", "on", "or", "she",
    "that", "the", "their", "then", "there", "these", "they", "this", "to", "was", "were",
    "which", "while", "will", "with", "without", "would", "no", "not", "per", "than", "so",
    "patient", "pt", "will", "also", "very", "any", "all", "some",
}
# Runs of letters (with inner hyphens/apostrophes) separated only by spaces.
_CHUNK = re.compile(r"[A-Za-z][A-Za-z'\-]*(?:[ \t]+[A-Za-z][A-Za-z'\-]*)+")


_INNER_OK = {"of", "and"}


def _phrases(line: str, max_words: int):
    for chunk in _CHUNK.findall(line):
        words = chunk.split()
        for n in range(2, max_words + 1):
            for i in range(len(words) - n + 1):
                gram = words[i:i + n]
                lowered = [w.casefold() for w in gram]
                if lowered[0] in _STOP or lowered[-1] in _STOP:
                    continue
                if any(w in _STOP and w not in _INNER_OK for w in lowered[1:-1]):
                    continue  # reads like a sentence, not a term
                if any(w.isupper() and len(w) > 1 for w in gram):
                    continue  # already contains an abbreviation
                if any(w[0].isupper() for w in gram[1:]):
                    continue  # capitalized mid-phrase: a name or a heading
                yield " ".join(lowered)


def mine(texts: list[str] | str, cfg: dict | None = None, *, min_count: int = 3,
         max_words: int = 5, limit: int = 25) -> list[dict]:
    """Ranked ``{"phrase", "count", "suggestion", "saves"}`` suggestions."""
    if isinstance(texts, str):
        texts = [texts]
    counts: Counter[str] = Counter()
    seen_lines: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            key = " ".join(line.split()).casefold()
            if not key or key in seen_lines:
                continue  # copied-forward lines count once
            seen_lines.add(key)
            counts.update(set(_phrases(line, max_words)))
    group = normalize_settings((cfg or {}).get("abbreviations") if isinstance(cfg, dict) else None)
    skip = set(group.get("rejected_suggestions", [])) | {c["term"].casefold() for c in group["custom"]}
    frequent = {p: n for p, n in counts.items() if n >= min_count}
    # Prefer the longest phrase: drop a phrase when a longer one containing it
    # occurs just as often ("side weakness" inside "left side weakness"), even
    # when that longer phrase is itself already saved or dismissed.
    kept = {p: n for p, n in frequent.items()
            if p not in skip
            and not any(f" {p} " in f" {q} " for q in skip)
            and not any(n2 >= n and len(q) > len(p) and f" {p} " in f" {q} "
                        for q, n2 in frequent.items())}
    out = []
    for phrase, n in kept.items():
        if lookup(phrase, cfg):
            continue  # the dictionary already shortens it
        short = suggest(phrase, cfg)
        if len(short) < 2 or len(short) >= len(phrase):
            continue
        out.append({"phrase": phrase, "count": n, "suggestion": short,
                    "saves": n * (len(phrase) - len(short))})
    out.sort(key=lambda s: (-s["saves"], s["phrase"]))
    return out[:limit]
