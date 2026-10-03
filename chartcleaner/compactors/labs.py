"""Condense lab tables into one line per panel (stage ``lab_compaction``).

Two Epic layouts are recognized:

* **Result tables** — one component per line: name, value, optional flag,
  reference range and unit (tab- or multi-space separated), e.g.
  ``Sodium   132   L   135 - 145 mmol/L``.
* **Recent Labs grids** — a header row of dates/times, then one row per
  component with a value per column, e.g. ``NA  138  135*``.

A block is rewritten only when **every** line in it parses and names a known
lab (``lab_compaction.aliases`` adds or overrides names); anything unusual is
left exactly as written. Output, with ``style: "line"``::

    BMP: Na 132 (L), K 4.1, Cl 102, CO2 24, BUN 18, Cr 0.9, Glu 132 (H)

``style: "fishbone"`` draws the BMP/CBC skeletons when every value is
present. Grids show the trend (``Na 138 → 135 (L)``) or, with
``latest_only``, the last value. Reference ranges are dropped unless
``keep_reference_ranges`` is on.
"""

from __future__ import annotations

import re
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "style": "line", "latest_only": False,
                            "keep_reference_ranges": False, "aliases": {}}

# Canonical short name -> (panel, accepted spellings). Panels print in this order.
LABS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Na": ("BMP", ("sodium", "na")), "K": ("BMP", ("potassium", "k")),
    "Cl": ("BMP", ("chloride", "cl")), "CO2": ("BMP", ("co2", "bicarbonate", "hco3", "total co2", "carbon dioxide")),
    "BUN": ("BMP", ("bun", "urea nitrogen", "blood urea nitrogen")),
    "Cr": ("BMP", ("creatinine", "creat", "cr")), "Glu": ("BMP", ("glucose", "glu")),
    "Ca": ("BMP", ("calcium", "ca")), "AG": ("BMP", ("anion gap", "ag")),
    "Mg": ("Other", ("magnesium", "mg")), "Phos": ("Other", ("phosphorus", "phos", "phosphate")),
    "WBC": ("CBC", ("wbc", "white blood cell count", "white blood cells")),
    "Hgb": ("CBC", ("hemoglobin", "hgb", "hb")), "Hct": ("CBC", ("hematocrit", "hct")),
    "Plt": ("CBC", ("platelets", "platelet count", "plt")),
    "MCV": ("CBC", ("mcv",)),
    "AST": ("LFT", ("ast", "sgot")), "ALT": ("LFT", ("alt", "sgpt")),
    "ALP": ("LFT", ("alkaline phosphatase", "alk phos", "alp")),
    "TBili": ("LFT", ("total bilirubin", "bilirubin total", "bilirubin, total", "tbili")),
    "Alb": ("LFT", ("albumin", "alb")),
    "INR": ("Coags", ("inr",)), "PT": ("Coags", ("pt", "prothrombin time", "protime")),
    "PTT": ("Coags", ("ptt", "aptt")),
    "Lactate": ("Other", ("lactate", "lactic acid")),
    "Trop": ("Other", ("troponin", "troponin i", "troponin t", "hs troponin")),
}
PANEL_ORDER = ("BMP", "CBC", "LFT", "Coags", "Other")

_VALUE = r"[<>]?\d+(?:\.\d+)?"
_FLAG = r"(?:\(?(?:H|L|HH|LL|A|C|High|Low|Critical)\)?|\*)"
_RANGE = r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?|[<>]\s*\d+(?:\.\d+)?"
_UNIT = r"[A-Za-z%/µμ][A-Za-z0-9%/µμ.^]*(?:/[A-Za-z0-9.^]+)?"
_ROW = re.compile(
    rf"^[ \t]*(?P<name>[A-Za-z][A-Za-z0-9 ,()/-]*?)[ \t]*(?:\t|:|[ \t]{{2,}})[ \t]*"
    rf"(?P<value>{_VALUE})[ \t]*(?P<flag1>{_FLAG})?[ \t]*"
    rf"(?:(?P<range>{_RANGE})[ \t]*)?(?:(?P<unit>{_UNIT})[ \t]*)?(?P<flag2>{_FLAG})?"
    rf"(?:[ \t]+(?:Final|Prelim(?:inary)?|Corrected))?[ \t]*$")
_DATE = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?"  # tolerate hospital-day labels
_GRID_HEADER = re.compile(rf"^[ \t]*(?:Lab|Component|Recent Labs)?[ \t]*(?:{_DATE}(?:[ \t]+\d{{3,4}})?[ \t]*){{2,}}$",
                          re.IGNORECASE)
_GRID_ROW = re.compile(rf"^[ \t]*(?P<name>[A-Za-z][A-Za-z0-9 ,()/-]*?)[ \t]+"
                       rf"(?P<values>(?:(?:{_VALUE}|-+|--)[*HL]?[ \t]*){{2,}})$")
_COLUMNS = re.compile(r"^[ \t]*(?:Component|Test|Lab)[ \t]+(?:Value|Result)\b[^\n]*$", re.IGNORECASE)


def _alias_map(options: dict) -> dict[str, str]:
    names = {spelling: short for short, (_panel, spellings) in LABS.items() for spelling in spellings}
    for spelling, short in (options.get("aliases") or {}).items():
        if isinstance(spelling, str) and isinstance(short, str) and spelling.strip():
            names[spelling.strip().casefold()] = short.strip()
    return names


