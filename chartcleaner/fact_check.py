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
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any

from .clinical_whitelist import _COMMON_MEDICATIONS
from .compactors.labs import LABS
from .compactors.vitals import VITALS

__all__ = ["DEFAULTS", "FactLoss", "FactReport", "FactTracker", "fact_counts",
           "extract_facts", "stage_category", "check", "MeaningChange", "meaning_changes",
           "UnsupportedFact", "OutputCheck", "verify_output"]

DEFAULTS: dict[str, Any] = {"enabled": True}
# Above this the per-stage snapshots cost seconds; the check is skipped with a warning.
MAX_CHARS = 1_000_000

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
    "rass", "cpot",
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


def _word_keys(text: str) -> list[tuple[str, str, int, int]]:
    out = []
    for m in _WORD.finditer(text):
        w = m.group(0).lower()
        if w in _KEYWORD_WORDS:
            out.append((f"kw:{_KEYWORD_WORDS[w]}", m.group(0), m.start(), m.end()))
        elif _is_drug(w):
            out.append((f"drug:{w}", m.group(0), m.start(), m.end()))
    for m in _KEYWORD_PHRASES.finditer(text):
        out.append(("kw:full code" if m.group(1) else "kw:comfort care", m.group(0), m.start(),
                    m.end()))
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


def _fact_spans(masked: str) -> list[tuple[str, str, int, int]]:
    """(key, display, start, end) for every fact in already-masked text."""
    spans: dict[int, tuple[str, str, int, int]] = {}  # number start -> fact (first match wins)
    for m in _BP.finditer(masked):
        for g in (1, 2):
            spans.setdefault(m.start(g), (f"n:{_norm_number(m.group(g))}", f"BP {m.group(0)}",
                                          m.start(), m.end()))
    for m in _LABELED_NUM.finditer(masked):
        label = _label_of(masked[max(0, m.start() - 48):m.start()])
        if label:
            spans.setdefault(m.start("num"), (f"n:{_norm_number(m.group('num'))}",
                                              f"{label} {m.group('num')}", m.start(), m.end()))
    for m in _WITH_UNIT.finditer(masked):
        spans.setdefault(m.start("num"), (f"n:{_norm_number(m.group('num'))}",
                                          f"{m.group('num')} {m.group('unit')}", m.start(),
                                          m.end()))
    out = [spans[k] for k in sorted(spans)]
    out.extend(_word_keys(masked))
    return out


def fact_counts(text: str) -> Counter:
    """How many times each fact key occurs — the cheap per-stage snapshot.

    Numbers count only in a clinical context, so dropping "Version 2 of 3"
    never looks like losing "Hunt-Hess 2"; values are compared by number, so
    "Sodium 141 mmol/L" → "Na 141" keeps the fact.
    """
    return Counter(key for key, _, _, _ in _fact_spans(_mask(text)))


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

    return [Fact(key, shown, line_of(start)) for key, shown, start, _end in _fact_spans(_mask(text))]


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
class MeaningChange:
    """A line a stage rewrote (not removed) that lost a negation or changed side."""
    kind: str            # "negation" | "laterality"
    stage_id: str
    stage_label: str
    category: str
    before: str
    after: str

    @property
    def message(self) -> str:
        if self.kind == "negation":
            return f"{self.stage_label} dropped a negation (no/denies/without…)"
        return f"{self.stage_label} changed left/right/bilateral"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "stage_id": self.stage_id, "stage_label": self.stage_label,
                "category": self.category, "before": self.before, "after": self.after,
                "message": self.message}


