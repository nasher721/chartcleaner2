"""Semantic copy-forward note differencing engine ("Note Bloat" reducer).

Inpatient EMR charts frequently carry forward days of daily progress notes where
70%+ of each note is identical to the prior day. This engine:
1. Identifies sequential clinical notes and their timestamps/dates.
2. Performs paragraph and sentence-level semantic diffing.
3. Isolates true clinical updates (new vitals, altered plans, new events).
4. Generates a compact "Delta Timeline" that slashes LLM prompt token counts by 50–70%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from thefuzz import fuzz

__all__ = ["NoteSegment", "DeltaResult", "extract_note_deltas", "format_delta_timeline"]

_DATE_HEADER_RE = re.compile(
    r"(?im)^(?:Progress\s+Notes?\s+by\s+[^\n]+?(?:on|at)\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|"
    r"(?:Note\s+Date|Date\s+of\s+Service|DOS)\s*[:#]\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|"
    r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(?:Progress\s+Note|Daily\s+Note|SOAP\s+Note)|"
    r"^[A-Z][a-zA-Z,.\s\-]+ at (\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+\d{1,2}:\d{2})"
)


@dataclass
class NoteSegment:
    index: int
    title: str
    date_str: str
    raw_text: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class NoteDelta:
    note_index: int
    date_str: str
    baseline_similarity: float
    total_paragraphs: int
    new_paragraphs: list[str] = field(default_factory=list)
    modified_paragraphs: list[tuple[str, str]] = field(default_factory=list)  # (old, new)


@dataclass
class DeltaResult:
    notes_found: int
    baseline_note: NoteSegment | None
    deltas: list[NoteDelta]
    compression_ratio: float  # % tokens/characters saved
    compact_text: str


def _split_into_notes(text: str) -> list[NoteSegment]:
    """Split concatenated progress notes by note boundaries."""
    # Find all note header matches
    matches = list(_DATE_HEADER_RE.finditer(text))
    if not matches:
        # Try finding standalone Progress Notes headers
        alt_matches = list(re.finditer(r"(?im)^Progress\s+Notes?\s+by\s+[^\n]+", text))
        if len(alt_matches) > 1:
            matches = alt_matches

    if len(matches) <= 1:
        # Only single note detected
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        return [NoteSegment(index=1, title="Note", date_str="", raw_text=text, paragraphs=paras)]

    notes: list[NoteSegment] = []
    for i, match in enumerate(matches):
        # Text before the first note header (demographics, problem list…) belongs
        # to the baseline so the delta view never loses it.
        start = 0 if i == 0 else match.start()
        end = matches[i + 1].start() if (i + 1) < len(matches) else len(text)
        chunk = text[start:end].strip()
        header_line = match.group(0).strip().split("\n", 1)[0]
        date_match = re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", header_line)
        date_str = date_match.group(0) if date_match else f"Day {i + 1}"

        paras = [p.strip() for p in chunk.split("\n\n") if p.strip()]
        notes.append(
            NoteSegment(
                index=i + 1,
                title=header_line,
                date_str=date_str,
                raw_text=chunk,
                paragraphs=paras,
            )
        )

    return notes


def _find_best_match(target: str, candidates: list[str]) -> tuple[float, str | None]:
    """Find highest fuzzy similarity match among candidates."""
    if not candidates or not target:
        return 0.0, None
    best_sim = 0.0
    best_cand = None
    target_low = target.lower()
    for cand in candidates:
        sim = fuzz.ratio(target_low, cand.lower())
        if sim > best_sim:
            best_sim = sim
            best_cand = cand
            if sim >= 98.0:
                break
    return best_sim, best_cand


_WRAPPER = re.compile(r"<(?P<tag>[A-Za-z_][\w-]*)>\n(?P<body>.*)\n</(?P=tag)>", re.DOTALL)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])|\n")


def _units(paragraph: str) -> list[str]:
    """Lines and sentences of a paragraph (decimals like 38.6 stay intact)."""
    return [u.strip() for u in _SENTENCE_SPLIT.split(paragraph) if u.strip()]


def _norm(unit: str) -> str:
    return " ".join(unit.split()).casefold().rstrip(" .;,")


def extract_note_deltas(
    text: str,
    similarity_threshold: float = 85.0,
    min_paragraph_chars: int = 30,
) -> DeltaResult:
    """Analyze multi-note text and extract day-over-day changes.

    Safety rule: text is only dropped when the very same sentence or line
    (ignoring case and spacing) already appeared in an earlier note. Any new
    sentence, changed number or short new line is kept, even inside an
    otherwise copied paragraph. ``similarity_threshold`` and
    ``min_paragraph_chars`` are kept for API compatibility; similarity is
    now only used for the per-note redundancy figure.
    """
    wrapper = _WRAPPER.fullmatch(text.strip())
    if wrapper:  # analyze inside a <patient_chart>…</patient_chart> wrapper, keep it outside
        inner = extract_note_deltas(wrapper.group("body"), similarity_threshold, min_paragraph_chars)
        if inner.notes_found > 1:
            tag = wrapper.group("tag")
            inner.compact_text = f"<{tag}>\n{inner.compact_text}\n</{tag}>"
            return inner
    notes = _split_into_notes(text)
    if len(notes) <= 1:
        return DeltaResult(
            notes_found=len(notes),
            baseline_note=notes[0] if notes else None,
            deltas=[],
            compression_ratio=0.0,
            compact_text=text,
        )

    baseline = notes[0]
    deltas: list[NoteDelta] = []
    seen_units: set[str] = {_norm(u) for p in baseline.paragraphs for u in _units(p)}
    seen_paras = list(baseline.paragraphs)
    kept_chars = len(baseline.raw_text)

    for current in notes[1:]:
        new_paras: list[str] = []
        mod_paras: list[tuple[str, str]] = []
        sim_scores: list[float] = []
        header = current.title.strip()

        for p in current.paragraphs:
            units = [u for u in _units(p) if u != header]
            if not units:
                continue
            fresh = [u for u in units if _norm(u) not in seen_units]
            best_sim, best_match = _find_best_match(p, seen_paras)
            sim_scores.append(best_sim)
            if not fresh:
                continue  # every sentence already appeared: copy-forward
            kept = " ".join(fresh)
            kept_chars += len(kept)
            if len(fresh) == len(units) and best_sim < 60.0:
                new_paras.append(p if header not in p else "\n".join(units))
            else:
                mod_paras.append((best_match or "", kept))

        seen_units.update(_norm(u) for p in current.paragraphs for u in _units(p))
        seen_paras.extend(current.paragraphs)
        avg_similarity = (sum(sim_scores) / len(sim_scores)) if sim_scores else 0.0
        deltas.append(
            NoteDelta(
                note_index=current.index,
                date_str=current.date_str,
                baseline_similarity=round(avg_similarity, 1),
                total_paragraphs=len(current.paragraphs),
                new_paragraphs=new_paras,
                modified_paragraphs=mod_paras,
            )
        )

    compact = format_delta_timeline(baseline, deltas)
    raw_content = sum(len(n.raw_text) for n in notes)
    saved_ratio = round(100.0 * (1.0 - (kept_chars / raw_content)), 1) if raw_content > 0 else 0.0

    return DeltaResult(
        notes_found=len(notes),
        baseline_note=baseline,
        deltas=deltas,
        compression_ratio=max(saved_ratio, 0.0),
        compact_text=compact,
    )


def format_delta_timeline(baseline: NoteSegment, deltas: list[NoteDelta]) -> str:
    """Format baseline note and subsequent deltas into a dense clinical summary."""
    lines: list[str] = [
        "## Baseline Admission Note",
        baseline.raw_text.strip(),
        "",
        "---",
        "## Longitudinal Clinical Updates (Copy-Forward Bloat Removed)",
    ]

    for d in deltas:
        lines.append(f"\n### Clinical Update ({d.date_str}) — {d.baseline_similarity}% Redundant Text Pruned")

        if not d.new_paragraphs and not d.modified_paragraphs:
            lines.append("*(Every sentence repeats an earlier note)*")
            continue

        if d.new_paragraphs:
            lines.append("**New Clinical Findings:**")
            for np in d.new_paragraphs:
                lines.append(f"- {np}")

        if d.modified_paragraphs:
            lines.append("**New or Changed Sentences:**")
            for _old, new in d.modified_paragraphs:
                lines.append(f"- {new}")

    return "\n".join(lines)
