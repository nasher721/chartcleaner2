"""Fill a note template from the cleaned chart ("Copy as… → note template").

Unlike prompt templates (which wrap the chart for an AI), a note template is
the note itself, filled with the chart's own lines. Placeholders:

``{{chart}}``            the whole cleaned chart
``{{date}}``             today (YYYY-MM-DD)
``{{systems}}``          every system block below, in order, empty ones left out
``{{system:N}}``         one block: N, CV, R, R/GU, GI, E, H, ID, Skin, Lines, Dispo
``{{section:NAME}}``     the body of a chart section ("Assessment & Plan", "Labs")
``{{problems}}`` ``{{devices}}`` ``{{micro}}`` ``{{overnight}}`` ``{{trends}}``
                         the matching insight block (see :mod:`chartcleaner.service`)

A system block collects the lines of the most recent note that mention that
system's terms (``[N]`` gets GCS, EVD, ICP, seizure, nimodipine…), verbatim,
each line in the first system that claims it. ``note_templates`` in
config.json adds templates or replaces a built-in of the same name.
"""

from __future__ import annotations

import re
from datetime import date as _date

__all__ = ["SYSTEMS", "DEFAULT_TEMPLATES", "templates", "get", "render", "systems"]

# system code -> terms (lowercase; ≤3 letters match whole words)
SYSTEMS: dict[str, tuple[str, ...]] = {
    "N": ("gcs", "nihss", "evd", "icp", "cpp", "seizure", "eeg", "pupil", "mental status",
          "sah", "ich", "stroke", "infarct", "aneurysm", "vasospasm", "nimodipine", "levetiracetam",
          "keppra", "lacosamide", "tcd", "transcranial", "hydrocephalus", "csf", "craniotomy",
          "craniectomy", "rass", "cam-icu", "sedation", "propofol", "dexmedetomidine", "fentanyl",
          "headache", "motor", "strength", "sensation", "cranial nerve", "aphasia", "hunt-hess", "mrs"),
    "CV": ("bp", "sbp", "dbp", "map", "hr", "heart rate", "blood pressure", "nicardipine", "clevidipine",
           "labetalol", "hydralazine", "norepinephrine", "pressor", "vasopressin", "phenylephrine",
           "metoprolol", "amiodarone", "afib", "atrial fibrillation", "troponin", "echo", "ekg", "ecg",
           "tachycardia", "bradycardia", "hypotension", "hypertension", "htn"),
    "R": ("spo2", "o2", "fio2", "peep", "vent", "ventilator", "intubated", "extubat", "ett", "trach",
          "rr", "respiratory", "abg", "pco2", "po2", "cxr", "chest x-ray", "nasal cannula", "bipap",
          "hfnc", "pneumonia", "sputum", "lungs", "breath"),
    "R/GU": ("cr", "creatinine", "bun", "uop", "urine output", "foley", "k", "potassium", "mg",
             "magnesium", "phos", "na", "sodium", "osm", "aki", "renal", "dialysis", "i/o",
             "intake", "output", "net", "salt tabs", "hypertonic", "fluid"),
    "GI": ("diet", "npo", "tube feed", "tf", "bowel", "bm", "lft", "ast", "alt", "bilirubin", "lipase",
           "ppi", "pantoprazole", "famotidine", "nausea", "abdomen", "dobhoff", "ngt", "ogt", "peg",
           "swallow", "slp", "constipation", "senna", "docusate", "miralax"),
    "E": ("glucose", "glu", "insulin", "a1c", "hba1c", "tsh", "cortisol", "hydrocortisone",
          "levothyroxine", "dm", "diabetes"),
    "H": ("hgb", "hemoglobin", "hct", "plt", "platelet", "inr", "ptt", "fibrinogen", "transfus",
          "prbc", "vte", "dvt", "heparin", "enoxaparin", "scd", "apixaban", "warfarin", "kcentra",
          "anticoag", "aspirin", "clopidogrel"),
    "ID": ("wbc", "tmax", "fever", "febrile", "afebrile", "culture", "cx", "lactate", "procalcitonin",
           "vancomycin", "cefepime", "ceftriaxone", "cefazolin", "piperacillin", "zosyn", "meropenem",
           "metronidazole", "antibiotic", "abx", "sepsis", "infection"),
    "Skin": ("skin", "wound", "pressure injury", "ulcer", "incision", "braden", "rash"),
    "Lines": ("central line", "cvc", "picc", "arterial line", "a-line", "art line", "midline",
              "piv", "iv access"),
    "Dispo": ("dispo", "disposition", "pt/ot", "physical therapy", "rehab", "snf", "ltach",
              "discharge", "family meeting", "goals of care", "code status", "full code", "dnr"),
}
SYSTEM_ORDER = tuple(SYSTEMS)

