"""Condense MAR (Medication Administration Record) & PRN bolus logs (stage ``mar_summary``).

Epic MAR pastes and "Medications Given (Last 24h)" sections often list every
individual PRN bolus, push, or scheduled administration on its own timestamped
line::

    Medications Given (Last 24 Hours):
    10/02 01:15  fentanyl 25 mcg IV Push Given
    10/02 03:40  fentanyl 25 mcg IV Push Given
    10/02 06:10  fentanyl 50 mcg IV Push Given

becomes::

    Medications Given (Last 24 Hours):
    Fentanyl IV: 100 mcg total over 3 doses (25, 25, 50 mcg; 10/02 01:15–10/02 06:10, last 50 mcg at 10/02 06:10)

When all given doses of a medication are identical, the breakdown uses ``N × dose``
(e.g. ``4 × 25 mcg``). Held or refused administrations are never added to the
numeric total and are noted separately (``; 1 held``). As with all compactors,
a block is rewritten only when every line in the block parses cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "min_doses": 2,
    "include_scheduled": True,
}

_HD = r"(?:[ \t]*\((?:HD|POD)#[^)]*\))?"
_DATE = rf"\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?{_HD}"
_TIME = r"(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d"
_STAMP = rf"(?:{_DATE}[ \t]+)?(?:{_TIME})"

_MAR_HEADER = re.compile(
    r"^[ \t]*(?:MAR|Medication[ \t]+Administration[ \t]+Record|"
    r"(?:PRN[ \t]+)?Medications?[ \t]+(?:Given|Administered)"
    r"(?:[ \t]*\([^)]*\))?)[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)

_UNITS = r"mcg|mg|g|units?|unit|mEq|mmol|mL|ml"
_ROUTES = (
    r"IV[ \t]+Push|IV[ \t]+Bolus|IVP|IVPB|IV|PO|by[ \t]+mouth|IM|"
    r"subcut(?:aneous(?:ly)?)?|SQ|SC|NG|GT|PEG|PR|SL|inhaled|INH|neb(?:ulized)?|topical"
)
_PRN_WORDS = r"PRN|as[ \t]+needed|Push|Bolus|IVP|breakthrough|one-time|once"
_STATUS_GIVEN = r"Given|Administered|Completed|Done|New[ \t]+Bag|Bag[ \t]+Change|Bolus[ \t]+Given"
_STATUS_HELD = r"Held|MAR[ \t]+Hold|Refused|Not[ \t]+Given|Missed|Deferred|Omitted"
_STATUS = rf"(?:{_STATUS_GIVEN}|{_STATUS_HELD})"

# Format 1: timestamp first ("10/02 01:15 fentanyl 25 mcg IV Push Given")
_STAMP_FIRST = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?P<stamp>{_STAMP})[ \t]*(?:[-–—:][ \t]*|[ \t]+)"
    rf"(?P<drug>[A-Za-z][A-Za-z0-9 /-]*?)[ \t]*(?:\([^)]*\)[ \t]*)?"
    rf"(?P<dose>\d+(?:\.\d+)?)[ \t]*(?P<unit>{_UNITS})\b"
    rf"(?:[ \t]+(?P<route>{_ROUTES})\b)?"
    rf"(?P<tail>[ \tA-Za-z0-9(),/-]*?)[ \t]*$",
    re.IGNORECASE,
)

# Format 2: drug first ("fentanyl 25 mcg IV Push 10/02 01:15 Given" or "... at 01:15 Given")
_DRUG_FIRST = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?P<drug>[A-Za-z][A-Za-z0-9 /-]*?)[ \t]*(?:\([^)]*\)[ \t]*)?"
    rf"(?P<dose>\d+(?:\.\d+)?)[ \t]*(?P<unit>{_UNITS})\b"
    rf"(?:[ \t]+(?P<route>{_ROUTES})\b)?"
    rf"(?P<mid>[ \tA-Za-z(),/-]*?)[ \t]+(?:(?:at|@)[ \t]*)?(?P<stamp>{_STAMP})"
    rf"(?P<tail>[ \tA-Za-z(),/-]*?)[ \t]*$",
    re.IGNORECASE,
)

# Format 3: single-line multi-dose ("fentanyl 25 mcg IV PRN: 01:15 (25 mcg), 03:40 (25 mcg)")
_INLINE_MULTI = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?P<drug>[A-Za-z][A-Za-z0-9 /-]*?)[ \t]*(?:\([^)]*\)[ \t]*)?"
    rf"(?:(?P<base_dose>\d+(?:\.\d+)?)[ \t]*(?P<base_unit>{_UNITS})\b[ \t]*)?"
    rf"(?:(?P<route>{_ROUTES})\b[ \t]*)?(?:(?P<prn>PRN)[ \t]*)?:[ \t]+"
    rf"(?P<list>.+)$",
    re.IGNORECASE,
)
_INLINE_ITEM = re.compile(
    rf"(?P<stamp>{_STAMP})[ \t]*\([ \t]*(?P<dose>\d+(?:\.\d+)?)[ \t]*(?P<unit>{_UNITS})"
    rf"(?:[ \t]+(?P<status>{_STATUS}))?[ \t]*\)",
    re.IGNORECASE,
)

_ALLOWED_TAIL_WORDS = re.compile(
    rf"\b(?:{_ROUTES}|{_PRN_WORDS}|{_STATUS}|scheduled|dose|pain|agitation|bp|sbp|"
    rf"hr|fever|nausea|anxiety|shivering|icp|seizure|for|q\d+h|daily|bid|tid|qid)\b"
    r"|[(),/:\-\s]",
    re.IGNORECASE,
)


@dataclass
class _Admin:
    raw: str
    drug: str
    drug_key: str
    dose: float
    unit: str
    route: str
    stamp: str
    held: bool
    prn: bool


def _fmt(val: float) -> str:
    return f"{val:g}"


def _norm_unit(u: str) -> str:
    low = u.lower()
    if low in ("unit", "units"):
        return "units"
    if low == "ml":
        return "mL"
    if low == "meq":
        return "mEq"
    return u


def _norm_route(r: str | None, extra: str = "") -> tuple[str, bool]:
    blob = f"{r or ''} {extra}".strip()
    low = " ".join(blob.split()).casefold()
    is_prn = bool(re.search(rf"\b(?:{_PRN_WORDS})\b", blob, re.IGNORECASE))
    if not r:
        m = re.search(rf"\b({_ROUTES})\b", extra, re.IGNORECASE)
        r = m.group(1) if m else ""
    r_low = " ".join((r or "").split()).casefold()
    if r_low in ("iv push", "iv bolus", "ivp", "ivpb", "iv"):
        return "IV", is_prn or r_low in ("iv push", "iv bolus", "ivp")
    if r_low in ("po", "by mouth"):
        return "PO", is_prn
    if r_low in ("subcut", "subcutaneous", "subcutaneously", "sq", "sc"):
        return "subcut", is_prn
    if r_low in ("inhaled", "inh", "neb", "nebulized"):
        return "INH", is_prn
    return (r.upper() if r and len(r) <= 4 else (r.lower() if r else "")), is_prn


def _clean_drug_name(raw_drug: str) -> tuple[str, str]:
    cleaned = " ".join(raw_drug.strip(" -–—:,").split())
    # Strip trailing route/action words if captured in drug name
    cleaned = re.sub(r"\s+(?:injection|tablet|capsule|solution|infusion|oral|iv)$", "",
                     cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        return "", ""
    display = cleaned[0].upper() + cleaned[1:].lower() if cleaned.isalpha() else cleaned
    return display, cleaned.casefold()


def _parse_tail_status(tail: str) -> tuple[bool, bool] | None:
    """Return (is_held, is_valid_tail) or None if unparseable text remains."""
    leftover = _ALLOWED_TAIL_WORDS.sub("", tail)
    if re.search(r"[A-Za-z0-9]", leftover):
        return None
    held = bool(re.search(rf"\b(?:{_STATUS_HELD})\b", tail, re.IGNORECASE))
    return held, True


def _parse_line(line: str) -> list[_Admin] | None:
    for rx in (_STAMP_FIRST, _DRUG_FIRST):
        m = rx.match(line)
        if not m:
            continue
        display, drug_key = _clean_drug_name(m.group("drug"))
        if not drug_key or drug_key in ("bp", "hr", "temp", "rr", "spo2", "icp", "cpp", "evd", "na", "gcs"):
            return None
        extra = (m.groupdict().get("mid") or "") + " " + (m.group("tail") or "")
        tail_check = _parse_tail_status(extra)
        if tail_check is None:
            return None
        held, _ = tail_check
        route, is_prn = _norm_route(m.group("route"), extra)
        unit = _norm_unit(m.group("unit"))
        dose = float(m.group("dose"))
        stamp = " ".join(m.group("stamp").split())
        return [_Admin(line, display, drug_key, dose, unit, route, stamp, held, is_prn)]

    m_inline = _INLINE_MULTI.match(line)
    if m_inline:
        display, drug_key = _clean_drug_name(m_inline.group("drug"))
        if not drug_key:
            return None
        rest = m_inline.group("list")
        items = list(_INLINE_ITEM.finditer(rest))
        if len(items) < 2:
            return None
        leftover = _INLINE_ITEM.sub("", rest)
        if re.search(r"[A-Za-z0-9]", leftover):
            return None
        route, is_prn = _norm_route(m_inline.group("route"), m_inline.group("prn") or "PRN")
        out: list[_Admin] = []
        for it in items:
            held = bool(it.group("status") and re.search(_STATUS_HELD, it.group("status"), re.IGNORECASE))
            out.append(_Admin(
                line, display, drug_key, float(it.group("dose")),
                _norm_unit(it.group("unit")), route, " ".join(it.group("stamp").split()),
                held, is_prn or True,
            ))
        return out
    return None


def _summarize_group(entries: list[_Admin]) -> str:
    first = entries[0]
    given = [e for e in entries if not e.held]
    held_count = sum(1 for e in entries if e.held)
    route_part = f" {first.route}" if first.route else ""
    head = f"{first.drug}{route_part}"
    unit = first.unit
    stamps = [e.stamp for e in given if e.stamp] or [e.stamp for e in entries if e.stamp]
    window = f"{stamps[0]}–{stamps[-1]}" if len(stamps) >= 2 and stamps[0] != stamps[-1] else (stamps[0] if stamps else "")

    if not given:
        held_str = f"{held_count} held ({window})" if window else f"{held_count} held"
        return f"{head}: 0 {unit} given ({held_str})"

    doses = [e.dose for e in given]
    total = sum(doses)
    n = len(doses)
    if len(set(doses)) == 1:
        breakdown = f"{n} × {_fmt(doses[0])} {unit}" if n > 1 else f"{_fmt(doses[0])} {unit}"
    else:
        breakdown = ", ".join(_fmt(d) for d in doses) + f" {unit}"

    last = given[-1]
    last_part = f"last {_fmt(last.dose)} {unit}" + (f" at {last.stamp}" if last.stamp else "")
    meta_parts = [breakdown]
    if window:
        meta_parts.append(window)
    meta_parts.append(last_part)
    if held_count:
        meta_parts.append(f"{held_count} held")
    dose_word = "dose" if n == 1 else "doses"
    return f"{head}: {_fmt(total)} {unit} total over {n} {dose_word} ({'; '.join(meta_parts)})"


_CANDIDATE_MAR_LINE = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?{_STAMP}[ \t]+[^\n]*\b(?:{_UNITS})\b",
    re.IGNORECASE,
)


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("mar_summary") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    try:
        min_doses = max(2, int(options.get("min_doses") or 2))
    except (TypeError, ValueError):
        min_doses = 2
    include_scheduled = bool(options.get("include_scheduled", True))

    lines = text.split("\n")
    out: list[str] = []
    blocks = 0
    doses_condensed = 0
    i = 0

    while i < len(lines):
        has_header = bool(_MAR_HEADER.match(lines[i]))
        start = i + 1 if has_header else i
        j = start
        parsed_rows: list[list[_Admin]] = []
        bad = False
        while j < len(lines):
            adm = _parse_line(lines[j])
            if adm is not None:
                parsed_rows.append(adm)
                j += 1
            elif _CANDIDATE_MAR_LINE.match(lines[j]):
                bad = True
                break
            else:
                break

        flat = [a for row in parsed_rows for a in row]
        if not bad and flat:
            eligible = [a for a in flat if include_scheduled or a.prn]
            by_key: dict[tuple[str, str, str], list[_Admin]] = {}
            for a in eligible:
                by_key.setdefault((a.drug_key, a.route, a.unit), []).append(a)
            condensable = {k for k, grp in by_key.items() if len(grp) >= min_doses}

            if condensable:
                if has_header:
                    out.append(lines[i])
                emitted_keys: set[tuple[str, str, str]] = set()
                for row in parsed_rows:
                    first = row[0]
                    key = (first.drug_key, first.route, first.unit)
                    if (include_scheduled or first.prn) and key in condensable:
                        if key not in emitted_keys:
                            emitted_keys.add(key)
                            grp = by_key[key]
                            out.append(_summarize_group(grp))
                            doses_condensed += len(grp)
                    else:
                        out.append(first.raw)
                blocks += 1
                i = j
                continue

        out.append(lines[i])
        i += 1

    return "\n".join(out), blocks, {"blocks": blocks, "doses_condensed": doses_condensed}
