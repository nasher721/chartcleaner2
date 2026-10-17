"""Tiered LLM context-window router and serial-position optimizer.

Implements three deterministic, 100% extractive context-management patterns
for clinical charts:

1. **Tiered Context Strategy**:
   - ``full`` (Tier 1): Chart fits within the token budget -> sent unchanged.
   - ``delta`` (Tier 2): Multi-note chart exceeds budget -> copy-forward duplicate
     sentences in earlier notes are pruned while keeping the latest note in full
     and every unique clinical sentence verbatim.
   - ``routed`` (Tier 3): Chart still exceeds budget -> retains safety-critical
     lines and the latest note in full, and ranks earlier paragraphs by relevance
     to the user's question or summary preset, keeping top paragraphs in
     chronological order up to the token budget.

2. **Serial Position Optimization (Bookending)**:
   - Long prompts (>= ``BOOKEND_MIN_TOKENS``) repeat the core task/question and
     grounding constraint at the end of the prompt so small local models do not
     suffer Lost-in-the-Middle attention decay across multi-day charts.

3. **Zero Synthetic Numbers**:
   - Every retained line comes verbatim from the cleaned chart, so
     ``verify_clinical_grounding``, ``fact_check.verify_output``, and
     ``citations.cite`` against the original chart remain 100% aligned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .citations import _tokens as _cite_tokens
from .delta_engine import _WRAPPER, _norm, _split_into_notes, _units
from .token_estimator import estimate_tokens

__all__ = [
    "BOOKEND_MIN_TOKENS",
    "DEFAULT_CONTEXT_TOKENS",
    "MAX_CONTEXT_TOKENS",
    "MIN_CONTEXT_TOKENS",
    "PackedContext",
    "pack_context",
    "resolve_context_tokens",
    "should_bookend",
]

DEFAULT_CONTEXT_TOKENS = 8192
MIN_CONTEXT_TOKENS = 1024
MAX_CONTEXT_TOKENS = 131072
BOOKEND_MIN_TOKENS = 2000

# Clinical concept synonyms so query routing ("Did the patient have a fever?")
# matches chart lines containing related terms ("Tmax 38.9", "febrile").
_CONCEPT_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"fever", "febrile", "afebrile", "temp", "temperature", "tmax", "chills", "rigors"}),
    frozenset({"antibiotic", "antibiotics", "abx", "infection", "culture", "cultures", "bcx", "ucx",
               "vancomycin", "vanc", "zosyn", "piperacillin", "cefepime", "ceftriaxone", "meropenem",
               "azithromycin", "metronidazole", "flagyl", "bactrim", "linezolid", "daptomycin"}),
    frozenset({"blood", "pressure", "bp", "sbp", "dbp", "map", "hypotension", "hypertension",
               "pressor", "pressors", "vasopressor", "norepinephrine", "levophed", "vasopressin",
               "phenylephrine", "epinephrine", "nicardipine", "clevidipine", "labetalol"}),
    frozenset({"vent", "ventilator", "intubated", "intubation", "extubated", "extubation", "abg", "vbg",
               "fio2", "peep", "spontaneous", "sbt", "trach", "tracheostomy", "bi-pap", "bipap", "hfnc",
               "oxygen", "spo2", "hypoxia", "respiratory"}),
    frozenset({"bleed", "bleeding", "hemorrhage", "hematoma", "ich", "sdh", "edh", "sah", "hgb",
               "hemoglobin", "plt", "platelets", "inr", "coags", "anticoagulation", "heparin",
               "enoxaparin", "lovenox", "warfarin", "coumadin", "apixaban", "eliquis", "rivaroxaban"}),
    frozenset({"neuro", "neurologic", "gcs", "nihss", "rass", "icp", "cpp", "evd", "pupil", "pupils",
               "seizure", "seizures", "eeg", "keppra", "levetiracetam", "weakness", "hemiparesis",
               "aphasia", "stroke", "infarct"}),
    frozenset({"renal", "kidney", "aki", "creatinine", "cr", "bun", "potassium", "hyperkalemia",
               "sodium", "hyponatremia", "hypernatremia", "urine", "uop", "dialysis", "hd", "crrt",
               "lasix", "furosemide"}),
    frozenset({"allergy", "allergies", "allg", "nkda", "code", "dnr", "dni", "comfort", "isolation"}),
)

_PRESET_KEYWORDS: dict[str, str] = {
    "clinical": "assessment plan medications labs vitals allergies code status",
    "brief": "assessment impression summary presenting history plan",
    "findings": "assessment findings impression abnormal labs imaging exam",
    "one_liner": "age history presenting problem admission diagnosis assessment",
    "problem_list": "assessment plan problem # active diagnosis",
    "handoff": "overnight events changed watch pending to-do code status allergies",
}

_SAFETY_OR_CRITICAL_RE = re.compile(
    r"\b(?:allerg(?:y|ies|ic)|ALLG|NKD?A|code status|full code|DNA?R|DNI|comfort (?:care|measures)|"
    r"isolation|contact precautions|droplet|airborne)\b",
    re.IGNORECASE,
)


@dataclass
class PackedContext:
    """Result of packing a chart into a target LLM token budget."""

    text: str
    tier: str               # "full" | "delta" | "routed"
    input_tokens: int
    packed_tokens: int
    budget_tokens: int
    context_window: int
    omitted_paragraphs: int = 0
    notes_count: int = 1

    @property
    def was_compacted(self) -> bool:
        return self.tier != "full"

    def tier_label(self) -> str:
        if self.tier == "full":
            return f"Context: full (~{self.packed_tokens:,} / {self.context_window:,} tok)"
        if self.tier == "delta":
            return (f"Context: delta-compacted (~{self.packed_tokens:,} / {self.context_window:,} tok "
                    f"from ~{self.input_tokens:,} tok — copied-forward repeats removed)")
        return (f"Context: routed (~{self.packed_tokens:,} / {self.context_window:,} tok "
                f"from ~{self.input_tokens:,} tok — {self.omitted_paragraphs} earlier paragraph(s) omitted)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "input_tokens": self.input_tokens,
            "packed_tokens": self.packed_tokens,
            "budget_tokens": self.budget_tokens,
            "context_window": self.context_window,
            "omitted_paragraphs": self.omitted_paragraphs,
            "notes_count": self.notes_count,
            "was_compacted": self.was_compacted,
        }


def should_bookend(chart: str, bookend: bool | None = None) -> bool:
    """Whether to repeat the core instruction/question at the end of the prompt."""
    if bookend is not None:
        return bool(bookend)
    return estimate_tokens(chart) >= BOOKEND_MIN_TOKENS


def resolve_context_tokens(opts: dict) -> int:
    """``local_llm.context_tokens`` from a merged config dict, defaulting when unset/invalid."""
    try:
        return int(opts.get("context_tokens") or DEFAULT_CONTEXT_TOKENS)
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_TOKENS


def _expand_query_tokens(query: str, preset: str) -> dict[str, float]:
    seed = f"{query} {_PRESET_KEYWORDS.get(preset, '')}".strip()
    base = _cite_tokens(seed)
    expanded = dict(base)
    for tok in list(base):
        for group in _CONCEPT_GROUPS:
            if tok in group:
                for syn in group:
                    expanded.setdefault(syn, 0.75)
    return expanded


def _verbatim_delta_chart(notes: list) -> str:
    """Keep the latest note in full and prune duplicate sentences from earlier notes.

    Unlike ``format_delta_timeline``, this helper emits zero synthetic headers or
    similarity percentages — every retained line exists verbatim in the source
    chart so grounding and citation checks against the original chart never
    encounter invented numbers.
    """
    if len(notes) <= 1:
        return notes[0].raw_text if notes else ""

    seen_units: set[str] = set()
    blocks: list[str] = []

    # Earlier notes (all except the latest): keep unique sentences in order,
    # always preserving the note's header line.
    for note in notes[:-1]:
        header = note.title.strip()
        kept_paras: list[str] = []
        for p in note.paragraphs:
            units = _units(p)
            fresh: list[str] = []
            for u in units:
                if u.strip() == header:
                    fresh.append(u)
                    continue
                norm_u = _norm(u)
                if norm_u not in seen_units or _SAFETY_OR_CRITICAL_RE.search(u):
                    fresh.append(u)
                    seen_units.add(norm_u)
            if fresh:
                sep = "\n" if "\n" in p else " "
                kept_paras.append(sep.join(fresh) if len(fresh) < len(units) else p)
        if kept_paras:
            body = "\n\n".join(kept_paras)
            if header and not body.startswith(header):
                body = f"{header}\n{body}"
            blocks.append(body)

    # Latest note: always kept in full at the end (recency anchor).
    blocks.append(notes[-1].raw_text.strip())
    return "\n\n".join(blocks)


def _route_paragraphs(
    notes: list,
    budget_tokens: int,
    query: str,
    preset: str,
) -> tuple[str, int]:
    """Select the highest-priority paragraphs within ``budget_tokens`` in chronological order."""
    want = _expand_query_tokens(query, preset)

    # Flatten into (note_idx, para_idx, is_latest_note, is_header_or_safety, text, tokens, score)
    items: list[dict[str, Any]] = []
    n_notes = len(notes)
    for n_idx, note in enumerate(notes):
        is_latest = (n_idx == n_notes - 1)
        header = note.title.strip()
        seen_norms: set[str] = set()
        for p_idx, p in enumerate(note.paragraphs):
            p_norm = _norm(p)
            if not is_latest and p_norm in seen_norms:
                continue
            seen_norms.add(p_norm)
            toks = estimate_tokens(p)
            is_safety = bool(_SAFETY_OR_CRITICAL_RE.search(p)) or (p_idx == 0 and header and p.startswith(header))
            have = _cite_tokens(p)
            overlap = sum(w for t, w in want.items() if t in have) if want else 0.0
            # Recency bonus + query overlap + safety bonus
            recency = (n_idx + 1) / max(n_notes, 1)
            score = (1000.0 if is_safety else 0.0) + (500.0 if is_latest else 0.0) + overlap * 10.0 + recency
            items.append({
                "key": (n_idx, p_idx),
                "is_latest": is_latest,
                "is_safety": is_safety,
                "text": p,
                "tokens": toks,
                "score": score,
            })

    if not items:
        return "", 0

    # Sort candidates by priority score descending (latest paragraphs kept from bottom up if single huge note)
    ranked = sorted(
        items,
        key=lambda it: (
            it["is_safety"],
            it["is_latest"],
            it["score"],
            it["key"][0],
            it["key"][1],
        ),
        reverse=True,
    )

    selected_keys: set[tuple[int, int]] = set()
    used_tokens = 0
    for it in ranked:
        if used_tokens + it["tokens"] <= budget_tokens or not selected_keys:
            selected_keys.add(it["key"])
            used_tokens += it["tokens"]

    omitted = len(items) - len(selected_keys)
    out_paras: list[str] = []
    for it in items:
        if it["key"] in selected_keys:
            out_paras.append(it["text"])

    return "\n\n".join(out_paras), max(0, omitted)


def pack_context(
    chart: str,
    *,
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    reserve_tokens: int = 900,
    budget_tokens: int | None = None,
    query: str = "",
    preset: str = "",
) -> PackedContext:
    """Pack ``chart`` into ``context_tokens - reserve_tokens`` (or explicit ``budget_tokens``) using the 3-tier strategy."""
    ctx_win = min(max(int(context_tokens or DEFAULT_CONTEXT_TOKENS), MIN_CONTEXT_TOKENS), MAX_CONTEXT_TOKENS)
    if budget_tokens is not None:
        budget = max(32, int(budget_tokens))
    else:
        budget = max(256, ctx_win - max(100, int(reserve_tokens)))

    wrapper = _WRAPPER.fullmatch(chart.strip())
    inner_chart = wrapper.group("body") if wrapper else chart
    tag = wrapper.group("tag") if wrapper else None

    input_tokens = estimate_tokens(chart)
    notes = _split_into_notes(inner_chart)
    n_notes = len(notes)

    # Tier 1: Full chart fits within budget
    if input_tokens <= budget:
        return PackedContext(
            text=chart,
            tier="full",
            input_tokens=input_tokens,
            packed_tokens=input_tokens,
            budget_tokens=budget,
            context_window=ctx_win,
            omitted_paragraphs=0,
            notes_count=n_notes,
        )

    # Tier 2: Multi-note verbatim delta compaction (latest note kept in full)
    if n_notes > 1:
        delta_body = _verbatim_delta_chart(notes)
        delta_text = f"<{tag}>\n{delta_body}\n</{tag}>" if tag else delta_body
        delta_tokens = estimate_tokens(delta_text)
        if delta_tokens <= budget:
            return PackedContext(
                text=delta_text,
                tier="delta",
                input_tokens=input_tokens,
                packed_tokens=delta_tokens,
                budget_tokens=budget,
                context_window=ctx_win,
                omitted_paragraphs=0,
                notes_count=n_notes,
            )
        # Use delta-pruned notes as the input to Tier 3 routing
        notes = _split_into_notes(delta_body)

    # Tier 3: Query & clinical-priority routed packing
    routed_body, omitted = _route_paragraphs(notes, budget, query, preset)
    routed_text = f"<{tag}>\n{routed_body}\n</{tag}>" if tag else routed_body
    routed_tokens = estimate_tokens(routed_text)
    return PackedContext(
        text=routed_text,
        tier="routed",
        input_tokens=input_tokens,
        packed_tokens=routed_tokens,
        budget_tokens=budget,
        context_window=ctx_win,
        omitted_paragraphs=omitted,
        notes_count=n_notes,
    )