def _short(name: str, aliases: dict[str, str]) -> str | None:
    key = " ".join(name.replace(",", " ").split()).casefold()
    return aliases.get(key) or aliases.get(key.replace(" ", ""))


def _panel(short: str) -> str:
    return LABS.get(short, ("Other", ()))[0]


def _flag(*flags: str | None) -> str:
    for f in flags:
        if f:
            f = f.strip("()").upper()
            if f in ("H", "HIGH", "HH"):
                return " (H)"
            if f in ("L", "LOW", "LL"):
                return " (L)"
            if f in ("*", "A"):
                return " (abnl)"
            if f in ("C", "CRITICAL"):
                return " (crit)"
    return ""


def _fishbone(values: dict[str, str], panel: str) -> str | None:
    if panel == "BMP" and all(k in values for k in ("Na", "Cl", "BUN", "K", "CO2", "Cr", "Glu")):
        v = values
        cols = [(v["Na"], v["K"]), (v["Cl"], v["CO2"]), (v["BUN"], v["Cr"])]
        widths = [max(len(a), len(b)) for a, b in cols]
        top = " | ".join(a.ljust(w) for (a, _b), w in zip(cols, widths))
        bottom = " | ".join(b.ljust(w) for (_a, b), w in zip(cols, widths))
        return f"{top} /\n{'-' * len(top)}< {v['Glu']}\n{bottom} \\"
    if panel == "CBC" and all(k in values for k in ("WBC", "Hgb", "Hct", "Plt")):
        v = values
        pad = " " * (len(v["WBC"]) + 2)
        return f"{pad}{v['Hgb']}\n{v['WBC']} >--< {v['Plt']}\n{pad}{v['Hct']}"
    return None


def _format(entries: list[tuple[str, str, str]], options: dict, prefix: str) -> str:
    """entries: (short, value text incl. flag, bare value) in source order."""
    by_panel: dict[str, list[tuple[str, str, str]]] = {}
    for item in entries:
        by_panel.setdefault(_panel(item[0]), []).append(item)
    lines = []
    for panel in PANEL_ORDER:
        items = by_panel.get(panel)
        if not items:
            continue
        if options.get("style") == "fishbone":
            drawn = _fishbone({s: bare for s, _shown, bare in items}, panel)
            if drawn:
                lines.append(f"{prefix}{panel}:\n{drawn}")
                continue
        lines.append(f"{prefix}{panel}: " + ", ".join(f"{s} {shown}" for s, shown, _bare in items))
    return "\n".join(lines)


def _table_block(lines: list[str], aliases: dict[str, str], options: dict) -> str | None:
    entries = []
    for line in lines:
        m = _ROW.match(line)
        short = m and _short(m.group("name"), aliases)
        if not short:
            return None
        shown = m.group("value") + _flag(m.group("flag1"), m.group("flag2"))
        if options.get("keep_reference_ranges") and m.group("range"):
            shown += f" [{' '.join(m.group('range').split())}]"
        entries.append((short, shown, m.group("value")))
    return _format(entries, options, "")


def _grid_block(header: str, rows: list[str], aliases: dict[str, str], options: dict) -> str | None:
    entries = []
    for line in rows:
        m = _GRID_ROW.match(line)
        short = m and _short(m.group("name"), aliases)
        if not short:
            return None
        cells = [c for c in m.group("values").split() if not set(c) <= {"-"}]
        if not cells:
            return None
        def shown(cell: str) -> str:
            bare = cell.rstrip("*HL")
            return bare + _flag(cell[len(bare):] or None)
        if options.get("latest_only"):
            text = shown(cells[-1])
        else:
            text = " → ".join(shown(c) for c in cells)
        entries.append((short, text, cells[-1].rstrip("*HL")))
    dates = re.findall(rf"{_DATE}(?:[ \t]+\d{{3,4}})?", header)
    span = f" ({dates[0]} → {dates[-1]})" if len(dates) > 1 and not options.get("latest_only") else (
        f" ({dates[-1]})" if dates else "")
    body = _format(entries, {**options, "style": "line"}, "")
    return "\n".join(f"{line.split(':', 1)[0]}{span}:{line.split(':', 1)[1]}" for line in body.splitlines())


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("lab_compaction") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    aliases = _alias_map(options)
    lines = text.split("\n")
    out: list[str] = []
    blocks = skipped = 0
    i = 0
    while i < len(lines):
        if _GRID_HEADER.match(lines[i]):
            j = i + 1
            while j < len(lines) and _GRID_ROW.match(lines[j]):
                j += 1
            if j - i - 1 >= 2:
                rendered = _grid_block(lines[i], lines[i + 1:j], aliases, options)
                if rendered is not None:
                    out.append(rendered)
                    blocks += 1
                    i = j
                    continue
                skipped += 1
        if _ROW.match(lines[i]) and _short(_ROW.match(lines[i]).group("name"), aliases):
            j = i
            while j < len(lines) and (m := _ROW.match(lines[j])) and _short(m.group("name"), aliases):
                j += 1
            if j - i >= 3:
                rendered = _table_block(lines[i:j], aliases, options)
                if rendered is not None:
                    if out and _COLUMNS.match(out[-1]):
                        out.pop()  # the table's column header has nothing left to label
                    out.append(rendered)
                    blocks += 1
                    i = j
                    continue
        out.append(lines[i])
        i += 1
    details = {"blocks": blocks}
    if skipped:
        details["skipped"] = skipped
    return "\n".join(out), blocks, details
