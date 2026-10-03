"""Normalize medication lists (stage ``med_normalize``).

Only lines inside a medication section are touched — a section starts at a
header such as "Medications", "Current Outpatient Medications", "Scheduled
Meds", "Continuous Infusions" or "PRN Meds" and ends at the next header or a
blank line followed by non-medication text. Within it, each line is reduced to
``drug dose route frequency [PRN reason]``:

* brand names in capitals in parentheses are dropped: ``atorvastatin (LIPITOR)``
* order clutter is dropped: Dispense/Refills counts, start/end dates,
  ordering/authorizing provider, "Taking"/"Not Taking" columns
  (``drop_fields`` adds regexes)
* "Take 1 tablet (40 mg total) by mouth daily." → ``40 mg PO daily``
* routes and frequencies are shortened via ``route_map`` / ``frequency_map``
  (JSON-overridable); Do Not Use forms are never produced (no QD, SC, HS)
* "Held" / "Discontinued" / "Paused" status words become ``(held)`` /
  ``(discontinued)``.

Lines the rules don't recognize stay exactly as written.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

DEFAULTS: dict[str, Any] = {"enabled": False, "drop_fields": [], "route_map": {},
                            "frequency_map": {}}

ROUTES = {
    "by mouth": "PO", "oral": "PO", "orally": "PO", "intravenous": "IV", "intravenously": "IV",
    "intramuscular": "IM", "subcutaneous": "subcut", "subcutaneously": "subcut",
    "sublingual": "SL", "per tube": "per tube", "via g-tube": "per G-tube", "inhalation": "inhaled",
    "topical": "topical", "rectal": "PR", "nasal": "intranasal", "transdermal": "transdermal",
}
FREQUENCIES = {
    "once daily": "daily", "one time daily": "daily", "every day": "daily", "daily": "daily",
    "two times daily": "BID", "twice daily": "BID", "2 times daily": "BID",
    "three times daily": "TID", "3 times daily": "TID", "four times daily": "QID", "4 times daily": "QID",
    "every morning": "every morning", "nightly": "nightly", "at bedtime": "at bedtime",
    "every other day": "every other day", "weekly": "weekly", "once": "once",
    "every 2 hours": "q2h", "every 4 hours": "q4h", "every 6 hours": "q6h", "every 8 hours": "q8h",
    "every 12 hours": "q12h", "every 24 hours": "q24h", "continuous": "continuous",
    "as needed": "PRN", "as needed for": "PRN",
}
_HEADER = re.compile(
    r"^[ \t]*(?:current\s+(?:outpatient\s+|inpatient\s+)?|active\s+|home\s+|scheduled\s+|prn\s+|discharge\s+)?"
    r"(?:medications?|meds|continuous\s+infusions?|infusions)[ \t]*:?[ \t]*$", re.IGNORECASE)
_COLUMN_HEADER = re.compile(r"^[ \t]*Medication[ \t]+Sig\b.*$", re.IGNORECASE)
_OTHER_HEADER = re.compile(r"^[ \t]*[A-Z][A-Za-z /&()-]{2,40}:?[ \t]*$")
_DROP = [
    r"\bDispense:?\s*\d+(?:\.\d+)?\s*[A-Za-z]*(?:\s*\([^)]*\))?",
    r"\bRefills?:?\s*\d+",
    r"\b(?:Start|End|Last)\s+(?:Date|dose)?:?\s*\d{1,2}/\d{1,2}/\d{2,4}",
    r"\b(?:Ordering|Authorizing|Prescribing)\s+Provider:?\s*[^;\n]*",
    r"\bPatient\s+not\s+taking:?[^;\n]*",
    r"\b(?:Not\s+)?Taking\b",
    r"\s\d+\s+(?:tablet|capsule|each|vial|bottle|inhaler|patch)s?\s+\d+\s*$",  # trailing "90 tablet 3"
    r"\(\s*[A-Z][A-Z0-9 -]{2,}\s*\)",  # brand name in capitals
]
_SIG = re.compile(r"\b(?:Take|Give|Inject|Apply|Inhale|Place|Use|Instill)\s+"
                  r"(?P<qty>\d+(?:\.\d+)?(?:\s*(?:tablets?|capsules?|puffs?|units?|mL|drops?|patch(?:es)?|sprays?))?)\s*"
                  r"(?:\((?P<total>[^)]*?)\s*total\))?", re.IGNORECASE)
_STRENGTH = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:mg|mcg|g|mEq|units?)\b(?!\s*/)", re.IGNORECASE)
_STATUS = re.compile(r"\[?\s*\b(Held|Hold|Paused|Discontinued|Stopped)\b\s*\]?", re.IGNORECASE)


def _phrase_map(base: dict[str, str], extra: Any) -> list[tuple[re.Pattern, str]]:
    merged = {**base, **({k.casefold(): v for k, v in extra.items()
                          if isinstance(k, str) and isinstance(v, str)} if isinstance(extra, dict) else {})}
    return [(re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE), v)
            for k, v in sorted(merged.items(), key=lambda kv: -len(kv[0]))]


def _normalize(line: str, drops: list[re.Pattern], routes, freqs) -> str:
    bullet = re.match(r"^[ \t]*(?:[-•*·]|\d+[.)])?[ \t]*", line).group(0)
    body = line[len(bullet):]
    status = _STATUS.search(body)
    body = _STATUS.sub("", body)
    for pattern in drops:
        body = pattern.sub(" ", body)
    sig = _SIG.search(body)
    if sig and sig.group("total"):
        # The dose given replaces the product strength: "500 mg tablet ... (1,000 mg total)".
        before = body[:sig.start()]
        product = _STRENGTH.search(before)
        if product:
            before = before[:product.start()] + before[product.end():]
        body = before + sig.group("total").strip() + " " + body[sig.end():]
    elif sig:
        # No total given: keep the quantity ("Inject 10 Units" -> "10 Units"), except a
        # lone "1 tablet" after a strength, which adds nothing.
        qty = sig.group("qty")
        if re.fullmatch(r"1\s*(?:tablet|capsule)", qty, re.IGNORECASE) and _STRENGTH.search(body[:sig.start()]):
            qty = ""
        body = body[:sig.start()] + qty + " " + body[sig.end():]
    for pattern, short in routes:
        body = pattern.sub(short, body)
    for pattern, short in freqs:
        body = pattern.sub(short, body)
    body = re.sub(r"(?<!\d )\b(?:tablet|capsule)\b", "", body, flags=re.IGNORECASE)
    body = re.sub(r"\s+([,.;])", r"\1", " ".join(body.split())).strip(" .;,")
    if status:
        word = status.group(1).casefold()
        body += " (held)" if word in ("held", "hold", "paused") else " (discontinued)"
    return bullet.rstrip() + (" " if bullet.strip() else "") + body if body else line


def med_section_lines(lines: list) -> Iterator[tuple[int, str]]:
    """(index, line) for every non-blank line inside a medication section."""
    in_meds = False
    for i, line in enumerate(lines):
        if line is None:
            continue
        if _HEADER.match(line):
            in_meds = True
            continue
        if not in_meds:
            continue
        if not line.strip():
            nxt = next((l for l in lines[i + 1:] if l and l.strip()), "")
            in_meds = bool(re.match(r"^[ \t]*(?:[-•*·]|\d+[.)])?[ \t]*[a-z]", nxt))
            continue
        if _OTHER_HEADER.match(line) and not re.search(r"\d", line) and not _COLUMN_HEADER.match(line):
            in_meds = False
            continue
        yield i, line


def medication_list(text: str, cfg: dict | None = None) -> list[str]:
    """Normalized medication lines from every medication section in ``text``."""
    options = {**DEFAULTS, **((cfg or {}).get("med_normalize") or {})}
    drops = [re.compile(p, re.IGNORECASE) for p in _DROP + list(options.get("drop_fields") or [])]
    routes = _phrase_map(ROUTES, options.get("route_map"))
    freqs = _phrase_map(FREQUENCIES, options.get("frequency_map"))
    out = []
    lines = text.split("\n")
    for _, line in med_section_lines(lines):
        if _COLUMN_HEADER.match(line):
            continue
        norm = _normalize(line, drops, routes, freqs)
        norm = re.sub(r"^[ \t]*(?:[-•*·]|\d+[.)])[ \t]*", "", norm).strip()
        if norm:
            out.append(norm)
    return out


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("med_normalize") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    drops = [re.compile(p, re.IGNORECASE) for p in _DROP + list(options.get("drop_fields") or [])]
    routes = _phrase_map(ROUTES, options.get("route_map"))
    freqs = _phrase_map(FREQUENCIES, options.get("frequency_map"))
    lines: list[str | None] = list(text.split("\n"))
    changed = 0
    for i, line in med_section_lines(lines):
        if _COLUMN_HEADER.match(line):
            lines[i] = None  # removed below
            changed += 1
            continue
        new = _normalize(line, drops, routes, freqs)
        if new != line:
            lines[i] = new
            changed += 1
    if not changed:
        return text, 0, {}
    return "\n".join(line for line in lines if line is not None), changed, {"lines": changed}