@dataclass
class FactReport:
    total: int
    losses: list[FactLoss]
    # rewritten lines that lost a negation or switched side (see meaning_changes)
    meaning: list[MeaningChange] = field(default_factory=list)
    # values in the output that were nowhere in the input (FactLoss rows, count = times)
    introduced: list[FactLoss] = field(default_factory=list)
    # impossible values / very high doses in the output (plausibility.Implausible)
    implausible: list[Any] = field(default_factory=list)
    # keys of implausible items that the input already had (not caused by cleaning)
    implausible_in_source: set = field(default_factory=set)

    def lost(self, category: str | None = None) -> int:
        return sum(x.count for x in self.losses if category in (None, x.category))

    @property
    def kept(self) -> int:
        return self.total - self.lost()

    def _meaning(self, category: str) -> int:
        return sum(1 for m in self.meaning if m.category == category)

    def _introduced(self, category: str) -> int:
        return sum(x.count for x in self.introduced if x.category == category)

    @property
    def implausible_new(self) -> list[Any]:
        """Impossible values the cleaning produced (the input didn't have them)."""
        return [x for x in self.implausible if x.key() not in self.implausible_in_source]

    @property
    def status(self) -> str:
        """``alert`` (unexpected loss/change), ``review`` (a rule removed facts) or ``ok``."""
        if (self.lost("unexpected") or self._meaning("unexpected")
                or self._introduced("unexpected") or self.implausible_new):
            return "alert"
        if self.lost("rule") or self._meaning("rule"):
            return "review"
        return "ok"

    def headline(self) -> str:
        if self.status == "alert":
            parts = []
            if self.lost("unexpected"):
                parts.append(f"{self.lost('unexpected')} clinical value(s) lost unexpectedly")
            if self._meaning("unexpected"):
                parts.append(f"{self._meaning('unexpected')} line(s) changed meaning")
            if self._introduced("unexpected"):
                parts.append(f"{self._introduced('unexpected')} value(s) appeared that weren't in the chart")
            if self.implausible_new:
                parts.append(f"{len(self.implausible_new)} impossible value(s) after cleaning")
            text = "; ".join(parts)
        elif self.status == "review":
            parts = []
            if self.lost("rule"):
                parts.append(f"{self.lost('rule')} clinical value(s) removed by rules")
            if self._meaning("rule"):
                parts.append(f"{self._meaning('rule')} line(s) changed meaning by rules")
            text = "; ".join(parts) + " — review"
        else:
            text = f"All {self.total - self.lost('by_design')} clinical value(s) kept"
        by_design = self.lost("by_design")
        if by_design:
            text += f" ({by_design} dropped by section/summary settings)"
        old = len(self.implausible) - len(self.implausible_new)
        if old:
            text += f" · {old} value(s) in the chart look impossible"
        return text

    def summary(self) -> dict:
        """Counts only — no chart text — safe for run history."""
        by_stage: dict[str, int] = {}
        for x in self.losses:
            if x.category != "by_design":
                by_stage[x.stage_id] = by_stage.get(x.stage_id, 0) + x.count
        return {"status": self.status, "total": self.total, "kept": self.kept,
                **{f"lost_{c}": self.lost(c) for c in CATEGORY_ORDER},
                "lost_by_stage": by_stage,
                "meaning_flags": len(self.meaning),
                "introduced": sum(x.count for x in self.introduced),
                "implausible": len(self.implausible)}

    def to_dict(self) -> dict:
        return {**self.summary(), "headline": self.headline(),
                "losses": [x.to_dict() for x in self.losses],
                "meaning": [m.to_dict() for m in self.meaning],
                "introduced_values": [x.to_dict() for x in self.introduced],
                "implausible_values": [{**x.to_dict(), "in_source": x.key() in self.implausible_in_source}
                                       for x in self.implausible]}


# --- meaning guard: negation and laterality on rewritten lines -------------

_NEGATION = re.compile(
    r"(?<![A-Za-z])(?:no|not|denies|denied|deny|without|negative|neg|absent|never|none|nor)"
    r"(?![A-Za-z])|(?<![A-Za-z])w/o(?![A-Za-z])|n't\b"
    r"|(?<![A-Za-z])(?-i:WO|NEG)(?![A-Za-z])|\(-\)|(?<![A-Za-z])-ve\b", re.IGNORECASE)
_SIDE = re.compile(
    r"(?<![A-Za-z])(?:(left|lt)|(right|rt)|(bilateral|bilat|both))(?![A-Za-z])"
    r"|(?<![A-Za-z/])(?:(L)|(R))(?![A-Za-z/])|(?<![A-Za-z])(b/l)(?![A-Za-z])", re.IGNORECASE)
# Single-letter sides only count in capitals ("L MCA", not "r/o").
MAX_MEANING_LINES = 4000


def _sides(line: str) -> Counter:
    out: Counter = Counter()
    for m in _SIDE.finditer(line):
        if m.group(1):
            out["L"] += 1
        elif m.group(2):
            out["R"] += 1
        elif m.group(3) or m.group(6):
            out["B"] += 1
        elif m.group(4) and m.group(4) == "L":
            out["L"] += 1
        elif m.group(5) and m.group(5) == "R":
            out["R"] += 1
    return out


