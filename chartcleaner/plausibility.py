"""Values that cannot be right: physiologically impossible labs, vitals and
scores, and medication doses far above any usual single dose.

These limits are deliberately wide — they catch a decimal slip (Na 1400,
K 45), a parsing error in a compactor, or a 10× dose (nimodipine 600 mg), not
a merely abnormal result. Nothing is ever changed; :func:`check` only lists
what looks impossible so a person can look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["VALUE_LIMITS", "DOSE_LIMITS", "Implausible", "check"]

# canonical name -> (lowest possible, highest possible)
VALUE_LIMITS: dict[str, tuple[float, float]] = {
    "Na": (100, 190), "K": (1.0, 10.0), "Cl": (60, 150), "CO2": (3, 60), "BUN": (0, 300),
    "Cr": (0, 25), "Glu": (10, 2000), "Ca": (3, 20), "Mg": (0.3, 10), "Phos": (0.3, 20),
    "AG": (0, 60), "WBC": (0, 500), "Hgb": (1, 25), "Hct": (5, 75), "Plt": (0, 2500),
    "MCV": (40, 150), "AST": (0, 30000), "ALT": (0, 30000), "ALP": (0, 5000), "TBili": (0, 80),
    "Alb": (0.5, 7), "INR": (0.5, 20), "PT": (5, 150), "PTT": (10, 250), "Lactate": (0, 35),
    "HR": (10, 300), "RR": (0, 80), "SpO2": (30, 100), "MAP": (10, 250), "ICP": (0, 150),
    "CPP": (0, 250), "CVP": (0, 50),
    "GCS": (3, 15), "NIHSS": (0, 42), "Hunt-Hess": (1, 5), "mRS": (0, 6), "Fisher": (0, 4),
    "RASS": (0, 5), "CPOT": (0, 8),
}
# temperatures may be Celsius or Fahrenheit
_TEMP_RANGES = ((25.0, 45.0), (77.0, 113.0))

_VITAL_LABELS = {
    "hr": "HR", "heart rate": "HR", "pulse": "HR", "rr": "RR", "resp": "RR",
    "respiratory rate": "RR", "spo2": "SpO2", "o2 sat": "SpO2", "sao2": "SpO2",
    "map": "MAP", "icp": "ICP", "cpp": "CPP", "cvp": "CVP",
    "temp": "T", "temperature": "T", "tmax": "T",
}
_SCORE_LABELS = {
    "gcs": "GCS", "nihss": "NIHSS", "hunt-hess": "Hunt-Hess", "hunt hess": "Hunt-Hess",
    "mrs": "mRS", "fisher": "Fisher", "rass": "RASS", "cpot": "CPOT",
}

# drug -> [(unit regex, highest usual single dose / rate)]
DOSE_LIMITS: dict[str, list[tuple[str, float]]] = {
    "nimodipine": [(r"mg", 120)],
    "acetaminophen": [(r"mg", 2000)],
    "levetiracetam": [(r"mg", 4500)],
    "metoprolol": [(r"mg", 200)],
    "labetalol": [(r"mg", 300)],
    "hydralazine": [(r"mg", 100)],
    "morphine": [(r"mg", 60)],
    "hydromorphone": [(r"mg", 32)],
    "oxycodone": [(r"mg", 80)],
    "fentanyl": [(r"mcg/hr", 400), (r"mcg", 500)],
    "insulin": [(r"units?/hr", 50), (r"units?", 150)],
    "heparin": [(r"units?/kg/hr", 40), (r"units?/hr", 5000)],
    "enoxaparin": [(r"mg", 200)],
    "nicardipine": [(r"mg/hr", 15)],
    "clevidipine": [(r"mg/hr", 32)],
    "norepinephrine": [(r"mcg/kg/min", 3), (r"mcg/min", 100)],
    "epinephrine": [(r"mcg/kg/min", 2), (r"mcg/min", 50)],
    "vasopressin": [(r"units?/min", 0.1)],
    "propofol": [(r"mcg/kg/min", 200)],
    "dexmedetomidine": [(r"mcg/kg/hr", 2.0)],
    "phenylephrine": [(r"mcg/min", 400), (r"mcg/kg/min", 10)],
    "mannitol": [(r"g", 150)],
    "lorazepam": [(r"mg", 10)],
    "midazolam": [(r"mg/hr", 30), (r"mg", 20)],
    "potassium chloride": [(r"meq", 80)],
    "vancomycin": [(r"mg", 4000), (r"g", 4)],
    "warfarin": [(r"mg", 20)],
    "aspirin": [(r"mg", 1300)],
}

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_DOSE = re.compile(
    rf"(?<![A-Za-z])(?P<drug>{'|'.join(sorted((re.escape(d) for d in DOSE_LIMITS), key=len, reverse=True))})"
    rf"(?![A-Za-z])[^\n\d]{{0,30}}?(?P<num>{_NUM})\s?(?P<unit>[A-Za-z]+(?:/[A-Za-z]+)*)",
    re.IGNORECASE)


@dataclass
class Implausible:
    kind: str           # "value" | "dose"
    label: str
    value: str
    unit: str
    low: float | None
    high: float
    line: str

    @property
    def message(self) -> str:
        if self.kind == "dose":
            return (f"{self.label} {self.value} {self.unit} is above the usual most "
                    f"({self.high:g} {self.unit}) — check for a decimal or unit error")
        return (f"{self.label} {self.value} is outside what is possible "
                f"({self.low:g}–{self.high:g}) — check for a typo or parsing error")

    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.label, self.value)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "value": self.value, "unit": self.unit,
                "low": self.low, "high": self.high, "line": self.line, "message": self.message}


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _line(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else len(text)].strip()


def _canonical(label: str, before: str) -> str | None:
    from .trends import _lab_name
    lab = _lab_name(before)
    if lab:
        return lab
    key = label.lower()
    return _VITAL_LABELS.get(key) or _SCORE_LABELS.get(key)


def check(text: str) -> list[Implausible]:
    """Impossible values and very high doses in ``text`` (dates/times ignored)."""
    from .fact_check import _LABELED_NUM, _label_of, _mask

    masked = _mask(text)
    out: list[Implausible] = []
    seen: set[tuple[str, str, str, str]] = set()
    for m in _LABELED_NUM.finditer(masked):
        raw = m.group("num").lstrip("<>")
        before = masked[max(0, m.start() - 48):m.start()]
        label = _label_of(before)
        if not label:
            continue
        name = _canonical(label, before)
        if not name:
            continue
        try:
            value = _number(raw)
        except ValueError:
            continue
        if name == "T":
            if any(lo <= value <= hi for lo, hi in _TEMP_RANGES):
                continue
            low, high = _TEMP_RANGES[0]
        else:
            low, high = VALUE_LIMITS[name]
            if low <= value <= high:
                continue
        line = _line(text, m.start())
        key = ("value", name, raw, line)
        if key not in seen:
            seen.add(key)
            out.append(Implausible("value", name, raw, "", low, high, line))
    for m in _DOSE.finditer(masked):
        drug = m.group("drug").lower()
        unit = m.group("unit")
        for unit_rx, limit in DOSE_LIMITS[drug]:
            if re.fullmatch(unit_rx, unit, re.IGNORECASE):
                try:
                    value = _number(m.group("num"))
                except ValueError:
                    break
                if value > limit:
                    line = _line(text, m.start())
                    key = ("dose", drug, m.group("num"), line)
                    if key not in seen:
                        seen.add(key)
                        out.append(Implausible("dose", drug, m.group("num"), unit, None, limit, line))
                break
    return out
