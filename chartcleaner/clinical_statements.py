"""Deterministic clinical statements with source spans.

A :class:`ClinicalStatement` is one recognized relationship in the chart —
a medication tuple (drug, dose, unit, route, frequency, status), a labeled
measurement (``Na 141`` on ``03/14``), or a finding with its polarity,
certainty, temporality, experiencer and laterality — together with the exact
source span it came from. Every clinical attribute may be unknown; nothing
is inferred. Parsing is a declared set of patterns drawn from the existing
abbreviation dictionary, lab/vital labels and date helpers: a sentence that
carries clinical signal (a negation, an uncertainty cue, a history cue) but
does not parse is recorded as an *unresolved* span, never silently skipped.

Statements are compared by :mod:`statement_compare`. Approved abbreviations
are the same statement because segments are expanded through the bundled
dictionary before parsing (``SOB`` → ``shortness of breath``, ``NAD`` →
``no acute distress``), so the negation inside ``NAD`` is seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import fact_check as fc
from .compactors.labs import LABS
from .compactors.vitals import VITALS
from .edit_provenance import SourceSpan, merge_spans

__all__ = [
    "CERTAINTY_RANK", "KINDS", "NON_ACTIVE_STATUSES", "ClinicalStatement", "StatementExtraction",
    "StatementIndex", "canonical_subject", "equivalent", "extract_statements", "normalize_date",
    "signature",
]

KINDS = ("medication", "measurement", "finding")
CERTAINTY_RANK = {"possible": 0, "stated": 1, "confirmed": 2}
NON_ACTIVE_STATUSES = frozenset({"held", "discontinued"})


@dataclass(frozen=True)
class ClinicalStatement:
    kind: str
    subject: str
    value: str | None = None
    unit: str | None = None
    polarity: str | None = None          # "positive" | "negative"
    certainty: str | None = None         # "possible" | "stated" | "confirmed"
    temporality: str | None = None       # "MM/DD" / "YYYY-MM-DD" / "current" / "prior" / "past" / "day:N"
    experiencer: str | None = None       # "patient" | "family"
    laterality: str | None = None        # "L" | "R" | "B"
    route: str | None = None
    frequency: str | None = None
    medication_status: str | None = None  # held | discontinued | started | continued | increased | decreased
    note_id: str = ""
    section: str = ""
    source_spans: tuple[SourceSpan, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.subject

    def to_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject, "value": self.value, "unit": self.unit,
                "polarity": self.polarity, "certainty": self.certainty, "temporality": self.temporality,
                "experiencer": self.experiencer, "laterality": self.laterality, "route": self.route,
                "frequency": self.frequency, "medication_status": self.medication_status,
                "note_id": self.note_id, "section": self.section,
                "source_spans": [list(s) for s in self.source_spans]}


@dataclass(frozen=True)
class StatementExtraction:
    statements: tuple[ClinicalStatement, ...]
    unresolved_spans: tuple[SourceSpan, ...]

    def by_key(self) -> dict[tuple[str, str], list[ClinicalStatement]]:
        out: dict[tuple[str, str], list[ClinicalStatement]] = {}
        for s in self.statements:
            out.setdefault(s.key, []).append(s)
        return out

    @property
    def spans(self) -> tuple[SourceSpan, ...]:
        return merge_spans(span for s in self.statements for span in s.source_spans)


# --- abbreviation expansion --------------------------------------------------------

# Slash forms and a few staples the dictionary may spell differently.
_FALLBACK_EXPANSIONS = {
    "w/o": "without", "h/o": "history of", "hx": "history", "r/o": "rule out", "s/p": "status post",
    "b/l": "bilateral", "bilat": "bilateral", "c/o": "complains of", "nad": "no acute distress",
    "sob": "shortness of breath", "cp": "chest pain", "pod": "postoperative day", "hd": "hospital day",
    "prn": "as needed", "fh": "family history", "nkda": "no known drug allergies", "y/o": "year-old",
    "yo": "year-old", "tmrw": "tomorrow", "reg": "regimen",
}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z/'-]*")


@lru_cache(maxsize=1)
def _expansions() -> dict[str, str]:
    """Abbreviation → lower-case expansion for unambiguous dictionary entries."""
    from .abbreviations import _ambiguous_abbreviations, _expandable, _source_rows, display_term
    try:
        rows = _source_rows()
        ambiguous = _ambiguous_abbreviations()
    except OSError:
        rows, ambiguous = [], frozenset()
    negated = fc._negation_abbreviations()
    out: dict[str, str] = {}
    for row in rows:
        abbr, term = (row.get("Abbreviation") or "").strip(), (row.get("Expanded version") or "").strip()
        if not abbr or not term or "not expanded in source" in term.casefold() or abbr in out:
            continue
        if not _expandable(abbr):
            continue
        is_negation = negated is not None and negated.fullmatch(abbr) is not None
        if abbr in ambiguous and not is_negation:
            continue
        out[abbr] = display_term(term).lower()
    for k, v in _FALLBACK_EXPANSIONS.items():
        out.setdefault(k, v)
        out.setdefault(k.upper(), v)
    return out


def _expand(text: str) -> str:
    table = _expansions()

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        found = table.get(tok)
        if found is None and ("/" in tok or tok.islower()):
            found = table.get(tok.lower())
            # "mca" is MCA; two-letter words ("mg", "cp") stay as written —
            # upper-casing them would invent a dictionary meaning.
            if found is None and tok.islower() and len(tok) >= 3:
                found = table.get(tok.upper())
        return found if found is not None else tok

    return _TOKEN.sub(repl, text)


_ARTICLES = re.compile(r"\b(?:the|a|an|is|are|was|were|has|have|had|with|w|w/|for|patient|pt)\b")
_AGE = re.compile(r"\b\d{1,3}\s*[- ]?\s*(?:year[- ]old|years[- ]old|y/?o|yo|yrs?)\b", re.IGNORECASE)
_REDACTION = re.compile(r"\[REDACTED_[A-Z_]+\]|\[\[[A-Za-z_]+\d+\]\]")


def _mask_tokens(text: str) -> str:
    """Blank redaction/tokenization placeholders (same length, so spans hold)."""
    return _REDACTION.sub(lambda m: " " * len(m.group(0)), text) if "[" in text else text
_PUNCT = re.compile(r"[^\w\s/.-]")
_WS = re.compile(r"\s+")


def canonical_subject(text: str) -> str:
    """Lower-case, dictionary-expanded, article-free form used for equivalence."""
    text = _expand(text)
    text = _PUNCT.sub(" ", text.lower())
    text = _ARTICLES.sub(" ", text)
    text = _WS.sub(" ", text).strip(" .-")
    # "of" is structural inside a phrase (shortness of breath) but a dangling
    # one left by a stripped cue ("family history of stroke" → "of stroke") is not.
    return re.sub(r"^(?:of\s+)+|(?:\s+of)+$", "", text)


# --- cues ---------------------------------------------------------------------

_NEG = re.compile(r"(?<![A-Za-z])(?:no|not|denies|denied|deny|without|negative for|neg for|absent|"
                  r"none|never|free of|nonreactive|non-reactive)(?![A-Za-z])|\(-\)|(?<![A-Za-z])-ve\b",
                  re.IGNORECASE)
_POSSIBLE = re.compile(r"(?<![A-Za-z])(?:possible|possibly|probable|probably|likely|suspected|suspect|"
                       r"suspicious for|concern for|concerning for|rule out|cannot (?:exclude|rule out)|"
                       r"can't (?:exclude|rule out)|questionable|presumed|versus|vs\.?)(?![A-Za-z])|\?",
                       re.IGNORECASE)
_CONFIRMED = re.compile(r"(?<![A-Za-z])(?:confirmed|definite|definitive|diagnosed)(?![A-Za-z])", re.IGNORECASE)
_FAMILY = re.compile(r"(?<![A-Za-z])(?:family history|mother|father|brother|sister|sibling|parents?|"
                     r"maternal|paternal|grandmother|grandfather)(?![A-Za-z])", re.IGNORECASE)
_PAST = re.compile(r"(?<![A-Za-z])(?:history of|prior|previous|previously|remote|old|status post|resolved|"
                   r"in (?:19|20)\d{2})(?![A-Za-z])|(?<![\d/])(?:19|20)\d{2}(?![\d/])", re.IGNORECASE)
_CURRENT = re.compile(r"(?<![A-Za-z])(?:today|currently|now|this morning|this am|tonight)(?![A-Za-z])",
                      re.IGNORECASE)
_PRIOR = re.compile(r"(?<![A-Za-z])(?:yesterday|last night|overnight)(?![A-Za-z])", re.IGNORECASE)
_SIDE_WORDS = re.compile(r"(?<![\w/])(?:(left|lt)|(right|rt)|(bilateral|bilat|both))(?![\w/])", re.IGNORECASE)
_SIDE_LETTER = re.compile(r"(?<![\w/])(?:(L)|(R))(?![\w/])")
_DATE = re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?(?![\d/])|"
                   r"(?P<iso>\d{4}-\d{2}-\d{2})")
_DAY = re.compile(r"(?<![A-Za-z])(?:HD|POD|hospital day|postoperative day|post-op day|post-operative day|day)"
                  r"\s*#?\s*(\d{1,3})(?![\d])", re.IGNORECASE)
_LABEL_PREFIX = re.compile(r"^\s*[A-Za-z][A-Za-z /&()-]{0,30}:\s*")
_GRADE = re.compile(r"\b\d\s*/\s*5\b|\b[0-4]\+\b")


def normalize_date(text: str) -> str | None:
    m = _DATE.search(text)
    if not m:
        return None
    if m.group("iso"):
        return m.group("iso")
    mm, dd, yy = int(m.group("m")), int(m.group("d")), m.group("y")
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    if yy:
        year = int(yy)
        year = year + (1900 if year >= 70 else 2000) if year < 100 else year
        return f"{year:04d}-{mm:02d}-{dd:02d}"
    return f"{mm:02d}/{dd:02d}"


def _temporality(text: str, line_prefix: str = "") -> str | None:
    date = normalize_date(text) or normalize_date(line_prefix)
    if date:
        return date
    # A day marker only dates the line when it is the line's label ("HD 3:");
    # inside a sentence it belongs to its own comma item (see _nearest_date).
    day = _DAY.search(line_prefix)
    if day:
        return f"day:{int(day.group(1))}"
    if _CURRENT.search(text):
        return "current"
    if _PRIOR.search(text):
        return "prior"
    return None


def _laterality(text: str) -> str | None:
    m = _SIDE_WORDS.search(text)
    if m:
        return "L" if m.group(1) else "R" if m.group(2) else "B"
    m = _SIDE_LETTER.search(text)
    if m:
        return "L" if m.group(1) else "R"
    return None


# --- medications ----------------------------------------------------------------

_DOSE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>mcg|micrograms?|mg|milligrams?|g|grams?|kg|"
                   r"units?|iu|meq|ml|mL|l|%)(?![A-Za-z])", re.IGNORECASE)
_UNIT_NORM = {"micrograms": "mcg", "microgram": "mcg", "milligrams": "mg", "milligram": "mg", "grams": "g",
              "gram": "g", "unit": "units", "iu": "IU", "meq": "mEq", "ml": "mL"}
_ROUTES = [
    ("PO", r"(?:po|by mouth|oral|orally|per os)"), ("IV", r"(?:iv|intravenous(?:ly)?)"),
    ("SC", r"(?:sc|sq|subq|subcut|subcutaneous(?:ly)?)"), ("IM", r"(?:im|intramuscular(?:ly)?)"),
    ("PR", r"(?:pr|rectal(?:ly)?)"), ("SL", r"(?:sl|sublingual(?:ly)?)"), ("TOP", r"(?:topical(?:ly)?)"),
    ("INH", r"(?:inhaled|inhalation)"), ("NEB", r"(?:nebulized|neb)"), ("NG", r"(?:ng|per tube|via tube)"),
]
_ROUTE_RE = [(name, re.compile(rf"(?<![A-Za-z]){pat}(?![A-Za-z])", re.IGNORECASE)) for name, pat in _ROUTES]
_FREQS = [
    ("daily", r"(?:daily|once daily|every day|each day|qd|qday|q24h|every 24 hours)"),
    ("nightly", r"(?:nightly|qhs|at bedtime|at night|every night)"),
    ("BID", r"(?:bid|twice daily|twice a day|two times daily|q12h|every 12 hours)"),
    ("TID", r"(?:tid|three times daily|three times a day|q8h|every 8 hours)"),
    ("QID", r"(?:qid|four times daily|four times a day|q6h|every 6 hours)"),
    ("q4h", r"(?:q4h|every 4 hours)"), ("q2h", r"(?:q2h|every 2 hours)"),
    ("weekly", r"(?:weekly|once weekly|every week|q week)"), ("PRN", r"(?:prn|as needed)"),
]
_FREQ_RE = [(name, re.compile(rf"(?<![A-Za-z0-9]){pat}(?![A-Za-z0-9])", re.IGNORECASE)) for name, pat in _FREQS]
_STATUSES = [
    ("held", r"(?:held|hold|holding|on hold)"),
    ("discontinued", r"(?:discontinued?|discontinuing|d/c'?d|dc'?d|stopp?ed|stop|stopping)"),
    ("started", r"(?:started|starting|start|began|begin|initiated?|new)"),
    ("continued", r"(?:continued?|continuing|cont\.?|resumed?|resuming)"),
    ("increased", r"(?:increased?|increasing|uptitrated?|titrate up)"),
    ("decreased", r"(?:decreased?|decreasing|reduced?|taper(?:ed|ing)?)"),
]
_STATUS_RE = [(name, re.compile(rf"(?<![A-Za-z]){pat}(?![A-Za-z])", re.IGNORECASE)) for name, pat in _STATUSES]
_MED_SPLIT = re.compile(r"\s+and\s+|,\s+(?=[A-Za-z])|\s*;\s*", re.IGNORECASE)
_DRUG_WORD = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z-]{3,}(?![A-Za-z])")


def _is_drug(word: str) -> bool:
    w = word.lower()
    if fc._is_drug(w):
        return True
    return "-" in w and any(fc._is_drug(part) for part in w.split("-") if len(part) >= 4)


def _first(res: list[tuple[str, re.Pattern]], text: str) -> str | None:
    best: tuple[int, str] | None = None
    for name, rx in res:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


def _medications(segment: str, span: SourceSpan, line_prefix: str, note_id: str, section: str
                 ) -> tuple[list[ClinicalStatement], list[tuple[int, int]]]:
    """Medication statements in ``segment`` and the dose spans they consumed."""
    out: list[ClinicalStatement] = []
    consumed: list[tuple[int, int]] = []
    drugs = [m for m in _DRUG_WORD.finditer(segment) if _is_drug(m.group(0))]
    if not drugs:
        return out, consumed
    chunks = list(_MED_SPLIT.finditer(segment))
    bounds = [0] + [c.end() for c in chunks] + [len(segment)]
    seg_temporality = _temporality(segment, line_prefix)
    seg_negative = bool(_NEG.search(segment[:drugs[0].start()]))
    for dm in drugs:
        # The chunk (between list separators) the drug sits in.
        start = max(b for b in bounds if b <= dm.start())
        end = min(b for b in bounds if b > dm.start()) if any(b > dm.start() for b in bounds) else len(segment)
        chunk = segment[start:end]
        after = segment[dm.end():end]
        dose = _DOSE.search(after)
        if dose and _DRUG_WORD.search(after[:dose.start()]) and any(
                _is_drug(w.group(0)) for w in _DRUG_WORD.finditer(after[:dose.start()])):
            dose = None  # that dose belongs to the next drug in the chunk
        value = unit = None
        if dose:
            value = fc._norm_number(dose.group("num"))
            raw_unit = dose.group("unit")
            unit = _UNIT_NORM.get(raw_unit.lower(), raw_unit if raw_unit.lower() in ("mg", "mcg", "g", "kg", "units", "%", "ml") else raw_unit.lower())
            if unit == "ml":
                unit = "mL"
            consumed.append((dm.end() + dose.start("num"), dm.end() + dose.end("num")))
        status = _first(_STATUS_RE, chunk)
        polarity = "negative" if (seg_negative and dm is drugs[0]) or _NEG.search(chunk[:dm.start() - start]) else "positive"
        out.append(ClinicalStatement(
            kind="medication", subject=dm.group(0).lower(), value=value, unit=unit,
            polarity=polarity, certainty="stated", temporality=seg_temporality, experiencer="patient",
            route=_first(_ROUTE_RE, after), frequency=_first(_FREQ_RE, after), medication_status=status,
            note_id=note_id, section=section, source_spans=(span,)))
    return out, consumed


# --- measurements -------------------------------------------------------------------

# A unit that names the measurement when no label precedes the number.
_UNIT_SUBJECT = {"c": "t", "f": "t", "°c": "t", "°f": "t", "bpm": "hr", "kg": "weight", "lb": "weight",
                 "lbs": "weight"}


@lru_cache(maxsize=1)
def _label_canon() -> dict[str, str]:
    out: dict[str, str] = {}
    for short, (_panel, names) in LABS.items():
        for name in names:
            out[name.lower()] = short.lower()
        out[short.lower()] = short.lower()
    for v in VITALS:
        out[str(v).lower()] = str(v).lower()
    for extra in fc._EXTRA_LABELS:
        out.setdefault(extra, extra)
    out.update({"blood pressure": "bp", "bp": "bp", "temperature": "t", "temp": "t", "t": "t",
                "heart rate": "hr", "pulse": "hr", "hr": "hr", "respiratory rate": "rr", "rr": "rr",
                "oxygen saturation": "spo2", "sat": "spo2", "sats": "spo2", "spo2": "spo2", "o2 sat": "spo2",
                "pupil": "pupil", "pupils": "pupil", "date": None})
    # A point-of-care glucose is the same measurement as the lab glucose.
    out["poc glucose"] = out.get("glucose", "glucose")
    out["glu"] = out.get("glucose", "glucose")
    out["gluc"] = out.get("glucose", "glucose")
    return {k: v for k, v in out.items() if v is not None}


def _nearest_date(segment: str, pos: int, fallback: str | None) -> str | None:
    """The date (or day marker) closest to the number at ``pos`` within its comma item."""
    end = len(segment)
    for i in range(pos, len(segment)):
        if segment[i] in ",;":
            end = i
            break
    start = 0
    for i in range(pos - 1, -1, -1):
        if segment[i] in ",;":
            start = i + 1
            break
    item_after, item_before = segment[pos:end], segment[start:pos]
    found = normalize_date(item_after) or normalize_date(item_before)
    if found:
        return found
    day = _DAY.search(item_before) or _DAY.search(item_after)
    if day:
        return f"day:{int(day.group(1))}"
    return fallback


def _nearest_side(segment: str, pos: int) -> str | None:
    """The side word nearest before the number at ``pos`` within its comma item."""
    start = 0
    for i in range(pos - 1, -1, -1):
        if segment[i] in ",;":
            start = i + 1
            break
    return _laterality(segment[start:pos])


_OWN_LABELS = {"pupil": "pupil", "pupils": "pupil", "pupillary": "pupil",
               "glu": "glucose", "gluc": "glucose",  # the abbreviation stage's short form
               "poc glu": "poc glucose", "poc gluc": "poc glucose"}
_QUALIFIERS = frozenset({"grade", "score", "scale", "class", "stage", "level", "of"})


def _own_label(before: str) -> str | None:
    """Labels fact_check does not know, plus "Hunt-Hess grade 3" → hunt-hess."""
    words = re.split(r"[^A-Za-z0-9-]+", before.strip().rstrip(":=#"))
    words = [w for w in words if w]
    while words and words[-1].lower() in _QUALIFIERS:
        words.pop()
    if not words:
        return None
    for n in (2, 1):
        if len(words) >= n:
            cand = " ".join(words[-n:]).lower()
            if cand in _OWN_LABELS:
                return _OWN_LABELS[cand]
    last = words[-1].lower()
    canon = _label_canon()
    for n in (2, 1):
        if len(words) >= n:
            cand = " ".join(words[-n:])
            if cand.lower() in fc._LABELS:
                return cand
            # The abbreviation stage may have shortened the label ("Hunt-Hess" →
            # "HH"): its dictionary expansion is the label to look up.
            expanded = canonical_subject(cand)
            if expanded in fc._LABELS or expanded in canon:
                return expanded
    return None


def _measurements(segment: str, span: SourceSpan, line_prefix: str, consumed: list[tuple[int, int]],
                  note_id: str, section: str) -> list[ClinicalStatement]:
    out: list[ClinicalStatement] = []
    masked = fc._mask(segment)
    fallback = _temporality(segment, line_prefix)
    canon = _label_canon()
    seen: set[int] = set()
    for m in fc._BP.finditer(masked):
        if any(a <= m.start() < b for a, b in consumed):
            continue
        label = fc._label_of(masked[max(0, m.start() - 48):m.start()]) or "bp"
        out.append(ClinicalStatement("measurement", canon.get(label.lower(), label.lower()), m.group(0),
                                     "mmHg", "positive", "stated", _nearest_date(segment, m.start(), fallback),
                                     "patient", _nearest_side(segment, m.start()),
                                     note_id=note_id, section=section, source_spans=(span,)))
        seen.update((m.start(1), m.start(2)))
    for m in fc._LABELED_NUM.finditer(masked):
        pos = m.start("num")
        if pos in seen or any(a <= pos < b for a, b in consumed):
            continue
        label = fc._label_of(masked[max(0, pos - 48):pos]) or _own_label(masked[max(0, pos - 48):pos])
        unit_m = fc._WITH_UNIT.match(masked, pos)
        unit = unit_m.group("unit") if unit_m else None
        if not label:
            # A bare "T 38.2 C" or "38.2 °C": the unit (or a one-letter vital
            # label) says what was measured.
            before = masked[:pos].rstrip(" \t:=")
            if re.search(r"(?<![A-Za-z])T$", before):
                label = "t"
            elif unit and _UNIT_SUBJECT.get(unit.replace(" ", "").casefold()):
                label = _UNIT_SUBJECT[unit.replace(" ", "").casefold()]
        if not label:
            continue
        out.append(ClinicalStatement("measurement", canon.get(label.lower(), label.lower()),
                                     fc._norm_number(m.group("num")), unit, "positive", "stated",
                                     _nearest_date(segment, pos, fallback), "patient",
                                     _nearest_side(segment, pos), note_id=note_id,
                                     section=section, source_spans=(span,)))
        seen.add(pos)
    return out


# --- pipe tables ----------------------------------------------------------------------



def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _table_statements(lines: list[tuple[int, str]], note_id: str, section_at) -> list[ClinicalStatement]:
    """Measurements from a header row of labels and rows of values.

    ``lines`` are ``(offset, text)`` for consecutive pipe-table lines; a date
    cell in a row dates every value on that row.
    """
    if len(lines) < 2:
        return []
    header = [canonical_subject(c) for c in _cells(lines[0][1])]
    canon = _label_canon()
    labels = [canon.get(h) for h in header]
    if not any(labels):
        return []
    out: list[ClinicalStatement] = []
    for offset, text in lines[1:]:
        cells = _cells(text)
        if set("".join(cells)) <= set("-: "):
            continue  # markdown separator row
        date = next((normalize_date(c) for c in cells if normalize_date(c)), None)
        span = SourceSpan(offset, offset + len(text))
        for label, cell in zip(labels, cells):
            if not label or not re.fullmatch(r"[<>]?\d+(?:\.\d+)?(?:/\d+)?", cell):
                continue
            out.append(ClinicalStatement("measurement", label, fc._norm_number(cell) if "/" not in cell else cell,
                                         None, "positive", "stated", date, "patient", note_id=note_id,
                                         section=section_at(offset), source_spans=(span,)))
    return out


# --- findings -----------------------------------------------------------------------

_FINDING_TERMS = frozenset("""
fever chills cough pain dyspnea nausea vomiting diarrhea constipation headache dizziness syncope weakness
numbness tingling edema rash bleeding distress infarct infarction hemorrhage hematoma stroke tia seizure
seizures sepsis pneumonia infection bacteremia uti pe dvt embolism thrombosis mi ischemia angina deficit
deficits crackles rales wheezing wheeze rhonchi confusion delirium agitation lethargy somnolence tremor
palsy aphasia dysarthria neglect hemianopia hemiparesis paresis plegia droop anticoagulation
intubated intubation extubated extubation candidate tpa thrombolysis thrombectomy craniotomy fracture
hypotension hypertension hypoxia hypoxemia tachycardia bradycardia arrhythmia fibrillation afib
hyponatremia hypernatremia hypokalemia hyperkalemia anemia thrombocytopenia leukocytosis hyperglycemia
hypoglycemia malignancy cancer carcinoma tumor mass lesion abscess cellulitis pressure ulcer wound
allergy allergies allergic reaction anaphylaxis disease syndrome failure insufficiency distension tenderness
guarding rebound murmur gallop rub jvd cyanosis clubbing pallor jaundice icterus lymphadenopathy
appendectomy cholecystectomy cabg stent bypass transplant dialysis ventilation vasospasm hydrocephalus
midline shift herniation edema hemorrhagic conversion aneurysm clipping coiling sah ich sdh edh ivh
weakness breath event events incontinence retention ileus obstruction perforation
pupil pupils reactive nonreactive sluggish brisk fixed dilated pinpoint anisocoria
""".split())
_SUFFIX = re.compile(r"\w{3,}(?:itis|emia|osis|pathy|oma|algia|plegia|paresis|penia|megaly|ectomy|ostomy|"
                     r"otomy|uria|pnea|rrhage|rrhagic|rrhea|iasis|trophy|edema|infarct\w*|ischemi\w*|"
                     r"fractures?|spasm|stenosis|sclerosis)\b", re.IGNORECASE)
# Words the suffix patterns catch that are not findings.
_NOT_FINDINGS = frozenset({"diagnosis", "diagnoses", "prognosis", "analysis", "analyses", "basis", "emphasis",
                           "hypothesis", "synthesis", "dose", "doses", "osteopathy", "aroma", "coma",
                           "diploma", "glaucoma", "soma", "comma", "atrophy", "hypertrophy"})
_LIST_SPLIT = re.compile(r",\s*|\s+and\s+|\s*/\s*(?![0-9])", re.IGNORECASE)
_CUE_STRIP = re.compile(
    r"(?<![A-Za-z])(?:no|not|denies|denied|deny|without|negative for|neg for|absent|none|never|free of|"
    r"possible|possibly|probable|probably|likely|suspected|suspect|suspicious for|concern for|concerning for|"
    r"rule out|cannot (?:exclude|rule out)|can't (?:exclude|rule out)|questionable|presumed|confirmed|"
    r"definite|definitive|diagnosed|history of|prior|previous|previously|remote|old|status post|resolved|"
    r"family history|mother|father|brother|sister|sibling|parents?|maternal|paternal|today|currently|now|"
    r"tonight|yesterday|overnight|this morning|evidence of|signs? of|complains of|reports?|noted|seen|"
    r"on exam|on|at|home|for|due to|secondary to|in|per)(?![A-Za-z])|\(-\)|\?",
    re.IGNORECASE)


@lru_cache(maxsize=1)
def _clinical_vocab() -> frozenset[str]:
    try:
        from .clinical_whitelist import _ANATOMICAL_AND_CLINICAL_TERMS
        return frozenset(_ANATOMICAL_AND_CLINICAL_TERMS) | _FINDING_TERMS
    except Exception:
        return _FINDING_TERMS


def _is_clinical(subject: str, *, cued: bool) -> bool:
    """Does ``subject`` name something clinical?

    With a cue (negation, uncertainty, history…) the broad anatomy/clinical
    whitelist counts; without one only the curated finding vocabulary and
    clinical suffixes do, so a header like "Progress Note" is not a finding.
    """
    words = re.findall(r"[a-z][a-z-]+", subject.lower())
    vocab = _clinical_vocab() if cued else _FINDING_TERMS
    if any(w in vocab or w.rstrip("s") in vocab for w in words):
        return True
    for m in _SUFFIX.finditer(subject):
        if m.group(0).lower() not in _NOT_FINDINGS and m.group(0).lower() not in {"coma", "glaucoma"} - {"glaucoma"}:
            return True
    return False


@lru_cache(maxsize=4096)
def signature(subject: str) -> str:
    """The subject as the bundled abbreviation matcher would write it.

    Two subjects are equivalent when their canonical forms agree *or* their
    signatures do: "lower extremity edema" and "LE edema" meet at "le edema".
    """
    try:
        from .abbreviations import abbreviate
        out, _n, _d = abbreviate(subject, None)
    except Exception:
        out = subject
    return _WS.sub(" ", _PUNCT.sub(" ", out.lower())).strip()


def equivalent(a: str, b: str) -> bool:
    return a == b or signature(a) == signature(b)


def _clean_subject(text: str) -> str:
    text = _SIDE_WORDS.sub(" ", text)
    text = _SIDE_LETTER.sub(" ", text)
    text = _CUE_STRIP.sub(" ", text)
    text = re.sub(r"\([^)]*\)", " ", text)  # "(SAH)" echo of the phrase in front of it
    text = _GRADE.sub(" ", text)
    text = _DATE.sub(" ", text)
    text = _AGE.sub(" ", text)
    text = re.sub(r"(?<![\d/])(?:19|20)\d{2}(?![\d/])", " ", text)
    text = re.sub(r"\d+(?:\.\d+)?\s*%?", " ", text)  # values belong to measurements, not subjects
    return canonical_subject(text)


def _findings(segment: str, span: SourceSpan, line_prefix: str, owned: bool, note_id: str, section: str
              ) -> tuple[list[ClinicalStatement], bool]:
    """Finding statements in ``segment`` and whether it carried unparsed clinical signal.

    ``owned`` says a medication or measurement parser already claimed the
    segment; it then only yields a finding when a negation makes it one.
    """
    body = _LABEL_PREFIX.sub("", segment, count=1) if ":" in segment else segment
    if len(body) < len(segment):
        span = SourceSpan(span.start + (len(segment) - len(body)), span.end)
    expanded = _expand(body)
    has_neg = _NEG.search(expanded) is not None
    has_possible = _POSSIBLE.search(expanded) is not None
    has_confirmed = _CONFIRMED.search(expanded) is not None
    has_family = _FAMILY.search(expanded) is not None
    has_past = _PAST.search(expanded) is not None
    signal = has_neg or has_possible or has_confirmed or has_family or has_past
    if owned and not has_neg:
        return [], False  # the medication / measurement parser owns this segment
    laterality = _laterality(expanded)
    temporality = "past" if has_past else _temporality(expanded, line_prefix)
    certainty = "confirmed" if has_confirmed else "possible" if has_possible else "stated"
    experiencer = "family" if has_family else "patient"
    polarity = "negative" if has_neg else "positive"
    # The subject is what follows the last cue ("rule out PE" → PE; "history of
    # stroke" → stroke); a negation at the start scopes over a comma/and list.
    cue_end = max((m.end() for rx in (_NEG, _POSSIBLE, _CONFIRMED, _FAMILY, _PAST)
                   for m in rx.finditer(expanded)), default=0)
    tail = expanded[cue_end:]
    neg_first = has_neg and _NEG.search(expanded) is not None and _NEG.search(expanded).start() <= 12
    if neg_first:
        parts = _LIST_SPLIT.split(tail if _clean_subject(tail) else expanded)
    else:
        head = tail if _clean_subject(tail) else expanded
        parts = [re.split(r",", head, maxsplit=1)[0]]
    out: list[ClinicalStatement] = []
    for part in parts:
        subject = _clean_subject(part)
        if not subject or len(subject.split()) > 8 or not _is_clinical(subject, cued=signal):
            continue
        out.append(ClinicalStatement("finding", subject, None, None, polarity, certainty, temporality,
                                     experiencer, laterality, note_id=note_id, section=section,
                                     source_spans=(span,)))
    return out, (signal and not out)


# --- segmentation --------------------------------------------------------------------

_SEG_BREAK = re.compile(r"(?<=[.;!?])\s+|\s*\|\s*|\t+")
_TABLE_BREAK = re.compile(r"(?<=[.;!?])\s+|\s*\|\s*|\t+|\s{3,}")
_SPACE_RUNS = re.compile(r" {2,}")


def _segments(line: str) -> list[tuple[int, int]]:
    # Runs of spaces are column breaks only on table-like lines; in prose they
    # are sloppy whitespace and must not cut a sentence in two.
    breaker = _TABLE_BREAK if ("|" in line or "\t" in line or len(_SPACE_RUNS.findall(line)) >= 3) else _SEG_BREAK
    out: list[tuple[int, int]] = []
    pos = 0
    for m in breaker.finditer(line):
        if m.start() > pos:
            out.append((pos, m.start()))
        pos = m.end()
    if pos < len(line):
        out.append((pos, len(line)))
    return [(a, b) for a, b in out if line[a:b].strip()]


def _section_lookup(text: str):
    try:
        from .stage_core import _section_spans
        spans = _section_spans(text)
    except Exception:
        spans = []

    def at(pos: int) -> str:
        for start, end, domain in spans:
            if start <= pos < end:
                return domain
        return ""

    return at


def extract_statements(text: str, *, source_offset: int = 0, note_id: str = "",
                       section: str = "") -> StatementExtraction:
    """Recognize statements in ``text``; spans are ``source_offset`` + local index."""
    statements: list[ClinicalStatement] = []
    unresolved: list[tuple[int, int]] = []
    if not text or not text.strip():
        return StatementExtraction((), ())
    text = _mask_tokens(text)  # "[REDACTED_ID]" must not read as "infectious diseases"
    section_at = _section_lookup(text) if not section else (lambda _pos: section)
    # Group consecutive pipe-delimited lines: two or more form a table (header
    # row of labels, rows of values); a lone one is ordinary text whose pipes
    # just separate segments.
    blocks: list[tuple[str, list[tuple[int, str]]]] = []
    pos = 0
    for line in text.split("\n"):
        if line.count("|") >= 2:
            if blocks and blocks[-1][0] == "table":
                blocks[-1][1].append((pos, line))
            else:
                blocks.append(("table", [(pos, line)]))
        else:
            blocks.append(("text", [(pos, line)]))
        pos += len(line) + 1
    for kind, items in blocks:
        if kind == "table" and len(items) >= 2:
            statements.extend(_table_statements([(source_offset + p, ln) for p, ln in items], note_id,
                                                lambda p: section_at(p - source_offset)))
            continue
        for pos, line in items:
            segs = _segments(line)
            for i, (a, b) in enumerate(segs):
                seg = line[a:b]
                prefix = line[:a]
                span = SourceSpan(source_offset + pos + a, source_offset + pos + b)
                sec = section_at(pos + a)
                meds, consumed = _medications(seg, span, prefix, note_id, sec)
                statements.extend(meds)
                measures = _measurements(seg, span, prefix, consumed, note_id, sec)
                statements.extend(measures)
                findings, unparsed = _findings(seg, span, prefix, bool(meds or measures), note_id, sec)
                statements.extend(findings)
                if unparsed and not meds and not measures:
                    unresolved.append(tuple(span))
    return StatementExtraction(tuple(statements), merge_spans(unresolved))


class StatementIndex:
    """The source's statements, indexed for whole-document questions."""

    def __init__(self, source: str):
        self.source = source
        self.extraction = extract_statements(source)
        self._by_key = self.extraction.by_key()
        self._latest: dict[tuple[str, str], str | None] = {}

    def get(self, key: tuple[str, str]) -> list[ClinicalStatement]:
        return self._by_key.get(key, [])

    def latest(self, key: tuple[str, str]) -> str | None:
        """The latest dated temporality among the source statements for ``key``."""
        if key not in self._latest:
            dates = [s.temporality for s in self.get(key)
                     if s.temporality and (s.temporality[0].isdigit())]
            self._latest[key] = max(dates) if dates else None
        return self._latest[key]