@lru_cache(maxsize=1)
def _negation_abbreviations() -> re.Pattern | None:
    """Abbreviations that stand for a negated phrase (NAD, DNR, NGTD, N/A…).

    Abbreviating "No acute distress" to "NAD" keeps the negation; counting
    these forms as negations lets the guard see that.
    """
    import csv
    from .abbreviations import SOURCE_PATH
    forms = set()
    try:
        with SOURCE_PATH.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                abbr = (row.get("Abbreviation") or "").strip()
                if len(abbr) >= 2 and _NEGATION.search(row.get("Expanded version") or ""):
                    forms.add(abbr)
    except OSError:
        return None
    if not forms:
        return None
    alternation = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alternation})(?![A-Za-z0-9])")


def _negations(line: str) -> int:
    n = len(_NEGATION.findall(line))
    abbrs = _negation_abbreviations()
    if abbrs is not None:
        n += sum(1 for m in abbrs.finditer(line) if not _NEGATION.fullmatch(m.group(0)))
    return n


_MAX_PAIR_WORK = 4000  # fuzzy pairing budget per replaced block (old lines × new lines)


def _surplus(lines: list[str], other: list[str]) -> list[str]:
    """Lines of ``lines`` that ``other`` has fewer copies of (counted, in order).

    Counting copies (not just presence) catches a rewrite of one instance of a
    line that copy-forward repeated elsewhere.
    """
    remaining = Counter(other)
    out = []
    for ln in lines:
        if remaining.get(ln, 0) > 0:
            remaining[ln] -= 1
        elif ln.strip():
            out.append(ln)
    return out


