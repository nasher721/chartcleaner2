"""Problem-oriented view: everything the chart says about each problem, in one place.

1. **Problems** come from the Assessment & Plan of the most recent note that
   has one: numbered (``1. SAH, Hunt-Hess 2``), hashed (``# Hyponatremia``)
   or ``Problem:`` lines. The plan lines under each (``- Nimodipine 60 mg``)
   stay attached.
2. **Search terms** for a problem are the words of its title plus related
   terms from :data:`RELATED` (SAH → vasospasm, nimodipine, TCD, sodium…),
   so the labs, drips and exam lines that belong to it are found even when
   they don't repeat the problem's name.
3. **Evidence** is every other chart line that mentions a term, copied
   verbatim with the note it came from (copy-forward duplicates collapse to
   the latest one), lines with clinical values first.

Nothing is reworded: the output only regroups the chart's own lines, so it is
as grounded as the chart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["RELATED", "Evidence", "Problem", "ProblemReport", "build", "logical_lines"]

# problem keyword -> related terms (all lowercase; short ones match whole words)
RELATED: dict[str, tuple[str, ...]] = {
    "sah": ("subarachnoid", "aneurysm", "coil", "clip", "vasospasm", "nimodipine", "tcd",
            "transcranial", "dci", "delayed cerebral ischemia", "hunt-hess", "fisher", "sodium", "na",
            "hydrocephalus", "evd"),
    "subarachnoid": ("sah", "aneurysm", "vasospasm", "nimodipine", "tcd", "transcranial"),
    "ich": ("hemorrhage", "hematoma", "sbp", "nicardipine", "clevidipine", "kcentra", "andexanet",
            "ich score", "edema"),
    "intracerebral": ("ich", "hematoma", "sbp", "nicardipine"),
    "stroke": ("infarct", "nihss", "tnk", "tpa", "alteplase", "thrombectomy", "mca", "aca", "pca",
               "aspirin", "clopidogrel", "statin", "atorvastatin", "mri", "cta"),
    "infarct": ("stroke", "nihss", "mca", "aspirin", "statin"),
    "tbi": ("contusion", "sdh", "subdural", "epidural", "icp", "cpp", "gcs", "craniectomy", "evd"),
    "evd": ("external ventricular drain", "icp", "csf", "drain", "cm h2o", "ventriculostomy"),
    "icp": ("evd", "cpp", "hypertonic", "mannitol", "osm", "sodium"),
    "seizure": ("eeg", "levetiracetam", "keppra", "lacosamide", "fosphenytoin", "phenytoin",
                "valproate", "lorazepam", "status"),
    "status epilepticus": ("eeg", "levetiracetam", "lacosamide", "midazolam", "propofol"),
    "hypertension": ("htn", "sbp", "bp", "nicardipine", "clevidipine", "labetalol", "hydralazine",
                     "amlodipine", "lisinopril", "metoprolol"),
    "htn": ("hypertension", "sbp", "bp", "nicardipine", "labetalol", "amlodipine"),
    "hyponatremia": ("sodium", "na", "osm", "salt tabs", "hypertonic", "fludrocortisone", "siadh",
                     "csw", "fluid restriction"),
    "sodium": ("na", "hyponatremia", "hypernatremia", "hypertonic", "osm"),
    "hypernatremia": ("sodium", "na", "free water", "d5w"),
    "aki": ("creatinine", "cr", "bun", "uop", "urine output", "renal"),
    "renal": ("creatinine", "cr", "bun", "uop"),
    "pneumonia": ("cxr", "chest x-ray", "sputum", "culture", "wbc", "fever", "tmax", "cefepime",
                  "ceftriaxone", "vancomycin", "piperacillin", "zosyn", "azithromycin", "o2", "fio2"),
    "vap": ("pneumonia", "sputum", "culture", "cefepime", "vancomycin", "fio2", "peep"),
    "sepsis": ("lactate", "wbc", "fever", "culture", "norepinephrine", "pressor", "map",
               "vancomycin", "cefepime", "piperacillin", "meropenem"),
    "fever": ("tmax", "culture", "wbc", "temp"),
    "uti": ("urine", "ua", "culture", "ceftriaxone", "nitrofurantoin", "foley"),
    "respiratory failure": ("intubated", "ett", "vent", "fio2", "peep", "abg", "spo2", "extubat",
                            "trach"),
    "hypoxia": ("spo2", "o2", "fio2", "nasal cannula", "bipap", "hfnc"),
    "atrial fibrillation": ("afib", "a-fib", "af", "rvr", "hr", "diltiazem", "metoprolol",
                            "amiodarone", "apixaban", "heparin", "anticoagulation"),
    "afib": ("atrial fibrillation", "rvr", "hr", "diltiazem", "metoprolol", "amiodarone"),
    "heart failure": ("hfref", "ef", "bnp", "furosemide", "lasix", "diuresis", "edema"),
    "diabetes": ("glucose", "insulin", "a1c", "hba1c", "dm", "metformin"),
    "dm": ("glucose", "insulin", "a1c", "diabetes"),
    "hyperglycemia": ("glucose", "insulin"),
    "anemia": ("hgb", "hemoglobin", "transfus", "prbc", "iron"),
    "thrombocytopenia": ("plt", "platelet", "hit"),
    "coagulopathy": ("inr", "ptt", "fibrinogen", "kcentra", "vitamin k", "ffp"),
    "dvt": ("vte", "heparin", "enoxaparin", "apixaban", "duplex", "scd"),
    "ppx": ("prophylaxis", "vte", "dvt", "scd", "heparin", "enoxaparin"),
    "prophylaxis": ("vte", "dvt", "scd", "heparin", "enoxaparin", "ppi", "pantoprazole", "famotidine"),
    "vte": ("dvt", "pe", "heparin", "enoxaparin", "scd"),
    "nutrition": ("tube feed", "tf", "dobhoff", "ngt", "og", "peg", "albumin", "prealbumin", "diet"),
    "dysphagia": ("swallow", "slp", "npo", "diet", "ngt", "dobhoff"),
    "delirium": ("cam-icu", "rass", "quetiapine", "haloperidol", "dexmedetomidine", "melatonin"),
    "pain": ("cpot", "acetaminophen", "oxycodone", "fentanyl", "hydromorphone", "morphine"),
    "agitation": ("rass", "dexmedetomidine", "propofol", "quetiapine"),
    "hyperlipidemia": ("statin", "atorvastatin", "rosuvastatin", "ldl"),
}

_STOP = {"and", "or", "of", "the", "with", "without", "s/p", "status", "post", "day", "pod",
         "hd", "likely", "possible", "probable", "history", "hx", "acute", "chronic", "on", "in",
         "for", "to", "a", "an", "at", "per", "vs", "versus", "secondary", "due", "new", "known",
         "continue", "continued", "grade", "stable", "improving", "worsening", "resolved",
         "place", "placed", "home", "regimen", "diet", "resumes", "only", "given", "today",
         "will", "discuss", "draining", "monitor", "monitoring", "plan", "management", "w", "w/",
         "s/p", "follow", "f/u", "overread", "by", "dr", "pt", "patient", "x", "d", "q", "prn"}
_AP_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?(?:assessment\s*(?:&|and|/)\s*plan|a\s*/\s*p|impression\s*(?:&|and)\s*plan|"
                         r"assessment|plan|problem list|hospital course by problem)\s*:?\s*$",
                         re.IGNORECASE)
_PROBLEM = re.compile(r"^\s*(?:#(?!#)\s*|\d{1,2}[.)]\s+|problem\s*\d*\s*[:.]\s*)(?P<title>\S.*)$", re.IGNORECASE)
_PLAN_LINE = re.compile(r"^\s+[-•*]|^\s*[-•*]\s+|^\s{2,}\S")
_SECTION_END = re.compile(r"^\s*(?:[A-Z][A-Za-z /&]{2,40}):\s*$|^\s*#{2,6}\s+\S")
_FACT_HINT = re.compile(r"\d")


@dataclass
class Evidence:
    line: str
    note: str
    offset: int

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Problem:
    title: str
    heading: str
    note: str = ""        # the note whose A&P listed it last
    plan: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def to_text(self, max_evidence: int = 12) -> str:
        out = [f"# {self.heading}" + (f"  (last listed {self.note})" if self.note else "")]
        out.extend(f"  {p}" for p in self.plan)
        if self.evidence:
            out.append("  From the chart:")
            for e in self.evidence[:max_evidence]:
                out.append(f"  - {('[' + e.note + '] ') if e.note else ''}{e.line}")
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"title": self.title, "heading": self.heading, "note": self.note, "plan": list(self.plan),
                "terms": list(self.terms), "evidence": [e.to_dict() for e in self.evidence]}


@dataclass
class ProblemReport:
    problems: list[Problem]

    @property
    def empty(self) -> bool:
        return not self.problems

    def to_text(self, max_evidence: int = 12) -> str:
        if not self.problems:
            return ""
        return "Problem-oriented view\n\n" + "\n\n".join(p.to_text(max_evidence) for p in self.problems)

    def to_dict(self) -> dict:
        return {"problems": [p.to_dict() for p in self.problems], "text": self.to_text()}


def continues(prev: str, line: str) -> bool:
    """True when ``line`` is the wrapped rest of ``prev`` (not a new line, item or heading)."""
    words = prev.split()
    title_like = len(words) <= 4 and all(w[:1].isupper() or not w[:1].isalpha() for w in words)
    return bool(line.strip() and prev.strip() and not title_like
                and not re.search(r"[.:;!?]\s*$", prev)
                and (re.match(r"\s*[a-z0-9(]", line) or re.search(r"[a-z,]\s*$", prev))
                and not _PLAN_LINE.match(line) and not _PROBLEM.match(line)
                and not _AP_HEADING.match(prev) and not _SECTION_END.match(prev)
                and not _AP_HEADING.match(line) and not _SECTION_END.match(line))


def logical_lines(text: str) -> list[str]:
    """``text`` split into lines with wrapped lines rejoined."""
    out: list[str] = []
    for line in text.split("\n"):
        if out and continues(out[-1], line):
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return out


def _title_of(raw: str) -> str:
    """"SAH, Hunt-Hess 2, Fisher 3, post-coiling day 4." → "SAH"."""
    head = re.split(r"[,:;(]| — | - |\.\s", raw.strip(), maxsplit=1)[0]
    return head.strip(" .-—").strip()


def _problems_in(lines: list[str]) -> list[Problem]:
    """Problems (with plan lines) from the lines of one note."""
    problems: list[Problem] = []
    in_ap = False
    current: Problem | None = None
    for line in lines:
        if _AP_HEADING.match(line):
            in_ap, current = True, None
            continue
        if not in_ap:
            continue
        if not line.strip():
            continue
        m = _PROBLEM.match(line)
        if m and not _PLAN_LINE.match(line) or (m and line.lstrip().startswith("#")):
            title = _title_of(m.group("title"))
            if title:
                current = Problem(title=title, heading=m.group("title").strip())
                problems.append(current)
                continue
        if _SECTION_END.match(line) and not m:
            in_ap, current = False, None
            continue
        if current is not None and (_PLAN_LINE.match(line) or line.strip().startswith(("-", "•"))):
            current.plan.append(line.strip())
    return problems


def _terms(title: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'/-]*", title.lower()) if w not in _STOP]
    terms = list(dict.fromkeys(w for w in words if len(w) >= 2))
    low = title.lower()
    for key, related in RELATED.items():
        if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", low):
            terms.extend(t for t in (key, *related) if t not in terms)
    return terms


def _term_regex(terms: list[str]) -> re.Pattern | None:
    if not terms:
        return None
    parts = []
    for t in sorted(terms, key=len, reverse=True):
        if len(t) <= 3:
            # short terms (na, bp, cr, af) must be whole words, as written in charts
            parts.append(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])")
        else:
            parts.append(rf"(?<![A-Za-z]){re.escape(t)}")
    return re.compile("|".join(parts), re.IGNORECASE)


def build(text: str, max_evidence: int = 25) -> ProblemReport:
    """Problems from the latest A&P, each with the chart lines that relate to it."""
    from .delta_engine import _split_into_notes

    notes = _split_into_notes(text)
    multi = len(notes) > 1

    def note_label(note) -> str:
        if not multi:
            return ""
        return (note.date_str if note.date_str and not note.date_str.startswith("Day ")
                else f"Note {note.index}")

    # Latest note first; a problem an earlier A&P listed (and a copy-forward
    # trimmed away) is kept, labelled with the note that last listed it.
    problems: list[Problem] = []
    seen: set[str] = set()
    newest_with_ap = None
    for note in reversed(notes):
        found = _problems_in(note.raw_text.split("\n"))
        if found and newest_with_ap is None:
            newest_with_ap = note
        for prob in found:
            key = prob.title.lower()
            if key in seen:
                continue
            seen.add(key)
            if note is not newest_with_ap:
                prob.note = note_label(note)
            problems.append(prob)
    if not problems:
        return ProblemReport([])

    # every logical line of the chart (wrapped lines rejoined) with its note label and offset
    labelled: list[tuple[str, str, int]] = []
    cursor = 0
    for note in notes:
        start = text.find(note.raw_text[:80], cursor) if note.raw_text else cursor
        start = cursor if start < 0 else start
        label = note_label(note)
        pos = start
        for line in note.raw_text.split("\n"):
            prev = labelled[-1] if labelled and labelled[-1][1] == label else None
            if prev is not None and continues(prev[0], line):
                labelled[-1] = (prev[0].rstrip() + " " + line.strip(), label, prev[2])
            else:
                labelled.append((line, label, pos))
            pos += len(line) + 1
        cursor = max(cursor, start + 1)

    plan_lines = {p.strip() for prob in problems for p in [prob.heading, *prob.plan]}
    for prob in problems:
        prob.terms = _terms(prob.title)
        rx = _term_regex(prob.terms)
        if rx is None:
            continue
        latest: dict[str, Evidence] = {}
        for line, label, offset in labelled:
            stripped = line.strip()
            if (not stripped or stripped in plan_lines or _PROBLEM.match(line)
                    or _AP_HEADING.match(line) or len(stripped) < 4):
                continue
            if rx.search(stripped):
                key = re.sub(r"\s+", " ", stripped.lower())
                latest[key] = Evidence(stripped[:300], label, offset)  # later notes win
        found = list(latest.values())
        found.sort(key=lambda e: (not _FACT_HINT.search(e.line), -e.offset))
        prob.evidence = found[:max_evidence]
    return ProblemReport(problems)
