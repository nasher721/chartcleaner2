"""Condense Neuro ICU flowsheets (stage ``neuro_summary``).

Four independent parts, each with its own switch (all on once the stage is):

``neuro_checks``
    rows of GCS / GCS-E/V/M / RASS / NIHSS / FOUR / CAM-ICU / pupils with two
    or more readings each (optionally under a row of dates/times)::

        GCS: 14 15 15 13
        Pupils: 3 brisk  3 brisk  3 sluggish  3 sluggish

    → ``Neuro checks (4 readings): GCS 13–15 (last 13); Pupils 3 brisk → 3
    sluggish (last 3 sluggish)``

``evd``
    consecutive EVD lines — output rows (``EVD output: 10 12 8 15``) or timed
    hourly outputs (``0800 EVD output 10 mL``), the level (``EVD at 10 cm
    H2O``), open/clamped status — plus ICP/CPP rows →
    ``EVD: 10 cm H2O, open; output 45 mL over 4 readings (10, 12, 8, 15);
    ICP 8–22 (last 22; 1 reading >20)``

``sodium``
    three or more serial sodium lines (``0400 Na 141``, ``1000: 143 mmol/L``),
    optionally under a "Sodium checks" header →
    ``Na checks 0400–1600: 141 → 143 → 145 (3 checks, +4)``

``drips``
    three or more titration lines of one infusion in one unit
    (``0600 niCARdipine 5 mg/hr Rate Change``) →
    ``Nicardipine drip 5–10 mg/hr over 4 entries (start 5, last 7.5 mg/hr)``

As with the other condensers a block is rewritten only when **every** line in
it parses; anything unusual stays exactly as written. ``icp_threshold``
(default 20 mmHg) sets which ICP readings are counted as high.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "neuro_checks": True, "evd": True, "sodium": True,
                            "drips": True, "icp_threshold": 20}
PARTS = ("neuro_checks", "evd", "sodium", "drips")

_HD = r"(?:[ \t]*\((?:HD|POD)#[^)]*\))?"  # tolerate hospital-day labels added earlier
_DATE = rf"\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?{_HD}"
_TIME = r"\d{1,2}:?\d{2}"
_DATE_ROW = re.compile(rf"^[ \t]*(?:(?:{_DATE}|{_TIME})(?:[ \t]+{_TIME})?[ \t]*){{2,}}$")
_STAMP = rf"(?:{_DATE}[ \t]+)?(?:{_TIME})"
_NUM = r"-?\d+(?:\.\d+)?"


def _fmt(value: float) -> str:
    return f"{value:g}"


def _span(values: list[float]) -> str:
    lo, hi = min(values), max(values)
    return f"{_fmt(lo)}–{_fmt(hi)}" if lo != hi else _fmt(lo)


# --- neuro checks ---------------------------------------------------------------

NEURO_LABELS = {
    "gcs": "GCS", "gcs total": "GCS", "glasgow coma scale": "GCS", "glasgow coma scale score": "GCS",
    "gcs eye": "GCS-E", "eye opening": "GCS-E", "gcs e": "GCS-E",
    "gcs verbal": "GCS-V", "verbal response": "GCS-V", "best verbal response": "GCS-V", "gcs v": "GCS-V",
    "gcs motor": "GCS-M", "motor response": "GCS-M", "best motor response": "GCS-M", "gcs m": "GCS-M",
    "rass": "RASS", "nihss": "NIHSS", "four score": "FOUR", "four": "FOUR",
    "cam-icu": "CAM-ICU", "cam icu": "CAM-ICU",
    "pupils": "Pupils", "pupil": "Pupils", "pupil size/reaction": "Pupils",
    "left pupil": "L pupil", "right pupil": "R pupil", "l pupil": "L pupil", "r pupil": "R pupil",
}
_ROW = re.compile(r"^[ \t]*(?P<label>[A-Za-z][A-Za-z /-]*?)[ \t]*:?[ \t]+"
                  r"(?P<rest>(?:-?\d|neg|pos|uta|unable)\S*(?:[ \t].*)?)$", re.IGNORECASE)
_PUPIL = re.compile(r"(?P<size>\d(?:\.\d)?)[ \t]*(?:mm)?(?:[ \t]*/[ \t]*(?P<size2>\d(?:\.\d)?)[ \t]*(?:mm)?)?"
                    r"[ \t]*(?P<react>brisk|sluggish|reactive|non-?reactive|nr|fixed)?(?![A-Za-z])",
                    re.IGNORECASE)
_CAM = re.compile(r"\b(neg(?:ative)?|pos(?:itive)?|uta|unable to assess)\b", re.IGNORECASE)
_NUMS = re.compile(rf"(?<![\w.]){_NUM}(?![\w.])")


def _split_readings(rest: str) -> list[str]:
    return [r for r in re.split(r"\t+|[ \t]{2,}|[ \t]*[,;|][ \t]*", rest.strip()) if r]


def _neuro_row(line: str):
    m = _ROW.match(line)
    if not m:
        return None
    short = NEURO_LABELS.get(" ".join(m.group("label").split()).casefold())
    if not short:
        return None
    rest = m.group("rest")
    if short in ("Pupils", "L pupil", "R pupil"):
        cells = _split_readings(rest)
        if len(cells) < 2:
            return None
        values = []
        for cell in cells:
            p = _PUPIL.fullmatch(cell.strip())
            if not p:
                return None
            values.append(" ".join(cell.split()))
        return short, values
    if short == "CAM-ICU":
        found = [x.lower()[:3] for x in _CAM.findall(rest)]
        if len(found) < 2 or re.search(r"[A-Za-z0-9]", _CAM.sub("", rest)):
            return None
        return short, ["UTA" if f in ("uta", "una") else f for f in found]
    found = [float(x) for x in _NUMS.findall(rest)]
    if len(found) < 2 or re.search(r"[A-Za-z0-9]", _NUMS.sub("", rest)):
        return None
    return short, found


def _changes(values: list[str]) -> str:
    seq = [values[0]] + [v for prev, v in zip(values, values[1:]) if v != prev]
    if len(seq) == 1:
        return f"{seq[0]} (unchanged)"
    return " → ".join(seq) + f" (last {values[-1]})"


def _neuro_summary(short: str, values: list) -> str:
    if values and isinstance(values[0], str):
        return f"{short} {_changes(values)}"
    return f"{short} {_span(values)} (last {_fmt(values[-1])})"


_NEURO_HEADER = re.compile(r"^[ \t]*(?:neuro(?:logic(?:al)?)?[ \t]+(?:checks?|assessment|exam)|neuro)[ \t]*:?[ \t]*$",
                           re.IGNORECASE)


def _neuro_block(lines: list[str], i: int) -> tuple[str, int] | None:
    start = i + 1 if _DATE_ROW.match(lines[i]) else i
    rows, j = [], start
    while j < len(lines) and (row := _neuro_row(lines[j])):
        rows.append(row)
        j += 1
    readings = max((len(v) for _s, v in rows), default=0)
    if not rows or (len(rows) < 2 and readings < 3):
        return None
    return (f"Neuro checks ({readings} readings): "
            + "; ".join(_neuro_summary(s, v) for s, v in rows)), j


# --- EVD / ICP --------------------------------------------------------------------

_EVD_OUTPUT_ROW = re.compile(
    r"^[ \t]*EVD[ \t]+(?:output|drainage|drain(?:ed)?|out)[ \t]*(?:\(mL\))?[ \t]*:?[ \t]+(?P<rest>[\d.\s,mLl]+)$",
    re.IGNORECASE)
_EVD_OUTPUT_TIMED = re.compile(
    rf"^[ \t]*(?P<stamp>{_STAMP})[ \t]*:?[ \t]+EVD[ \t]+(?:output|drainage|drained|out)[ \t]*:?[ \t]*"
    r"(?P<value>\d+(?:\.\d+)?)[ \t]*mL[ \t]*$", re.IGNORECASE)
_EVD_LEVEL = re.compile(
    r"^[ \t]*EVD[ \t]+(?:(?:level(?:ed)?|set|height)[ \t]*(?:at|to)?|at|@)[ \t]*:?[ \t]*"
    r"(?P<level>\d+(?:\.\d+)?)[ \t]*cm[ \t]*H2O[ \t]*(?:above[ \t]+(?:the[ \t]+)?(?:tragus|EAM|foramen))?"
    r"[ \t]*[,;]?[ \t]*(?P<status>open|clamped|closed)?[ \t]*\.?[ \t]*$", re.IGNORECASE)
_EVD_STATUS = re.compile(r"^[ \t]*EVD[ \t]+(?:is[ \t]+)?(?P<status>open|clamped|closed)[ \t]*\.?[ \t]*$",
                         re.IGNORECASE)
_PRESSURE_ROW = re.compile(r"^[ \t]*(?P<label>ICP|CPP)[ \t]*(?:\(mmHg\))?[ \t]*:?[ \t]+(?P<rest>[-\d.\s,]+)$",
                           re.IGNORECASE)


def _evd_line(line: str) -> tuple[str, Any] | None:
    if m := _EVD_OUTPUT_ROW.match(line):
        values = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", m.group("rest"))]
        if len(values) >= 2 and not re.search(r"[A-Za-z]", re.sub(r"\bml\b", "", m.group("rest"), flags=re.I)):
            return "output", values
        return None
    if m := _EVD_OUTPUT_TIMED.match(line):
        return "output1", (m.group("stamp").strip(), float(m.group("value")))
    if m := _EVD_LEVEL.match(line):
        return "level", (m.group("level"), (m.group("status") or "").lower())
    if m := _EVD_STATUS.match(line):
        return "status", m.group("status").lower()
    if m := _PRESSURE_ROW.match(line):
        values = [float(x) for x in _NUMS.findall(m.group("rest"))]
        if values:
            return m.group("label").upper(), values
    return None


def _evd_block(lines: list[str], i: int, threshold: float) -> tuple[str, int] | None:
    items, j = [], i
    while j < len(lines) and (item := _evd_line(lines[j])):
        items.append(item)
        j += 1
    kinds = [k for k, _ in items]
    has_output = "output" in kinds or kinds.count("output1") >= 2
    if not items or not has_output or len(items) < 2 and "output" not in kinds:
        return None
    level = next((v for k, v in items if k == "level"), None)
    status = next((v for k, v in items if k == "status"), None) or (level[1] if level else "")
    outputs = [x for k, v in items if k == "output" for x in v] + [v[1] for k, v in items if k == "output1"]
    stamps = [v[0] for k, v in items if k == "output1"]
    parts = []
    head = ", ".join(x for x in ((f"{level[0]} cm H2O" if level else ""), status) if x)
    if head:
        parts.append(head)
    window = f" {stamps[0]}–{stamps[-1]}" if len(stamps) >= 2 else ""
    parts.append(f"output {_fmt(sum(outputs))} mL{window} over {len(outputs)} readings "
                 f"({', '.join(_fmt(x) for x in outputs)})")
    for label in ("ICP", "CPP"):
        values = [x for k, v in items if k == label for x in v]
        if not values:
            continue
        text = f"{label} {_span(values)} (last {_fmt(values[-1])}"
        if label == "ICP":
            high = sum(v > threshold for v in values)
            if high:
                text += f"; {high} reading{'s' if high > 1 else ''} >{_fmt(threshold)}"
        parts.append(text + ")")
    return "EVD: " + "; ".join(parts), j


# --- serial sodium -----------------------------------------------------------------

_NA_HEADER = re.compile(r"^[ \t]*(?:serial[ \t]+)?(?:sodium|na)[ \t]+(?:checks?|levels?|q\d+h?)[ \t]*:?[ \t]*$"
                        r"|^[ \t]*(?:q\d+h?[ \t]+)?(?:sodium|na)[ \t]+checks?[ \t]*:?[ \t]*$", re.IGNORECASE)
_NA_LINE = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?:(?P<stamp>{_STAMP})[ \t]*:?[ \t]+)?(?:(?:serum[ \t]+)?(?:sodium|na)[ \t]*:?[ \t]*)?"
    r"(?P<value>1[0-8]\d)[ \t]*(?:mmol/L|mEq/L)?[ \t]*(?:\(?[HL]\)?|\*)?[ \t]*"
    rf"(?:(?:at|@)[ \t]*(?P<stamp2>{_STAMP}))?[ \t]*$", re.IGNORECASE)


def _sodium_block(lines: list[str], i: int) -> tuple[str, int] | None:
    header = bool(_NA_HEADER.match(lines[i]))
    j = i + 1 if header else i
    values, stamps = [], []
    while j < len(lines) and (m := _NA_LINE.match(lines[j])):
        named = re.search(r"sodium|na", lines[j], re.IGNORECASE)
        stamp = m.group("stamp") or m.group("stamp2")
        if not header and not named:
            break  # bare "141" lines only count under a sodium header
        if not stamp and not named:
            break
        values.append(int(m.group("value")))
        if stamp:
            stamps.append(stamp.strip())
        j += 1
    if len(values) < 3:
        return None
    window = f" {stamps[0]}–{stamps[-1]}" if len(stamps) == len(values) else ""
    delta = values[-1] - values[0]
    return (f"Na checks{window}: " + " → ".join(map(str, values))
            + f" ({len(values)} checks, {delta:+d})"), j


# --- drip titrations ---------------------------------------------------------------

_RATE_UNIT = r"(?:mcg/kg/min|mcg/kg/hr?|mg/kg/hr?|mcg/min|mcg/hr?|mg/hr?|mg/min|units/kg/hr?|units/hr?|units/min|mL/hr?)"
_ACTION = (r"(?:rate[ \t]*(?:/[ \t]*dose[ \t]*)?(?:change|verify|verified|changed)|new[ \t]+bag|restarted|started|"
           r"stopped|paused|held|titrated(?:[ \t]+(?:up|down))?|increased|decreased|off|continued)")
_DRIP = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?:(?P<stamp>{_STAMP})[ \t]*:?[ \t]+)?(?P<drug>[A-Za-z][A-Za-z-]{{3,}})"
    rf"(?:[ \t]+(?:drip|infusion|gtt))?[ \t]*(?:\([^)]*\))?[ \t]*[:,-]?[ \t]*(?P<pre>{_ACTION})?[ \t]*(?:to[ \t]*)?"
    rf"(?P<rate>\d+(?:\.\d+)?)[ \t]*(?P<unit>{_RATE_UNIT})(?![A-Za-z/])[ \t]*[,;-]?[ \t]*(?P<post>{_ACTION})?[ \t]*\.?[ \t]*$",
    re.IGNORECASE)


def _drip_block(lines: list[str], i: int) -> tuple[str, int] | None:
    rows, j = [], i
    while j < len(lines) and (m := _DRIP.match(lines[j])):
        if rows and (m.group("drug").lower() != rows[0].group("drug").lower()
                     or m.group("unit").lower() != rows[0].group("unit").lower()):
            break
        rows.append(m)
        j += 1
    if len(rows) < 3:
        return None
    rates = [float(m.group("rate")) for m in rows]
    unit = rows[0].group("unit")
    drug = rows[0].group("drug")
    drug = drug[0].upper() + drug[1:].lower()
    last_action = " ".join(filter(None, (rows[-1].group("pre"), rows[-1].group("post")))).lower()
    stopped = any(w in last_action for w in ("stopped", "off", "paused", "held"))
    stamps = [m.group("stamp").strip() for m in rows if m.group("stamp")]
    window = f" {stamps[0]}–{stamps[-1]}" if len(stamps) == len(rows) else ""
    text = (f"{drug} drip{window}: {_span(rates)} {unit} over {len(rows)} entries "
            f"(start {_fmt(rates[0])}, last {_fmt(rates[-1])} {unit}")
    return text + ("; stopped)" if stopped else ")"), j


# --- stage -------------------------------------------------------------------------

def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("neuro_summary") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    threshold = float(options.get("icp_threshold") or 20)
    finders = []
    if options.get("neuro_checks"):
        finders.append(("neuro_checks", _neuro_block))
    if options.get("evd"):
        finders.append(("evd", lambda ls, k: _evd_block(ls, k, threshold)))
    if options.get("sodium"):
        finders.append(("sodium", _sodium_block))
    if options.get("drips"):
        finders.append(("drips", _drip_block))
    lines = text.split("\n")
    out: list[str] = []
    counts = {name: 0 for name, _ in finders}
    i = 0
    while i < len(lines):
        for name, find in finders:
            found = find(lines, i)
            if found:
                indent = re.match(r"[ \t]*", lines[i]).group(0)
                summary, i = found
                if name == "neuro_checks" and out and _NEURO_HEADER.match(out[-1]):
                    out.pop()  # "Neuro checks" header + "Neuro checks (…)": keep one
                out.append(indent + summary)
                counts[name] += 1
                break
        else:
            out.append(lines[i])
            i += 1
    total = sum(counts.values())
    if not total:
        return text, 0, {}
    return "\n".join(out), total, {k: v for k, v in counts.items() if v}