def _pairs(before: list[str], after: list[str]) -> list[tuple[str, str]]:
    """(old, new) line pairs for lines a stage rewrote.

    Lines present verbatim on both sides can't have changed meaning, so only
    the changed lines are aligned — this keeps a 4,000-line chart full of
    copy-forward repeats fast (aligning everything is quadratic there).
    """
    import difflib
    olds = _surplus(before, after)
    news = _surplus(after, before)
    if not olds or not news:
        return []
    if not any(_negations(ln) or _sides(ln) for ln in olds):
        return []  # nothing a rewrite could have flipped
    out: list[tuple[str, str]] = []
    sm = difflib.SequenceMatcher(a=olds, b=news, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        block_old, block_new = olds[i1:i2], news[j1:j2]
        if len(block_old) == len(block_new):
            out.extend(zip(block_old, block_new))
            continue
        if len(block_old) * len(block_new) > _MAX_PAIR_WORK:
            continue
        for old in block_old:
            if not (_negations(old) or _sides(old)):
                continue
            best, score = None, 0.5
            for new in block_new:
                r = difflib.SequenceMatcher(a=old, b=new, autojunk=False).ratio()
                if r > score:
                    best, score = new, r
            if best is not None:
                out.append((old, best))
    return out


def meaning_changes(before: str, after: str, stage_id: str = "output",
                    stage_label: str = "Output") -> list[MeaningChange]:
    """Rewritten lines whose negations dropped or whose sides changed.

    Only lines a stage *rewrote* are compared (removed lines are the facts
    check's job), so "No fever" → "fever" is caught while deleting a whole
    boilerplate line is not. Abbreviated sides (left → L) count as the same side.
    """
    a, b = before.split("\n"), after.split("\n")
    if len(a) > MAX_MEANING_LINES or len(b) > MAX_MEANING_LINES:
        return []
    category = stage_category(stage_id)
    out = []
    for old, new in _pairs(a, b):
        if not old.strip() or not new.strip():
            continue
        before_n, after_n = _negations(old), _negations(new)
        # Abbreviating can fold two negations into one form ("No acute process /
        # no acute pathology" → NAP); there only a vanished negation counts.
        lost = (before_n and not after_n) if stage_id in WORD_SAFE_STAGES else after_n < before_n
        if lost:
            out.append(MeaningChange("negation", stage_id, stage_label, category,
                                     old.strip()[:300], new.strip()[:300]))
        so, sn = _sides(old), _sides(new)
        if so != sn and (sn - so):  # a side appeared that wasn't there (a switch, not a drop)
            out.append(MeaningChange("laterality", stage_id, stage_label, category,
                                     old.strip()[:300], new.strip()[:300]))
        elif so and not sn and new.strip():
            out.append(MeaningChange("laterality", stage_id, stage_label, category,
                                     old.strip()[:300], new.strip()[:300]))
    return out


# --- AI output check: facts the source never had ---------------------------

@dataclass
class UnsupportedFact:
    key: str
    display: str
    start: int
    end: int

    def to_dict(self) -> dict:
        return {"key": self.key, "display": self.display, "start": self.start, "end": self.end}


@dataclass
class OutputCheck:
    """Clinical facts in generated text (AI summary, answer, compactor output)
    checked against the source chart."""
    total: int
    unsupported: list[UnsupportedFact]

    @property
    def ok(self) -> bool:
        return not self.unsupported

    @property
    def score(self) -> float:
        if not self.total:
            return 100.0
        return round(100.0 * (self.total - len(self.unsupported)) / self.total, 1)

    def headline(self) -> str:
        if not self.total:
            return "No clinical values to check"
        if self.ok:
            return f"All {self.total} clinical value(s) are in the chart"
        shown = ", ".join(dict.fromkeys(u.display for u in self.unsupported))
        return f"{len(self.unsupported)} value(s) not in the chart: {shown}"

    def to_dict(self) -> dict:
        return {"total": self.total, "ok": self.ok, "score": self.score,
                "headline": self.headline(),
                "unsupported": [u.to_dict() for u in self.unsupported]}


_ANY_NUMBER = re.compile(r"(?<![\w.])[<>]?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def verify_output(source: str, generated: str) -> OutputCheck:
    """Which clinical facts in ``generated`` the ``source`` chart never states.

    Numbers count as supported when the same value appears anywhere in the
    source (the label may be worded differently); drug names and safety
    keywords when the source has them. Spans index into ``generated`` so the
    app can highlight them.
    """
    numbers = {_norm_number(m.group(1)) for m in _ANY_NUMBER.finditer(source)}
    words = {m.group(0).lower() for m in _WORD.finditer(source)}
    source_keys = set(fact_counts(source))
    spans = _fact_spans(_mask(generated))
    unsupported = []
    for key, display, start, end in spans:
        if key.startswith("n:"):
            ok = key[2:] in numbers
        elif key.startswith("drug:"):
            ok = key[5:] in words
        else:
            ok = key in source_keys
        if not ok:
            unsupported.append(UnsupportedFact(key, display, start, end))
    return OutputCheck(total=len(spans), unsupported=unsupported)


class FactTracker:
    """Records per-stage fact counts while the pipeline runs."""

    def __init__(self, text: str):
        self.text = text
        self.facts = extract_facts(text)
        self.start = fact_counts(text)
        self.current = self.start
        self.previous = text
        self.drops: list[tuple[str, str, Counter]] = []  # (stage id, label, decreases)
        self.meaning: list[MeaningChange] = []
        self.added: list[tuple[str, str, Counter]] = []  # values new to the chart, per stage

    def after_stage(self, stage_id: str, label: str, text: str) -> None:
        now = fact_counts(text)
        dec = Counter({k: v - now.get(k, 0) for k, v in self.current.items() if v > now.get(k, 0)})
        if dec:
            self.drops.append((stage_id, label, dec))
        new = Counter({k: v - self.current.get(k, 0) for k, v in now.items()
                       if k.startswith("n:") and k not in self.start and v > self.current.get(k, 0)})
        if new:
            self.added.append((stage_id, label, new))
        if stage_id not in BY_DESIGN_STAGES and stage_id not in DEDUP_STAGES:
            try:
                self.meaning.extend(meaning_changes(self.previous, text, stage_id, label))
            except Exception:
                pass  # the guard must never break a clean
        self.current = now
        self.previous = text

    def report(self, final_text: str) -> FactReport:
        rep = check(self.text, final_text, self.drops, facts=self.facts, start=self.start)
        rep.meaning = self.meaning
        rep.introduced = self._introduced(final_text)
        self._plausibility(rep, final_text)
        return rep

    def _introduced(self, final_text: str) -> list[FactLoss]:
        if not self.added:
            return []
        final = fact_counts(final_text)
        facts = extract_facts(final_text)
        out = []
        for stage_id, label, new in self.added:
            for key, n in new.items():
                if not final.get(key):
                    continue  # a later stage removed it again
                shown = next((f for f in facts if f.key == key), None)
                out.append(FactLoss(key, shown.display if shown else key[2:], n, stage_id, label,
                                    stage_category(stage_id),
                                    lines=[shown.line] if shown else []))
        return out

    def _plausibility(self, rep: FactReport, final_text: str) -> None:
        from .plausibility import check as plausible
        try:
            rep.implausible = plausible(final_text)
            if rep.implausible:
                rep.implausible_in_source = {x.key() for x in plausible(self.text)}
        except Exception:
            pass


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