DEFAULT_TEMPLATES: list[dict] = [
    {"name": "Systems note ([N] [CV] [R] …)", "template":
        "{{date}}\n\n{{overnight}}\n\n{{systems}}\n\n{{devices}}\n\n{{micro}}"},
    {"name": "Interval note", "template":
        "Interval note {{date}}\n\nOvernight / interval events:\n{{overnight}}\n\n"
        "Assessment & plan:\n{{section:Assessment & Plan}}\n\nTrends:\n{{trends}}"},
    {"name": "Problem-oriented note", "template": "{{date}}\n\n{{problems}}\n\n{{devices}}"},
]

_PLACEHOLDER = re.compile(r"\{\{\s*(?P<name>[a-z]+)(?::(?P<arg>[^}]+))?\s*\}\}", re.IGNORECASE)


def templates(cfg: dict | None = None) -> list[dict]:
    """Built-ins, then the user's (same name replaces the built-in)."""
    out = {t["name"]: dict(t) for t in DEFAULT_TEMPLATES}
    for t in ((cfg or {}).get("note_templates") or []) if isinstance(cfg, dict) else []:
        if (isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip()
                and isinstance(t.get("template"), str)):
            out[t["name"].strip()] = {"name": t["name"].strip(), "template": t["template"]}
    return list(out.values())


def get(name: str, cfg: dict | None = None) -> dict:
    for t in templates(cfg):
        if t["name"].casefold() == name.strip().casefold():
            return t
    raise KeyError(f"Unknown note template: {name}")


def _term_regex(terms: tuple[str, ...]) -> re.Pattern:
    parts = []
    for t in sorted(terms, key=len, reverse=True):
        if len(t) <= 3:
            parts.append(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])")
        else:
            parts.append(rf"(?<![A-Za-z]){re.escape(t)}")
    return re.compile("|".join(parts), re.IGNORECASE)


_SYSTEM_RX = {code: _term_regex(terms) for code, terms in SYSTEMS.items()}
_SKIP = re.compile(r"^\s*(?:#{1,6}\s|[A-Z][A-Za-z /&]{1,40}:\s*$)")


def _latest_note(text: str) -> str:
    from .delta_engine import _split_into_notes
    notes = _split_into_notes(text)
    return notes[-1].raw_text if notes else text


def systems(text: str) -> dict[str, list[str]]:
    """Lines of the latest note grouped by system (each line in its first match)."""
    from .problems import logical_lines
    groups: dict[str, list[str]] = {code: [] for code in SYSTEM_ORDER}
    for line in logical_lines(_latest_note(text)):
        stripped = line.strip()
        if not stripped or _SKIP.match(line) or len(stripped) < 3:
            continue
        for code in SYSTEM_ORDER:
            if _SYSTEM_RX[code].search(stripped):
                if stripped not in groups[code]:
                    groups[code].append(stripped)
                break
    return groups


def _systems_text(groups: dict[str, list[str]], only: str | None = None) -> str:
    out = []
    for code in SYSTEM_ORDER:
        if only is not None and code.casefold() != only.casefold():
            continue
        lines = groups.get(code) or []
        if not lines and only is None:
            continue
        body = "\n".join(f"- {ln.lstrip('-•* ').strip()}" for ln in lines) or "- not documented"
        out.append(f"[{code}]\n{body}")
    return "\n\n".join(out)


def _section(text: str, name: str) -> str:
    from .section_parser import parse_clinical_sections
    want = re.sub(r"[^a-z0-9]+", " ", name.casefold().replace("&", " and ")).strip()
    parsed = parse_clinical_sections(_latest_note(text))
    for sec in reversed(parsed.sections):
        title = re.sub(r"[^a-z0-9]+", " ", sec.title.casefold().replace("&", " and ")).strip()
        if title == want or sec.domain == want.replace(" ", "_"):
            return sec.content.strip()
    return ""


def render(name: str, chart: str, cfg: dict | None = None, *, today: _date | None = None) -> str:
    """Note template ``name`` filled from ``chart`` (already cleaned)."""
    from .service import _unwrap, insights_text

    body = get(name, cfg)["template"]
    text = _unwrap(chart)
    cache: dict[str, str] = {}
    groups: dict[str, list[str]] | None = None

    def value(m: re.Match) -> str:
        nonlocal groups
        key, arg = m.group("name").lower(), (m.group("arg") or "").strip()
        if key == "chart":
            return text
        if key == "date":
            return (today or _date.today()).isoformat()
        if key in ("systems", "system"):
            if groups is None:
                groups = systems(text)
            return _systems_text(groups, arg if key == "system" else None)
        if key == "section":
            return _section(text, arg)
        if key in ("problems", "devices", "micro", "overnight", "trends"):
            if key not in cache:
                cache[key] = insights_text(text, (key,))
            return cache[key]
        return m.group(0)  # unknown placeholders stay visible

    filled = _PLACEHOLDER.sub(value, body)
    return re.sub(r"\n{3,}", "\n\n", filled).strip() + "\n"
