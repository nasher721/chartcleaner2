"""Summarize vitals flowsheets and intake/output (stage ``vitals_summary``).

A vitals flowsheet is two or more consecutive rows that each start with a
known vital sign and carry two or more readings, optionally under a header
row of dates/times::

    BP: 132/78 141/82 118/70
    Pulse: 88 104 92
    Temp: 37.1 °C (98.8 °F) 38.4 °C (101.1 °F)

becomes ``Vitals (3 readings): BP 118/70–141/82 (last 118/70); HR 88–104
(last 92); T 37.1–38.4 °C (last 38.4)``. Rows with a single reading (the
usual one-line vitals) are left alone. An Intake / Output / Net block in mL
becomes ``I/O: 2,450 in / 1,800 out (net +650 mL)``.
A row whose readings can't all be parsed keeps the whole block as written.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "window_hours": 0}

VITALS = {  # label spellings -> short name
    "bp": "BP", "blood pressure": "BP", "nibp": "BP", "art bp": "Art BP", "abp": "Art BP",
    "pulse": "HR", "heart rate": "HR", "hr": "HR",
    "temp": "T", "temperature": "T", "tmax": "Tmax",
    "resp": "RR", "respiratory rate": "RR", "rr": "RR",
    "spo2": "SpO2", "o2 sat": "SpO2", "sao2": "SpO2", "pulse ox": "SpO2",
    "map": "MAP", "icp": "ICP", "cpp": "CPP", "cvp": "CVP",
}
_LABEL = re.compile(r"^[ \t]*(?P<label>[A-Za-z][A-Za-z0-9 ]*?)[ \t]*:?[ \t]+(?P<rest>\S.*)$")
_BP = re.compile(r"\b(\d{2,3})/(\d{2,3})\b")
_TEMP_C = re.compile(r"(\d{2}(?:\.\d)?)\s*°?\s*C\b")
_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%?(?![\d./])")
_HD = r"(?:[ \t]*\((?:HD|POD)#[^)]*\))?"  # tolerate hospital-day labels added earlier
_DATE_ROW = re.compile(rf"^[ \t]*(?:\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?{_HD}(?:[ \t]+\d{{3,4}})?[ \t]*){{2,}}$")
_IO = re.compile(r"^[ \t]*(?P<kind>Intake|Output|Net)[ \t:]+(?P<sign>[-+]?)(?P<value>\d[\d,]*)\s*(?:ml|mL)\b",
                 re.IGNORECASE)
# one header column: a date with an optional clock time ("10/01", "10/01/2026",
# "10/01 0800", "10/01 08:00"). A hospital-day label after the date is ignored.
_DATE_COL = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?:[ \t]+(\d{3,4}|\d{1,2}:\d{2}))?")


def _column_times(header: str) -> list[datetime] | None:
    """One datetime per header column, or None when any column won't parse."""
    cols = _DATE_COL.findall(header)
    if len(cols) < 2:
        return None
    years = [int(y) if y else 0 for _, _, y, _ in cols]
    default_year = max(years) if max(years) > 0 else 2000
    out: list[datetime] = []
    for month, day, year, clock in cols:
        try:
            y = int(year) if year else default_year
            y += 2000 if y < 100 else 0
            hh, mm = 0, 0
            if clock:
                digits = clock.replace(":", "")
                hh, mm = (int(digits[:-2]), int(digits[-2:])) if len(digits) > 2 else (int(digits), 0)
            out.append(datetime(y, int(month), int(day), hh, mm))
        except ValueError:
            return None
    return out


def _window(values: list, times: list[datetime], hours: float) -> list:
    """The trailing values inside ``hours`` of the latest column (all, if none)."""
    latest = max(times)
    keep = [i for i, t in enumerate(times)
            if (latest - t).total_seconds() <= hours * 3600 + 1]
    return [v for i, v in enumerate(values) if i in keep] if keep else values


def _readings(short: str, rest: str) -> list[float] | list[tuple[int, int]] | None:
    if short in ("BP", "Art BP"):
        found = [(int(a), int(b)) for a, b in _BP.findall(rest)]
        leftover = _BP.sub("", rest)
    elif short in ("T", "Tmax"):
        found = [float(x) for x in _TEMP_C.findall(rest)]
        leftover = re.sub(r"\(\s*\d+(?:\.\d+)?\s*°?\s*F\s*\)", "", _TEMP_C.sub("", rest))
    else:
        found = [float(x) for x in _NUM.findall(rest)]
        leftover = _NUM.sub("", rest)
    if re.search(r"[A-Za-z0-9]", leftover.replace("°", "")):
        return None  # something we don't understand: leave the block alone
    return found


def _fmt(value: float) -> str:
    return f"{value:g}"


def _summary(short: str, values: list) -> str:
    if short in ("BP", "Art BP"):
        lo, hi = min(values, key=lambda v: v[0]), max(values, key=lambda v: v[0])
        last = values[-1]
        span = f"{lo[0]}/{lo[1]}–{hi[0]}/{hi[1]}" if lo != hi else f"{lo[0]}/{lo[1]}"
        return f"{short} {span} (last {last[0]}/{last[1]})"
    unit = " °C" if short in ("T", "Tmax") else ("%" if short == "SpO2" else "")
    lo, hi = min(values), max(values)
    span = f"{_fmt(lo)}–{_fmt(hi)}" if lo != hi else _fmt(lo)
    return f"{short} {span}{unit} (last {_fmt(values[-1])})"


def _vitals_row(line: str):
    m = _LABEL.match(line)
    if not m:
        return None
    short = VITALS.get(" ".join(m.group("label").split()).casefold())
    if not short:
        return None
    values = _readings(short, m.group("rest"))
    if not values or len(values) < 2:
        return None
    return short, values


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("vitals_summary") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    try:
        window_hours = float(options.get("window_hours") or 0)
    except (TypeError, ValueError):
        window_hours = 0
    lines = text.split("\n")
    out: list[str] = []
    blocks = 0
    i = 0
    while i < len(lines):
        header = lines[i] if _DATE_ROW.match(lines[i]) else None
        start = i + 1 if header else i
        rows, j = [], start
        while j < len(lines) and (row := _vitals_row(lines[j])):
            rows.append(row)
            j += 1
        if len(rows) >= 2:
            readings = max(len(v) for _s, v in rows)
            label = f"Vitals ({readings} readings)"
            # With window_hours, keep only the trailing readings inside the
            # window (parsed from the header date row). Anything unparseable
            # falls back to the full min/max/last summary.
            if window_hours > 0 and header:
                times = _column_times(header)
                if times and all(len(v) == len(times) for _s, v in rows):
                    sliced = [(s, _window(v, times, window_hours)) for s, v in rows]
                    if all(len(v) >= 2 for _s, v in sliced):
                        kept = max(len(v) for _s, v in sliced)
                        rows = sliced
                        readings = kept
                        label = (f"Vitals ({kept} of {len(times)} readings, "
                                 f"last {window_hours:g}h)")
            out.append(label + ": " + "; ".join(_summary(s, v) for s, v in rows))
            blocks += 1
            i = j
            continue
        io = {}
        j = i
        while j < len(lines) and (m := _IO.match(lines[j])):
            io[m.group("kind").casefold()] = int(m.group("value").replace(",", "")) * (
                -1 if m.group("sign") == "-" else 1)
            j += 1
        if {"intake", "output"} <= set(io):
            net = io.get("net", io["intake"] - io["output"])
            out.append(f"I/O: {io['intake']:,} in / {io['output']:,} out (net {net:+,} mL)")
            blocks += 1
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), blocks, {"blocks": blocks}
