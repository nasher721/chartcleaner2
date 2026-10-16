"""De-tabularize multi-column clinical flowsheets into prose series (stage ``flowsheet_tables``).

Recognizes two common Epic multi-column grid orientations:

1. **Timestamps across columns** (header row of 2+ dates/times, followed by
   2+ parameter rows)::

       Ventilator   10/02 0200   10/02 0600   10/02 1000
       Mode         AC/VC        AC/VC        PSV
       FiO2         60%          40%          35%
       PEEP         8            5            5

   becomes::

       Ventilator (10/02 0200 → 10/02 1000, 3 readings): Mode AC/VC → AC/VC → PSV (last PSV); FiO2 60% → 40% → 35% (last 35%); PEEP 8 → 5 → 5 (last 5)

2. **Timestamps down rows** (header row of ``Time``/``Date`` + 2+ column names,
   followed by 2+ timestamped rows)::

       Time         pH     pCO2   pO2    HCO3
       10/02 0200   7.31   48     88     24
       10/02 0600   7.38   40     95     23

   becomes::

       Flowsheet (10/02 0200 → 10/02 0600, 2 readings): pH 7.31 → 7.38 (last 7.38); pCO2 48 → 40 (last 40); pO2 88 → 95 (last 95); HCO3 24 → 23 (last 23)

Safety rule: a grid is rewritten **only** when every row in the contiguous block
has the exact column count of the header and every cell is a short clinical
value (number with optional unit/flag, respiratory/device mode token, or ``--``
placeholder). Any prose cell leaves the entire block untouched.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "style": "series",
    "collapse_unchanged": True,
}

_HD = r"(?:[ \t]*\((?:HD|POD)#[^)]*\))?"
_DATE = rf"\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?{_HD}"
_TIME = r"(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d"
_STAMP_TOKEN = rf"(?:{_DATE}(?:[ \t]+(?:{_TIME}))?|{_TIME})"
_STAMP_RE = re.compile(rf"^(?P<stamp>{_STAMP_TOKEN})$")

_CORNER_GENERIC = frozenset({
    "", "time", "date", "date/time", "datetime", "parameter", "parameters",
    "component", "flowsheet", "value", "values",
})

_PLACEHOLDERS = frozenset({"-", "--", "---", "n/a", "na", "none", ""})

# Valid cell: numeric with optional unit/flag/ratio, BP/split value, or short clinical mode token
_CELL_NUM_RE = re.compile(
    r"^[<>+-]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?"
    r"(?:[ \t]*(?:%|°[CF]|cmH2O|mmHg|mL/kg|mL/hr|mL|L/min|L|bpm|mcg/kg/min|mcg/min|mg/hr|units/hr|mEq/L|mmol/L|g/dL))?"
    r"(?:[ \t]*(?:\(?[HL*]+\)?))?$",
    re.IGNORECASE,
)
_CELL_TOKEN_RE = re.compile(
    r"^(?:AC/VC|AC/PC|VC-AC|PC-AC|SIMV|PSV|CPAP|PRVC|APRV|BiPAP|BIPAP|HFNC|NC|RA|"
    r"Room[ \t]+Air|Ventimask|NRB|Trach|ETT|Open|Clamped|Clear|Intact|Absent|Present|"
    r"Pos(?:itive)?|Neg(?:ative)?|Yes|No|[+-]\d)$",
    re.IGNORECASE,
)
_PURE_NUM_RE = re.compile(
    r"^(?P<num>[+-]?\d+(?:\.\d+)?)(?P<unit>[ \t]*(?:%|°[CF]|cmH2O|mmHg|mL|L/min|L|bpm)?)$",
    re.IGNORECASE,
)

_COL_SPLIT = re.compile(r"\t+|[ \t]{2,}")


def _fmt(v: float) -> str:
    return f"{v:g}"


def _is_valid_cell(cell: str) -> bool:
    c = cell.strip()
    if c.casefold() in _PLACEHOLDERS:
        return True
    if len(c) > 24:
        return False
    return bool(_CELL_NUM_RE.match(c) or _CELL_TOKEN_RE.match(c))


def _is_valid_param_name(name: str) -> bool:
    s = name.strip(" :")
    if not s or len(s) > 32:
        return False
    if _STAMP_RE.match(s):
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9 /()_%+-]*$", s))


def _parse_time_across_header(line: str) -> tuple[str, list[str]] | None:
    """Return (title_or_empty, [stamp1, stamp2, ...]) when line is a timestamp header."""
    parts = [p.strip() for p in _COL_SPLIT.split(line.strip()) if p.strip()]
    if len(parts) >= 2 and all(_STAMP_RE.match(p) for p in parts):
        return "", parts
    if len(parts) >= 3 and _is_valid_param_name(parts[0]) and all(_STAMP_RE.match(p) for p in parts[1:]):
        corner = parts[0].strip(" :")
        title = "" if corner.casefold() in _CORNER_GENERIC else corner
        return title, parts[1:]
    # Fallback: single-space separated bare timestamps on a header line
    tokens = list(re.finditer(_STAMP_TOKEN, line))
    if len(tokens) >= 2:
        prefix = line[:tokens[0].start()].strip(" :\t")
        rest_after = line[tokens[0].start():]
        leftover = re.sub(_STAMP_TOKEN, "", rest_after).strip()
        if not leftover:
            if not prefix:
                return "", [m.group(0).strip() for m in tokens]
            if _is_valid_param_name(prefix):
                title = "" if prefix.casefold() in _CORNER_GENERIC else prefix
                return title, [m.group(0).strip() for m in tokens]
    return None


def _parse_param_row(line: str, ncols: int) -> tuple[str, list[str]] | None:
    parts = [p.strip() for p in _COL_SPLIT.split(line.strip()) if p.strip()]
    if len(parts) != ncols + 1:
        # Try colon or single-space split when there's a clear label
        m = re.match(r"^[ \t]*(?P<name>[A-Za-z][A-Za-z0-9 /()_-]*?)[ \t]*:[ \t]+(?P<rest>\S.*)$", line)
        if not m:
            return None
        name = m.group("name").strip()
        cells = [c.strip() for c in m.group("rest").split() if c.strip()]
        if len(cells) != ncols:
            return None
        parts = [name, *cells]
    param = parts[0].strip(" :")
    cells = parts[1:]
    if not _is_valid_param_name(param):
        return None
    if not all(_is_valid_cell(c) for c in cells):
        return None
    if all(c.casefold() in _PLACEHOLDERS for c in cells):
        return None
    return param, cells


def _parse_time_down_header(line: str) -> tuple[str, list[str]] | None:
    """Return (title_or_empty, [param1, param2, ...]) for a transposed flowsheet."""
    parts = [p.strip() for p in _COL_SPLIT.split(line.strip()) if p.strip()]
    if len(parts) < 3:
        return None
    corner = parts[0].strip(" :")
    if corner.casefold() not in {"time", "date", "date/time", "datetime"}:
        return None
    params = [p.strip(" :") for p in parts[1:]]
    if not all(_is_valid_param_name(p) for p in params):
        return None
    return "", params


def _parse_time_down_row(line: str, ncols: int) -> tuple[str, list[str]] | None:
    parts = [p.strip() for p in _COL_SPLIT.split(line.strip()) if p.strip()]
    if len(parts) != ncols + 1:
        m = re.match(rf"^[ \t]*(?P<stamp>{_STAMP_TOKEN})[ \t]+(?P<rest>\S.*)$", line)
        if not m:
            return None
        stamp = m.group("stamp").strip()
        cells = [c.strip() for c in m.group("rest").split() if c.strip()]
        if len(cells) != ncols:
            return None
        parts = [stamp, *cells]
    stamp = parts[0]
    cells = parts[1:]
    if not _STAMP_RE.match(stamp):
        return None
    if not all(_is_valid_cell(c) for c in cells):
        return None
    return stamp, cells


def _format_param(param: str, raw_cells: list[str], style: str, collapse_unchanged: bool) -> str:
    vals = [c.strip() for c in raw_cells if c.strip().casefold() not in _PLACEHOLDERS]
    if not vals:
        return f"{param} --"
    if len(vals) == 1:
        return f"{param} {vals[0]}"
    if collapse_unchanged and len(set(v.casefold() for v in vals)) == 1:
        return f"{param} {vals[0]} (unchanged)"
    if style == "range" and len(vals) >= 3:
        nums = [_PURE_NUM_RE.match(v) for v in vals]
        if all(nums):
            units = {m.group("unit").strip() for m in nums if m}
            if len(units) <= 1:
                unit = next(iter(units), "")
                fvals = [float(m.group("num")) for m in nums if m]
                lo, hi = min(fvals), max(fvals)
                span = f"{_fmt(lo)}–{_fmt(hi)}" if lo != hi else _fmt(lo)
                return f"{param} {span}{unit} (last {vals[-1]})"
    return f"{param} {' → '.join(vals)} (last {vals[-1]})"


def _format_grid(
    title: str,
    stamps: list[str],
    rows: list[tuple[str, list[str]]],
    style: str,
    collapse_unchanged: bool,
) -> str:
    label = title or "Flowsheet"
    span = f"{stamps[0]} → {stamps[-1]}" if len(stamps) >= 2 and stamps[0] != stamps[-1] else stamps[0]
    series = "; ".join(_format_param(p, cells, style, collapse_unchanged) for p, cells in rows)
    return f"{label} ({span}, {len(stamps)} readings): {series}"


_CANDIDATE_PARAM_LINE = re.compile(
    r"^[ \t]*[A-Za-z][A-Za-z0-9 /()_%+-]{0,31}:?(?:\t|[ \t]{2,})\S"
)
_CANDIDATE_STAMP_LINE = re.compile(rf"^[ \t]*{_STAMP_TOKEN}(?:\t|[ \t]+)\S")


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("flowsheet_tables") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    style = options.get("style") if options.get("style") in ("series", "range") else "series"
    collapse_unchanged = bool(options.get("collapse_unchanged", True))

    lines = text.split("\n")
    out: list[str] = []
    tables = skipped = 0
    i = 0

    while i < len(lines):
        # Orientation 1: timestamps across columns
        across = _parse_time_across_header(lines[i])
        if across is not None:
            title, stamps = across
            ncols = len(stamps)
            rows: list[tuple[str, list[str]]] = []
            j = i + 1
            bad = False
            while j < len(lines):
                row = _parse_param_row(lines[j], ncols)
                if row is not None:
                    rows.append(row)
                    j += 1
                elif _CANDIDATE_PARAM_LINE.match(lines[j]):
                    bad = True
                    break
                else:
                    break
            if not bad and len(rows) >= 2:
                out.append(_format_grid(title, stamps, rows, style, collapse_unchanged))
                tables += 1
                i = j
                continue
            if bad:
                skipped += 1

        # Orientation 2: timestamps down rows (transposed)
        down = _parse_time_down_header(lines[i])
        if down is not None:
            title, params = down
            ncols = len(params)
            t_rows: list[tuple[str, list[str]]] = []
            j = i + 1
            bad = False
            while j < len(lines):
                t_row = _parse_time_down_row(lines[j], ncols)
                if t_row is not None:
                    t_rows.append(t_row)
                    j += 1
                elif _CANDIDATE_STAMP_LINE.match(lines[j]):
                    bad = True
                    break
                else:
                    break
            if not bad and len(t_rows) >= 2:
                stamps = [s for s, _ in t_rows]
                transposed = [
                    (params[col_idx], [cells[col_idx] for _, cells in t_rows])
                    for col_idx in range(ncols)
                ]
                out.append(_format_grid(title, stamps, transposed, style, collapse_unchanged))
                tables += 1
                i = j
                continue
            if bad:
                skipped += 1

        out.append(lines[i])
        i += 1

    details: dict[str, Any] = {"tables": tables}
    if skipped:
        details["skipped"] = skipped
    return "\n".join(out), tables, details
