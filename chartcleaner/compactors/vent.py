"""Condense serial ventilator settings and arterial/venous blood gases (stage ``vent_summary``).

ICU flowsheets and respiratory care pastes often repeat line-by-line blood gas
panels and ventilator checks across a shift::

    ABGs:
    02:00 ABG: 7.28 / 54 / 62 / 24
    06:00 ABG: 7.34 / 46 / 78 / 24
    10:00 ABG: 7.40 / 40 / 92 / 24

becomes::

    ABGs:
    ABG (3, 02:00–10:00): pH 7.28 → 7.34 → 7.40 (acidemia: pH 7.28), pCO2 54 → 46 → 40, pO2 62 → 78 → 92, HCO3 24

And serial ventilator lines::

    Vent Settings:
    02:00 AC/VC RR 18 TV 450 FiO2 60% PEEP 10
    06:00 AC/VC RR 16 TV 450 FiO2 40% PEEP 8
    10:00 PSV PS 10 PEEP 5 FiO2 35% SBT passed

becomes::

    Vent Settings:
    Vent (3, 02:00–10:00): mode AC/VC → PSV, FiO2 60% → 40% → 35%, PEEP 10 → 8 → 5, RR 18 → 16, TV 450, PS 10 (SBT passed)

Safety rule: a contiguous block is rewritten only when every line in the block
parses cleanly and the block has at least 2 readings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "abg": True,
    "settings": True,
}

_HD = r"(?:[ \t]*\((?:HD|POD)#[^)]*\))?"
_DATE = rf"\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?{_HD}"
_TIME = r"(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d"
_STAMP = rf"(?:{_DATE}[ \t]+)?(?:{_TIME})"

_ABG_HEADER = re.compile(
    r"^[ \t]*(?:ABGs?|VBGs?|Blood[ \t]+Gas(?:es)?|Arterial[ \t]+Blood[ \t]+Gas(?:es)?)[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)
_VENT_HEADER = re.compile(
    r"^[ \t]*(?:Vent(?:ilator)?(?:[ \t]+Settings?|[ \t]+Flowsheet)?|Mechanical[ \t]+Ventilation)[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)

# Slash-delimited blood gas: [stamp] [ABG|VBG] 7.32 / 45 / 78 / 24 [/ -2]
_ABG_SLASH = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?:(?P<stamp>{_STAMP})[ \t]*[-–—:]?[ \t]*)?"
    r"(?:(?P<kind>ABG|VBG|Art(?:erial)?[ \t]+Blood[ \t]+Gas|Venous[ \t]+Blood[ \t]+Gas)[ \t]*[-–—:]?[ \t]*)?"
    r"(?P<ph>6\.\d{2}|7\.\d{2})[ \t]*/[ \t]*"
    r"(?P<pco2>\d{1,3}(?:\.\d+)?)[ \t]*/[ \t]*"
    r"(?P<po2>\d{1,3}(?:\.\d+)?)"
    r"(?:[ \t]*/[ \t]*(?P<hco3>\d{1,2}(?:\.\d+)?))?"
    r"(?:[ \t]*/[ \t]*(?P<be>[+-]?\d{1,2}(?:\.\d+)?))?"
    r"(?P<tail>[ \t,;()A-Za-z0-9%./+-]*)$",
    re.IGNORECASE,
)

# Labeled blood gas: [stamp] [ABG|VBG] pH 7.32, pCO2 45, pO2 78[, HCO3 24][, BE -2][, Lactate 1.8]
_ABG_PREFIX = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?:(?P<stamp>{_STAMP})[ \t]*[-–—:]?[ \t]*)?"
    r"(?:(?P<kind>ABG|VBG|Art(?:erial)?[ \t]+Blood[ \t]+Gas|Venous[ \t]+Blood[ \t]+Gas)[ \t]*[-–—:]?[ \t]*)?"
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_ABG_KV = re.compile(
    r"(?P<k>pH|p?a?CO2|p?a?O2|HCO3-?|bicarb|BE|base[ \t]+excess|SaO2|O2[ \t]*sat|FiO2|PEEP|lac(?:tate)?)"
    r"[ \t]*(?:[:=][ \t]*|[ \t]+)"
    r"(?P<v>[+-]?\d+(?:\.\d+)?%?)",
    re.IGNORECASE,
)

# Ventilator settings
_VENT_MODES = r"AC/VC|VC/AC|A/C|AC-VC|VC|PRVC|PCV|AC/PC|SIMV|PSV|CPAP|BiPAP|APRV|HFNC"
_VENT_LINE_PREFIX = re.compile(
    rf"^[ \t]*(?:[-•*][ \t]*)?(?:(?P<stamp>{_STAMP})[ \t]*[-–—:]?[ \t]*)?"
    r"(?:(?:Vent(?:ilator)?)[ \t]*[-–—:]?[ \t]*)?"
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_VENT_MODE_TOKEN = re.compile(rf"\b(?P<mode>{_VENT_MODES})\b", re.IGNORECASE)
_VENT_KV = re.compile(
    rf"\b(?P<k>Mode|RR|f|rate|TV|Vt|tidal[ \t]+volume|FiO2|PEEP|PS|pressure[ \t]+support|"
    r"Pplat|plateau|PIP|Ppeak|Ve|MV|minute[ \t]+ventilation|I:E)"
    r"[ \t]*(?:[:=][ \t]*|[ \t]+)"
    rf"(?P<v>(?:{_VENT_MODES})"
    r"|\d+(?:\.\d+)?(?::\d+(?:\.\d+)?)?[ \t]*(?:%|mL|ml|L/min|cmH2O)?)",
    re.IGNORECASE,
)
_SBT_TOKEN = re.compile(
    r"\b(?P<sbt>SBT[ \t]+(?:passed|failed|tolerated|intolerant|in[ \t]+progress|trial)|"
    r"(?:passed|failed)[ \t]+SBT|spontaneous[ \t]+breathing[ \t]+trial(?:[ \t]+(?:passed|failed))?)\b",
    re.IGNORECASE,
)


@dataclass
class _AbgReading:
    stamp: str = ""
    kind: str = "ABG"
    ph: str = ""
    pco2: str = ""
    po2: str = ""
    hco3: str = ""
    be: str = ""
    lactate: str = ""
    fio2: str = ""
    peep: str = ""


@dataclass
class _VentReading:
    stamp: str = ""
    mode: str = ""
    params: dict[str, str] = field(default_factory=dict)
    sbt: str = ""


def _norm_gas_kind(raw: str | None) -> str:
    if not raw:
        return "ABG"
    return "VBG" if raw.strip().upper().startswith("V") else "ABG"


def _parse_abg_line(line: str) -> _AbgReading | None:
    m = _ABG_SLASH.match(line)
    if m:
        ph = m.group("ph")
        pco2 = m.group("pco2")
        po2 = m.group("po2")
        if not (6.80 <= float(ph) <= 7.80 and 10 <= float(pco2) <= 150 and 15 <= float(po2) <= 650):
            return None
        tail = m.group("tail") or ""
        fio2 = ""
        peep = ""
        lactate = ""
        for km in _ABG_KV.finditer(tail):
            k = km.group("k").lower()
            v = km.group("v")
            if k == "fio2":
                fio2 = v if v.endswith("%") else f"{v}%"
            elif k == "peep":
                peep = v
            elif k.startswith("lac"):
                lactate = v
        leftover = _ABG_KV.sub("", tail)
        leftover = re.sub(r"\b(?:on|room[ \t]+air|RA|L[ \t]*NC|LPM|mEq/L|mmHg)\b|[(),/;:\-\s]", "", leftover, flags=re.I)
        if leftover:
            return None
        return _AbgReading(
            stamp=(m.group("stamp") or "").strip(),
            kind=_norm_gas_kind(m.group("kind")),
            ph=ph,
            pco2=pco2,
            po2=po2,
            hco3=m.group("hco3") or "",
            be=m.group("be") or "",
            lactate=lactate,
            fio2=fio2,
            peep=peep,
        )

    pm = _ABG_PREFIX.match(line)
    if not pm:
        return None
    body = pm.group("body")
    vals: dict[str, str] = {}
    for km in _ABG_KV.finditer(body):
        raw_k = km.group("k").lower().replace(" ", "")
        v = km.group("v").strip()
        if raw_k == "ph":
            vals["ph"] = v
        elif raw_k in ("pco2", "paco2", "co2"):
            vals["pco2"] = v
        elif raw_k in ("po2", "pao2", "o2"):
            vals["po2"] = v
        elif raw_k in ("hco3", "hco3-", "bicarb"):
            vals["hco3"] = v
        elif raw_k in ("be", "baseexcess"):
            vals["be"] = v
        elif raw_k == "fio2":
            vals["fio2"] = v if v.endswith("%") else f"{v}%"
        elif raw_k == "peep":
            vals["peep"] = v
        elif raw_k.startswith("lac"):
            vals["lactate"] = v
    if "ph" not in vals or ("pco2" not in vals and "po2" not in vals):
        return None
    try:
        if not (6.80 <= float(vals["ph"]) <= 7.80):
            return None
    except ValueError:
        return None
    leftover = _ABG_KV.sub("", body)
    leftover = re.sub(r"\b(?:mEq/L|mmol/L|mmHg|on|RA|room[ \t]+air)\b|[(),/;:\-\s]", "", leftover, flags=re.I)
    if leftover:
        return None
    return _AbgReading(
        stamp=(pm.group("stamp") or "").strip(),
        kind=_norm_gas_kind(pm.group("kind")),
        ph=vals.get("ph", ""),
        pco2=vals.get("pco2", ""),
        po2=vals.get("po2", ""),
        hco3=vals.get("hco3", ""),
        be=vals.get("be", ""),
        lactate=vals.get("lactate", ""),
        fio2=vals.get("fio2", ""),
        peep=vals.get("peep", ""),
    )


def _norm_vent_key(raw: str) -> str:
    low = " ".join(raw.lower().split())
    if low == "mode":
        return "mode"
    if low in ("rr", "f", "rate"):
        return "RR"
    if low in ("tv", "vt", "tidal volume"):
        return "TV"
    if low == "fio2":
        return "FiO2"
    if low == "peep":
        return "PEEP"
    if low in ("ps", "pressure support"):
        return "PS"
    if low in ("pplat", "plateau"):
        return "Pplat"
    if low in ("pip", "ppeak"):
        return "PIP"
    if low in ("ve", "mv", "minute ventilation"):
        return "Ve"
    if low == "i:e":
        return "I:E"
    return raw


def _parse_vent_line(line: str) -> _VentReading | None:
    if _parse_abg_line(line) is not None:
        return None
    pm = _VENT_LINE_PREFIX.match(line)
    if not pm:
        return None
    body = pm.group("body")
    params: dict[str, str] = {}
    mode = ""
    sbt = ""

    sm = _SBT_TOKEN.search(body)
    if sm:
        sbt = " ".join(sm.group("sbt").split())
        body = body[:sm.start()] + " " + body[sm.end():]

    for km in _VENT_KV.finditer(body):
        k = _norm_vent_key(km.group("k"))
        v = " ".join(km.group("v").split())
        if k == "mode":
            mode = v.upper()
        else:
            if k == "FiO2" and not v.endswith("%"):
                v = f"{v}%"
            params[k] = v
    body_wo_kv = _VENT_KV.sub(" ", body)

    mm = _VENT_MODE_TOKEN.search(body_wo_kv)
    if mm and not mode:
        mode = mm.group("mode").upper()
        body_wo_kv = body_wo_kv[:mm.start()] + " " + body_wo_kv[mm.end():]

    # Require at least 2 vent parameters (or mode + at least 1 parameter, or SBT + parameter)
    total_signals = len(params) + (1 if mode else 0) + (1 if sbt else 0)
    if total_signals < 2 or not (params or sbt):
        return None
    if not any(k in params for k in ("FiO2", "PEEP", "TV", "RR", "PS", "Pplat", "PIP")) and not (mode and sbt):
        return None

    leftover = re.sub(r"\b(?:on|at|cmH2O|mL|L/min|bpm|breaths/min)\b|[(),/;:\-\s]", "", body_wo_kv, flags=re.I)
    if leftover:
        return None
    return _VentReading(
        stamp=(pm.group("stamp") or "").strip(),
        mode=mode,
        params=params,
        sbt=sbt,
    )


def _collapse_series(values: list[str]) -> str:
    non_empty = [v for v in values if v]
    if not non_empty:
        return ""
    if len(set(non_empty)) == 1:
        return non_empty[0]
    deduped: list[str] = []
    for v in non_empty:
        if not deduped or deduped[-1] != v:
            deduped.append(v)
    return " → ".join(deduped)


def _stamp_window(readings: list) -> str:
    """, ``first–last`` (or ``only``) timestamp suffix for a block header."""
    stamps = [r.stamp for r in readings if r.stamp]
    if len(stamps) >= 2 and stamps[0] != stamps[-1]:
        return f", {stamps[0]}–{stamps[-1]}"
    return f", {stamps[0]}" if stamps else ""


def _format_abg_block(readings: list[_AbgReading]) -> str:
    kinds = {r.kind for r in readings}
    label = next(iter(kinds)) if len(kinds) == 1 else "ABG/VBG"
    window = _stamp_window(readings)

    parts: list[str] = []
    ph_series = _collapse_series([r.ph for r in readings])
    if ph_series:
        flags: list[str] = []
        low_phs = [r.ph for r in readings if r.ph and float(r.ph) < 7.30]
        high_phs = [r.ph for r in readings if r.ph and float(r.ph) > 7.50]
        if low_phs:
            flags.append(f"acidemia: pH {low_phs[0]}")
        if high_phs:
            flags.append(f"alkalemia: pH {high_phs[0]}")
        flag_str = f" ({'; '.join(flags)})" if flags else ""
        parts.append(f"pH {ph_series}{flag_str}")

    pco2_series = _collapse_series([r.pco2 for r in readings])
    if pco2_series:
        parts.append(f"pCO2 {pco2_series}")

    po2_series = _collapse_series([r.po2 for r in readings])
    if po2_series:
        low_po2 = [r.po2 for r in readings if r.po2 and float(r.po2) < 60]
        po2_flag = f" (hypoxemia: pO2 {low_po2[0]})" if low_po2 else ""
        parts.append(f"pO2 {po2_series}{po2_flag}")

    for attr, name in (("hco3", "HCO3"), ("be", "BE"), ("lactate", "Lactate"), ("fio2", "FiO2"), ("peep", "PEEP")):
        series = _collapse_series([getattr(r, attr) for r in readings])
        if series:
            parts.append(f"{name} {series}")

    return f"{label} ({len(readings)}{window}): " + ", ".join(parts)


def _format_vent_block(readings: list[_VentReading]) -> str:
    window = _stamp_window(readings)
    parts: list[str] = []
    mode_series = _collapse_series([r.mode for r in readings])
    if mode_series:
        parts.append(f"mode {mode_series}")

    param_order = ("FiO2", "PEEP", "RR", "TV", "PS", "Pplat", "PIP", "Ve", "I:E")
    for key in param_order:
        series = _collapse_series([r.params.get(key, "") for r in readings])
        if series:
            parts.append(f"{key} {series}")

    sbts = [r.sbt for r in readings if r.sbt]
    sbt_suffix = f" ({_collapse_series(sbts)})" if sbts else ""
    return f"Vent ({len(readings)}{window}): " + ", ".join(parts) + sbt_suffix


def _condense_block(
    lines: list[str],
    i: int,
    header_re: re.Pattern,
    parse_line,
    format_block,
) -> tuple[int, list[str], int] | None:
    """From line ``i``, condense a contiguous run of parsable readings.

    Returns ``(next_index, output_lines, readings_count)`` when the run holds at
    least 2 readings (an optional header line is kept); ``None`` otherwise.
    """
    has_hdr = bool(header_re.match(lines[i]))
    start = i + 1 if has_hdr else i
    readings = []
    j = start
    while j < len(lines):
        parsed = parse_line(lines[j])
        if parsed is None:
            break
        readings.append(parsed)
        j += 1
    if len(readings) < 2:
        return None
    out = ([lines[i]] if has_hdr else []) + [format_block(readings)]
    return j, out, len(readings)


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    opts = {**DEFAULTS, **(cfg.get("vent_summary") or {})}
    if not opts.get("enabled", False):
        return text, 0, {"skipped": True}

    do_abg = bool(opts.get("abg", True))
    do_settings = bool(opts.get("settings", True))
    if not do_abg and not do_settings:
        return text, 0, {"skipped": True}

    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    condensed = 0

    while i < n:
        hit = None
        if do_abg:
            hit = _condense_block(lines, i, _ABG_HEADER, _parse_abg_line, _format_abg_block)
        if hit is None and do_settings:
            hit = _condense_block(lines, i, _VENT_HEADER, _parse_vent_line, _format_vent_block)
        if hit is not None:
            next_i, block, count = hit
            out.extend(block)
            condensed += count
            i = next_i
            continue

        out.append(lines[i])
        i += 1

    if not condensed:
        return text, 0, {}
    return "\n".join(out), condensed, {"readings_condensed": condensed}
