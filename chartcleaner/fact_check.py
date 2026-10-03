""""Nothing clinical lost" check: compare clinical facts before and after a clean.

A *fact* is something a clinician would not want silently dropped:

* a number with a unit (``40 mg``, ``98%``, ``10 cm H2O``, ``36.8 C``)
* a number after a known lab, vital or score label (``Na 141``, ``GCS 14``)
* a blood pressure (``138/76``)
* a medication name from the clinical whitelist (``nimodipine``)
* a safety keyword (allergy, NKDA, DNR/DNI, full code, comfort care)

The pipeline snapshots fact counts before and after every stage, so each lost
fact is attributed to the stage that removed it. Numbers are compared by
value, not by wording, so a compactor turning ``Sodium 141 mmol/L`` into
``Na 141`` keeps the fact. Each loss gets a category:

``unexpected``
    removed by a stage that should only reformat (whitespace, bullets,
    headers, abbreviations, …) — always worth a look.
``rule``
    removed by a removal rule (boilerplate, metadata, learned, custom,
    PHI). Usually right, but a rule that eats a lab value is a bad rule.
``by_design``
    removed by a setting whose job is dropping content (section filter,
    imaging impression, vitals summary, latest-only labs). Listed, never
    alarming. Duplicate-note folding only counts when the value disappears
    from the output entirely.

The report carries chart text (context lines) and therefore stays in memory;
:meth:`FactReport.summary` is the text-free part that history may keep.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .clinical_whitelist import _COMMON_MEDICATIONS
from .compactors.labs import LABS
from .compactors.vitals import VITALS

__all__ = ["DEFAULTS", "FactLoss", "FactReport", "FactTracker", "fact_counts",
           "extract_facts", "stage_category", "check"]

DEFAULTS: dict[str, Any] = {"enabled": True}

# Stage → category for losses it causes (see the module docstring).
RULE_STAGES = frozenset({
    "metadata_lines", "boilerplate", "learned_rules", "literal_replacements",
    "phi_patterns", "clinical_identifiers", "nlp_redaction", "tokenize_phi", "timestamps",
})
BY_DESIGN_STAGES = frozenset({
    "sections", "imaging_impression", "vitals_summary", "lab_compaction", "neuro_summary",
})
DEDUP_STAGES = frozenset({"duplicate_notes", "fuzzy_dedup"})
# Abbreviating swaps a drug or keyword for a standard short form on purpose.
WORD_SAFE_STAGES = frozenset({"medical_abbreviations", "expand_abbreviations"})

CATEGORY_ORDER = ("unexpected", "rule", "by_design")


def stage_category(stage_id: str) -> str:
    if stage_id.startswith("custom:") or stage_id in RULE_STAGES:
        return "rule"
    if stage_id in BY_DESIGN_STAGES or stage_id in DEDUP_STAGES:
        return "by_design"
    return "unexpected"


# --- extraction ------------------------------------------------------------

# Spans that look numeric but are never facts: dates, clock times, tokens.
_MASKS = [
    re.compile(r"\[\[[A-Za-z_]+\d+\]\]"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"(?<![\d/.])(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])(?:/\d{2,4})?(?![\d/])"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
]
_BP = re.compile(r"(?<![\d/.])(\d{2,3})/(\d{2,3})(?![\d/])")
_UNITS = (
    r"mcg|mg|g|kg|ng|pg|lbs?|mL|ml|L|units?|mEq|mmol|mOsm|mm\s?Hg|cm\s?H2O|"
    r"%|°\s?[CF]|[CF](?![A-Za-z])|bpm|cm|mm|IU|K/u[Ll]|K/µ[Ll]|x\s?10"
)
_WITH_UNIT = re.compile(
    rf"(?<![\w.])(?P<num>[<>]?\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|[<>]?\d+(?:\.\d+)?)\s?(?P<unit>(?:{_UNITS})(?:/[A-Za-z0-9.]+)*)(?![A-Za-z0-9])")

_EXTRA_LABELS = (
    "gcs", "nihss", "mrs", "hunt-hess", "hunt hess", "fisher", "icp", "cpp", "evd", "fio2", "peep",
    "a1c", "hba1c", "bnp", "ldl", "tsh", "ck", "lipase", "ammonia", "ph", "pco2", "po2",
    "glucose", "poc glucose", "weight", "wt", "uop", "osm", "serum osm", "vent rate", "tv",
)
_LABELS = frozenset(
    {s for _, (_, names) in LABS.items() for s in names} | set(VITALS) | set(_EXTRA_LABELS))
_NUM = r"[<>]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[<>]?\d+(?:\.\d+)?"
# Numbers are found first; the label is read from the words just before each
# one in Python (a 150-way regex alternation is several times slower).
_LABELED_NUM = re.compile(rf"(?<![\w.])(?P<num>{_NUM})(?![\d/])")
_SEGMENT_BREAK = re.compile(r"[^A-Za-z0-9\- \t]")
_CONNECTORS = frozenset({"of", "is", "was"})

# Drop the whitelist's human-name lookalikes; they'd count real names.
_DRUGS = frozenset(d for d in _COMMON_MEDICATIONS if " " not in d
                   and d not in {"tamar", "grace", "jordan", "victoria", "charlotte", "haven"})
# Generic-name stems catch drugs the whitelist doesn't list (nimodipine, …).
_DRUG_STEMS = ("dipine", "olol", "pril", "sartan", "statin", "mycin", "micin", "cillin",
               "conazole", "prazole", "parin", "xaban", "floxacin", "cycline", "semide", "tidine",
               "azepam", "zolam", "setron", "tiapine", "adone", "barbital", "racetam",
               "triptyline", "oxetine", "afil", "gliptin", "gliflozin", "glutide", "navir",
               "clovir", "mab", "nib", "platin", "rubicin", "taxel")
_WORD = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z-]{2,}(?![A-Za-z])")
_KEYWORD_WORDS = {"allergy": "allergy", "allergies": "allergy", "allergic": "allergy",
                  "nkda": "NKDA", "dnr": "DNR", "dni": "DNI"}
_KEYWORD_PHRASES = re.compile(r"(?<![A-Za-z])(?:(full\s+code)|(comfort\s+(?:care|measures)))(?![A-Za-z])",
                              re.IGNORECASE)


def _norm_number(raw: str) -> str:
    s = raw.lstrip("<>").replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    return s


def _mask(text: str) -> str:
    for rx in _MASKS:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _is_drug(word: str) -> bool:
    return word in _DRUGS or (len(word) >= 7 and word.endswith(_DRUG_STEMS))


def _word_keys(text: str) -> list[tuple[str, str, int]]:
    out = []
    for m in _WORD.finditer(text):
        w = m.group(0).lower()
        if w in _KEYWORD_WORDS:
            out.append((f"kw:{_KEYWORD_WORDS[w]}", m.group(0), m.start()))
        elif _is_drug(w):
            out.append((f"drug:{w}", m.group(0), m.start()))
    for m in _KEYWORD_PHRASES.finditer(text):
        out.append(("kw:full code" if m.group(1) else "kw:comfort care", m.group(0), m.start()))
    return out


def _label_of(before: str) -> str | None:
    """The lab/vital/score label ending ``before`` (the text up to a number)."""
    before = before.rstrip(" \t").rstrip(":=#").rstrip(" \t")
    if not before or not before[-1].isalpha() and not before[-1].isdigit():
        return None
    parts = _SEGMENT_BREAK.split(before)[-1].split()[-5:]
    if parts and parts[-1].lower() in _CONNECTORS:
        parts.pop()
    for n in (4, 3, 2, 1):
        if len(parts) >= n:
            cand = " ".join(parts[-n:])
            if cand.lower() in _LABELS:
                return cand
    return None


def _fact_spans(masked: str) -> list[tuple[str, str, int]]:
    """(key, display, start) for every fact in already-masked text."""
    spans: dict[int, tuple[str, str, int]] = {}  # number start -> fact (first match wins)
    for m in _BP.finditer(masked):
        for g in (1, 2):
            spans.setdefault(m.start(g), (f"n:{_norm_number(m.group(g))}", f"BP {m.group(0)}",
                                          m.start()))
    for m in _LABELED_NUM.finditer(masked):
        label = _label_of(masked[max(0, m.start() - 48):m.start()])
        if label:
            spans.setdefault(m.start("num"), (f"n:{_norm_number(m.group('num'))}",
                                              f"{label} {m.group('num')}", m.start()))
    for m in _WITH_UNIT.finditer(masked):
        spans.setdefault(m.start("num"), (f"n:{_norm_number(m.group('num'))}",
                                          f"{m.group('num')} {m.group('unit')}", m.start()))
    out = [spans[k] for k in sorted(spans)]
    out.extend(_word_keys(masked))
    return out


def fact_counts(text: str) -> Counter:
    """How many times each fact key occurs — the cheap per-stage snapshot.

    Numbers count only in a clinical context, so dropping "Version 2 of 3"
    never looks like losing "Hunt-Hess 2"; values are compared by number, so
    "Sodium 141 mmol/L" → "Na 141" keeps the fact.
    """
    return Counter(key for key, _, _ in _fact_spans(_mask(text)))


@dataclass
class Fact:
    key: str
    display: str
    line: str


def extract_facts(text: str) -> list[Fact]:
    """The clinical facts in ``text`` with the line each one sits on."""
    starts: list[int] = []
    pos = 0
    for line in text.split("\n"):
        starts.append(pos)
        pos += len(line) + 1

    def line_of(index: int) -> str:
        i = bisect.bisect_right(starts, index) - 1
        end = text.find("\n", starts[i])
        return text[starts[i]:end if end != -1 else len(text)].strip()

    return [Fact(key, shown, line_of(start)) for key, shown, start in _fact_spans(_mask(text))]


# --- report ----------------------------------------------------------------

@dataclass
class FactLoss:
    key: str
    display: str
    count: int
    stage_id: str
    stage_label: str
    category: str
    lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"key": self.key, "display": self.display, "count": self.count,
                "stage_id": self.stage_id, "stage_label": self.stage_label,
                "category": self.category, "lines": list(self.lines)}


@dataclass
class FactReport:
    total: int
    losses: list[FactLoss]

    def lost(self, category: str | None = None) -> int:
        return sum(x.count for x in self.losses if category in (None, x.category))

    @property
    def kept(self) -> int:
        return self.total - self.lost()

    @property
    def status(self) -> str:
        """``alert`` (unexpected loss), ``review`` (a rule removed facts) or ``ok``."""
        if self.lost("unexpected"):
            return "alert"
        if self.lost("rule"):
            return "review"
        return "ok"

    def headline(self) -> str:
        if self.status == "alert":
            text = f"{self.lost('unexpected')} clinical value(s) lost unexpectedly"
        elif self.status == "review":
            text = f"{self.lost('rule')} clinical value(s) removed by rules — review"
        else:
            text = f"All {self.total - self.lost('by_design')} clinical value(s) kept"
        by_design = self.lost("by_design")
        if by_design:
            text += f" ({by_design} dropped by section/summary settings)"
        return text

    def summary(self) -> dict:
        """Counts only — no chart text — safe for run history."""
        return {"status": self.status, "total": self.total, "kept": self.kept,
                **{f"lost_{c}": self.lost(c) for c in CATEGORY_ORDER}}

    def to_dict(self) -> dict:
        return {**self.summary(), "headline": self.headline(),
                "losses": [x.to_dict() for x in self.losses]}


class FactTracker:
    """Records per-stage fact counts while the pipeline runs."""

    def __init__(self, text: str):
        self.text = text
        self.facts = extract_facts(text)
        self.start = fact_counts(text)
        self.current = self.start
        self.drops: list[tuple[str, str, Counter]] = []  # (stage id, label, decreases)

    def after_stage(self, stage_id: str, label: str, text: str) -> None:
        now = fact_counts(text)
        dec = Counter({k: v - now.get(k, 0) for k, v in self.current.items() if v > now.get(k, 0)})
        if dec:
            self.drops.append((stage_id, label, dec))
        self.current = now

    def report(self, final_text: str) -> FactReport:
        return check(self.text, final_text, self.drops, facts=self.facts, start=self.start)


def check(before: str, after: str, drops: list[tuple[str, str, Counter]] | None = None, *,
          facts: list[Fact] | None = None, start: Counter | None = None) -> FactReport:
    """Compare facts in ``before`` and ``after``.

    ``drops`` is the per-stage decrease list from :class:`FactTracker`; without
    it every loss is attributed to a pseudo-stage ``"output"`` (unexpected).
    """
    facts = extract_facts(before) if facts is None else facts
    start = fact_counts(before) if start is None else start
    end = fact_counts(after)
    after_lines = {ln.strip() for ln in after.split("\n")}
    by_key: dict[str, list[Fact]] = {}
    for f in facts:
        by_key.setdefault(f.key, []).append(f)

    drops = drops if drops is not None else [("output", "Output", Counter(start) - end)]
    losses: list[FactLoss] = []
    for key, items in by_key.items():
        lost = min(len(items), max(0, start.get(key, 0) - end.get(key, 0)))
        if not lost:
            continue
        # Prefer context lines that no longer appear verbatim in the output.
        gone = [f for f in items if f.line not in after_lines] or items
        remaining = lost
        for stage_id, label, dec in drops:
            take = min(remaining, dec.get(key, 0))
            if not take:
                continue
            remaining -= take
            category = stage_category(stage_id)
            if stage_id in DEDUP_STAGES and end.get(key, 0):
                continue  # folded duplicate; the value is still in the output
            if stage_id in WORD_SAFE_STAGES and not key.startswith("n:"):
                continue
            losses.append(FactLoss(key, gone[0].display, take, stage_id, label, category,
                                   lines=[f.line for f in gone[:3]]))
            if not remaining:
                break
        if remaining:  # lost after the last stage snapshot (e.g. the wrapper)
            losses.append(FactLoss(key, gone[0].display, remaining, "output", "Output",
                                   "unexpected", lines=[f.line for f in gone[:3]]))
    losses.sort(key=lambda x: (CATEGORY_ORDER.index(x.category), x.stage_label, x.display))
    return FactReport(total=len(facts), losses=losses)
