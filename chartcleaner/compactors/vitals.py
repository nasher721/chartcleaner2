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
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False}

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
    lines = text.split("\n")
    out: list[str] = []
    blocks = 0
    i = 0
    while i < len(lines):
        start = i + 1 if _DATE_ROW.match(lines[i]) else i
        rows, j = [], start
        while j < len(lines) and (row := _vitals_row(lines[j])):
            rows.append(row)
            j += 1
        if len(rows) >= 2:
            readings = max(len(v) for _s, v in rows)
            out.append(f"Vitals ({readings} readings): " + "; ".join(_summary(s, v) for s, v in rows))
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
