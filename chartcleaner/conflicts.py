"""Possible contradictions inside the latest note.

The meaning guard (:mod:`chartcleaner.fact_check`) catches changes *cleaning*
makes. This module looks at what the chart already says: two lines of the
latest note that can't both be true. Each flag quotes both lines verbatim and
says why they were paired; nothing is changed and nothing is decided — the
reader checks which line is right. Checks:

* **side** — the same one-sided finding (weakness, facial droop, MCA stroke,
  SDH, craniotomy, EVD, pneumothorax, chest tube …) stated on the left in one
  line and on the right in another, with no line saying both or "bilateral";
* **lesion side** — every cerebral lesion is on one side and every
  motor/sensory deficit is on that same side (deficits are usually opposite);
* **diet** — NPO and eating (tolerating PO, regular diet) in the same note;
* **code status** — full code on one line and DNR/DNI/comfort care on another;
* **anticoagulation** — "no / holding anticoagulation" next to an active
  therapeutic anticoagulant (prophylactic SQ heparin doesn't count);
* **allergy** — a drug the chart lists as an allergy (or one in the same
  class: penicillins, sulfonamides, heparin for HIT) on a medication line;
* **fever** — "afebrile" next to a temperature of 38.0 °C / 100.4 °F or more.

Insights read the *cleaned* chart, so the abbreviated forms count too
(L/R, ALLG, Hep gtt, b/l).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["KINDS", "Conflict", "ConflictReport", "build"]

KINDS = ("side", "lesion side", "diet", "code status", "anticoagulation", "allergy", "fever")

_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?[A-Za-z][A-Za-z /&]{1,40}:\s*$")
_BULLET = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+")

# ---------------------------------------------------------------- sides
# "L"/"R" are case-sensitive and must stand alone ("R/O", "2 L NC" and "L5" are not sides)
_SIDE = re.compile(
    r"(?P<left>\b(?i:left)\b|(?<![\w/.])(?:L|Lt|LT)(?![\w/])|(?<![\w/.])L(?=[UL]E\b))"
    r"|(?P<right>\b(?i:right)\b|(?<![\w/.])(?:R|Rt|RT)(?![\w/])|(?<![\w/.])R(?=[UL]E\b))"
    r"|(?P<both>\b(?i:bilateral(?:ly)?|bilat|b/l)\b|(?<![\w/])B/L(?![\w/]))")
_LIQUID = re.compile(r"\d\s*$")  # "2 L" — liters, not left
# side fused into the abbreviation (RHP = right hemiparesis, LMCA = left MCA)
_FUSED = re.compile(r"(?<![\w/])(?P<side>[LR])(?P<what>HP|SWk?|MCA|ACA|PCA)\b")
_FUSED_NAME = {"HP": "weakness", "SW": "weakness", "SWk": "weakness", "MCA": "MCA", "ACA": "ACA", "PCA": "PCA"}

# canonical finding -> pattern. Only findings that are usually on one side.
_FINDINGS: dict[str, re.Pattern] = {
    "weakness": re.compile(r"\b(?:hemi)?(?:paresis|plegia|paretic|plegic)\b|\bweak(?:ness)?\b|\bdrift\b",
                           re.IGNORECASE),
    "facial droop": re.compile(r"\bfacial (?:droop|weakness|palsy)\b|\bface droop\b|\bNLF flattening\b",
                               re.IGNORECASE),
    "neglect": re.compile(r"\bneglect\b", re.IGNORECASE),
    "hemianopia": re.compile(r"\bhemianop(?:ia|sia)\b|\bfield cut\b", re.IGNORECASE),
    "numbness": re.compile(r"\bnumbness\b|\bhemi-?(?:an)?esthesia\b|\bsensory loss\b", re.IGNORECASE),
    "MCA": re.compile(r"\bMCA\b"),
    "ACA": re.compile(r"\bACA\b"),
    "PCA": re.compile(r"\bPCA\b(?!\s*pump)"),
    "SDH": re.compile(r"\bSDH\b|\bsubdural\b", re.IGNORECASE),
    "EDH": re.compile(r"\bEDH\b|\bepidural hematoma\b", re.IGNORECASE),
    "ICH": re.compile(r"\bICH\b|\bIPH\b|\bintraparenchymal\b|\bintracerebral\b", re.IGNORECASE),
    "craniotomy": re.compile(r"\b(?:hemi)?crani(?:otomy|ectomy)\b", re.IGNORECASE),
    "EVD": re.compile(r"\bEVD\b|\bventriculostomy\b", re.IGNORECASE),
    "pneumothorax": re.compile(r"\bpneumothorax\b|\bPTX\b", re.IGNORECASE),
    "chest tube": re.compile(r"\bchest tube\b|\bCT to (?:suction|water seal)\b", re.IGNORECASE),
}
_DEFICITS = ("weakness", "facial droop", "neglect", "hemianopia", "numbness")
_LESIONS = ("MCA", "ACA", "PCA", "SDH", "EDH", "ICH", "craniotomy")
# a generic stroke/bleed word counts as a cerebral lesion only with a hemispheric place word
_GENERIC_LESION = re.compile(
    r"\b(?:frontal|parietal|temporal|occipital|fronto\w*|temporo\w*|parieto\w*|hemispher\w*|basal ganglia|BG"
    r"|thalam\w*|(?:internal )?capsul\w*|corona radiata|insula\w*|putamin\w*|putamen|caudate|convexity|cortical"
    r"|subcortical)\b[\w\s-]{0,20}?\b(?:infarct\w*|stroke|CVA|hemorrhage|bleed|hematoma|contusion|mass|lesion)\b",
    re.IGNORECASE)
_NOT_HEMISPHERE = re.compile(r"\b(?:cerebell\w*|pont\w*|pons|brainstem|brain stem|medull\w*|midbrain)\b",
                             re.IGNORECASE)
_HISTORY = re.compile(r"\b(?:h/o|hx of|history of|prior|remote|old|previous|PMH|PSH|s/p remote)\b",
                      re.IGNORECASE)
_NEGATED = re.compile(r"\b(?:no|not|denies|without|w/o|negative for|neg for|resolved)\b[^.;,\n]{0,25}$",
                      re.IGNORECASE)
_WORDS_BEFORE = 4


def _side_of(line: str, start: int, end: int, prev_end: int) -> str | None:
    """The side a finding at ``line[start:end]`` is stated on, or None."""
    before = line[prev_end:start]
    words = before.split()
    window = " ".join(words[-_WORDS_BEFORE:]) if words else ""
    sides = [m for m in _SIDE.finditer(window) if not (m.group("left") and window[:m.start()].rstrip()
                                                              and _LIQUID.search(window[:m.start()]))]
    if sides:
        m = sides[-1]
        return "both" if m.group("both") else ("left" if m.group("left") else "right")
    after = re.match(r"\s*(?:,\s*|\(\s*|on (?:the )?|of (?:the )?|involving (?:the )?)?"
                     r"(\b(?i:left|right|bilateral(?:ly)?|bilat)\b|(?<![\w/])(?:L|R|B/L)(?![\w/]))",
                     line[end:end + 25])
    if after:
        word = after.group(1).lower()
        return "both" if word.startswith(("bi", "b/")) else ("left" if word.startswith("l") else "right")
    return None


def _sided(line: str) -> list[tuple[str, str]]:
    """(finding, side) pairs stated on ``line``."""
    hits: list[tuple[int, int, str]] = []
    for name, rx in _FINDINGS.items():
        hits.extend((m.start(), m.end(), name) for m in rx.finditer(line))
    hits.sort()
    out: list[tuple[str, str]] = []
    prev_end = 0
    for start, end, name in hits:
        if _NEGATED.search(line[:start]):
            prev_end = end
            continue
        side = _side_of(line, start, end, prev_end)
        if side:
            out.append((name, side))
        prev_end = end
    for m in _FUSED.finditer(line):
        if not _NEGATED.search(line[:m.start()]):
            out.append((_FUSED_NAME[m.group("what")], "left" if m.group("side") == "L" else "right"))
    if not _NOT_HEMISPHERE.search(line):
        for m in _GENERIC_LESION.finditer(line):
            side = _side_of(line, m.start(), m.end(), 0)
            if side:
                out.append(("hemispheric lesion", side))
    return out


def _side_conflicts(lines: list[str]) -> list[Conflict]:
    by_finding: dict[str, dict[str, list[str]]] = {}
    for line in lines:
        if _HISTORY.search(line):
            continue
        pairs = _sided(line)
        for name in {n for n, _s in pairs}:
            sides = {s for n, s in pairs if n == name}
            if len(sides) > 1:
                sides = {"both"}  # "L > R weakness" on one line: both on purpose
            for side in sides:
                by_finding.setdefault(name, {}).setdefault(side, []).append(line)
    out: list[Conflict] = []
    for name, sides in by_finding.items():
        if "both" in sides or not ("left" in sides and "right" in sides):
            continue
        out.append(Conflict("side", f"{name}: stated on the left and on the right",
                            [_clean(sides["left"][0]), _clean(sides["right"][0])]))
    # lesion and deficit on the same side (deficits are usually opposite the lesion)
    lesion_sides: dict[str, str] = {}
    deficit_sides: dict[str, str] = {}
    for name, sides in by_finding.items():
        if "both" in sides:
            continue
        target = lesion_sides if (name in _LESIONS or name == "hemispheric lesion") else (
            deficit_sides if name in _DEFICITS else None)
        if target is None:
            continue
        for side, ls in sides.items():
            target.setdefault(side, ls[0])
    if len(lesion_sides) == 1 and len(deficit_sides) == 1:
        side = next(iter(lesion_sides))
        if side in deficit_sides and not any(_NOT_HEMISPHERE.search(ln) for ln in lesion_sides.values()):
            out.append(Conflict("lesion side",
                                f"{side} brain lesion with a {side}-sided deficit (usually the opposite side)",
                                [_clean(lesion_sides[side]), _clean(deficit_sides[side])]))
    return out


# ---------------------------------------------------------------- diet
_NPO = re.compile(r"\bNPO\b(?!\s*(?:after|at|@|past|p)\s*(?:MN|midnight|MMN|12|\d)|\s*for\b|\s*except\b)"
                  r"|\bnothing by mouth\b", re.IGNORECASE)
_NPO_PLANNED = re.compile(r"\b(?:will|plan|make|until|then|if|prior to|before|for (?:OR|procedure|surgery))\b"
                          r"[^.\n]{0,30}\bNPO\b|\bNPO\b[^.\n]{0,20}\b(?:until|for|pending|prior)\b", re.IGNORECASE)
_EATING = re.compile(
    r"\btolerat\w*\s+(?:a\s+)?(?:PO|po|oral|regular|reg|diet|clears?|clear liquids?|full liquids?|soft|meals?|breakfast|lunch|dinner)\b"
    r"|\bdiet\s*:\s*(?:regular|reg|cardiac|diabetic|ADA|carb[- ]controlled|consistent carb\w*|soft|dysphagia|mech\w* soft"
    r"|clears?|clear liquids?|full liquids?|renal|heart healthy|low sodium|2\s*g)\b"
    r"|\b(?:eating|ate) (?:well|breakfast|lunch|dinner|\d+\s*%)\b|\bPO intake (?:good|fair|adequate)\b",
    re.IGNORECASE)


def _diet_conflicts(lines: list[str]) -> list[Conflict]:
    npo = [ln for ln in lines if _NPO.search(ln) and not _NPO_PLANNED.search(ln) and not _EATING.search(ln)]
    eating = [ln for ln in lines if _EATING.search(ln) and not _NPO.search(ln)]
    if npo and eating:
        return [Conflict("diet", "NPO and eating in the same note", [_clean(npo[0]), _clean(eating[0])])]
    return []


# ---------------------------------------------------------------- code status
_FULL_CODE = re.compile(r"\bfull code\b", re.IGNORECASE)
_LIMITED = re.compile(r"\bDNA?R\b|\bDNI\b|\bcomfort (?:care|measures)\b|\bCMO\b|\bno (?:CPR|intubation)\b",
                      re.IGNORECASE)
_CODE_TALK = re.compile(r"\b(?:discuss\w*|consider\w*|address\w*|readdress\w*|revisit\w*|goals of care"
                        r"|GOC|family meeting|would (?:want|not want)|if\b|changed|chang\w+ (?:to|from)"
                        r"|previously|was|prior)\b", re.IGNORECASE)


def _code_conflicts(lines: list[str]) -> list[Conflict]:
    full = [ln for ln in lines if _FULL_CODE.search(ln) and not _LIMITED.search(ln) and not _CODE_TALK.search(ln)]
    limited = [ln for ln in lines if _LIMITED.search(ln) and not _FULL_CODE.search(ln)
               and not _CODE_TALK.search(ln) and not _NEGATED_CODE.search(ln)]
    if full and limited:
        return [Conflict("code status", "full code and DNR/DNI/comfort care in the same note",
                         [_clean(full[0]), _clean(limited[0])])]
    return []


_NEGATED_CODE = re.compile(r"\bnot\s+(?:DNA?R|DNI)\b|\b(?:DNA?R|DNI)\s*:\s*(?:no|n)\b", re.IGNORECASE)


# ---------------------------------------------------------------- anticoagulation
_THERAPEUTIC_AC = re.compile(
    r"\b(?:hep(?:arin)?)\s*(?:gtt|drip|infusion|nomogram)\b|\bhep(?:arin)?\b[^.\n]{0,20}\bunits?/(?:kg/)?h(?:r|our)?\b"
    r"|\bwarfarin\b|\bcoumadin\b|\bapixaban\b|\beliquis\b|\brivaroxaban\b|\bxarelto\b|\bdabigatran\b"
    r"|\bpradaxa\b|\bedoxaban\b|\bargatroban\b|\bbivalirudin\b"
    r"|\b(?:enoxaparin|lovenox)\b[^.\n]{0,20}\b(?:1\s*mg/kg|BID|q12h?)\b"
    r"|\b(?:therapeutic|full[- ]dose) (?:anticoagulation|AC|lovenox|enoxaparin|heparin)\b",
    re.IGNORECASE)
_AC_INACTIVE = re.compile(
    r"\b(?:hold|holding|held|on hold|stop\w*|d/?c'?d?|discontinu\w*|off|avoid|contraindicat\w*|reverse\w*"
    r"|reversal|resume|restart|consider|no|not|home|PTA|prior to admission|outpatient|allerg\w*|until|when|if"
    r"|last dose|h/o|history)\b", re.IGNORECASE)
_NO_AC = re.compile(
    r"\b(?:no|not on|off|hold(?:ing)?|held|avoid|contraindicat\w*)\b[^.;\n]{0,25}"
    r"\b(?:anticoagula\w*|AC|AC/AP|AP/AC|blood thinners?)\b"
    r"|\b(?:anticoagula\w*|AC)\b[^.;\n]{0,15}\b(?:contraindicated|held|on hold)\b", re.IGNORECASE)


def _ac_conflicts(lines: list[str]) -> list[Conflict]:
    no_ac = [ln for ln in lines if _NO_AC.search(ln) and not _THERAPEUTIC_AC.search(ln)]
    active = [ln for ln in lines if _THERAPEUTIC_AC.search(ln) and not _AC_INACTIVE.search(ln)]
    if no_ac and active:
        return [Conflict("anticoagulation", "no/held anticoagulation and an active anticoagulant",
                         [_clean(no_ac[0]), _clean(active[0])])]
    return []


# ---------------------------------------------------------------- allergy
_ALLERGY_LINE = re.compile(r"\ballerg(?:y|ies|ic)\b|\bALLG\b|\bHIT\b", re.IGNORECASE)
_NKA = re.compile(r"\bNKD?A\b|\bno known (?:drug )?allerg", re.IGNORECASE)
# allergen word -> the drugs it covers
_CLASSES: dict[str, tuple[str, ...]] = {
    "penicillin": ("penicillin", "amoxicillin", "ampicillin", "piperacillin", "zosyn", "unasyn", "augmentin",
                   "nafcillin", "oxacillin", "dicloxacillin", "pip-tazo", "pip/tazo"),
    "pcn": ("penicillin", "amoxicillin", "ampicillin", "piperacillin", "zosyn", "unasyn", "augmentin",
            "nafcillin", "oxacillin", "dicloxacillin", "pip-tazo", "pip/tazo"),
    "sulfa": ("sulfamethoxazole", "bactrim", "tmp-smx", "tmp/smx", "smx-tmp", "sulfadiazine", "sulfasalazine"),
    "hit": ("heparin", "hep gtt", "enoxaparin", "lovenox"),
    "heparin": ("heparin", "hep gtt", "enoxaparin", "lovenox"),
}
_NOT_ALLERGEN = frozenset(
    "allergy allergies allergic allg reaction reactions rash hives urticaria anaphylaxis angioedema swelling "
    "itching itch pruritus nausea vomiting unknown intolerance known drug drugs seasonal environmental "
    "patient reports with without from and the has have to of none no other food foods latex shellfish "
    "iodine contrast dye tape adhesive peanuts nuts eggs milk severe mild moderate reviewed updated "
    "listed noted history unable obtain verify ".split())
_MED_LINE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|units?|mEq|mL/h(?:r)?|mg/kg|mcg/kg/min)\b"
                       r"|\b(?:IV|PO|SQ|SC|IM|IVPB|PR|NG|gtt|drip|q\d{1,2}h|qd|daily|BID|TID|QID|PRN|qhs)\b"
                       r"|\b(?:start\w*|continue|cont|on|received|given)\b", re.IGNORECASE)
_MED_INACTIVE = re.compile(r"\b(?:allerg\w*|ALLG|avoid|hold|held|stop\w*|d/?c'?d?|discontinu\w*|no|not|switch\w* from"
                           r"|changed from|instead of|rather than|reaction|HIT)\b", re.IGNORECASE)


def _allergens(lines: list[str]) -> dict[str, str]:
    """allergen drug word -> the allergy line that names it."""
    out: dict[str, str] = {}
    for line in lines:
        if not _ALLERGY_LINE.search(line) or _NKA.search(line) and not re.search(r"[,;]", line):
            continue
        if re.search(r"\bHIT\b", line) and not re.search(r"\b(?:no|neg\w*|r/o|rule out|low (?:probability|suspicion))\b[^.\n]{0,15}\bHIT\b",
                                                           line, re.IGNORECASE):
            out.setdefault("hit", line)
        body = re.sub(r"^.*?\b(?:allerg(?:y|ies|ic)|ALLG)\b\s*(?:to)?\s*:?", "", line, flags=re.IGNORECASE) \
            if re.search(r"\b(?:allerg(?:y|ies|ic)|ALLG)\b\s*(?:to)?\s*:", line, re.IGNORECASE) else line
        for word in re.findall(r"[A-Za-z][A-Za-z/-]{2,}", body):
            w = word.casefold()
            if w in _NOT_ALLERGEN or w in ("hit",):
                continue
            if w in _CLASSES or len(w) >= 5:
                out.setdefault(w, line)
    return out


def _allergy_conflicts(all_lines: list[str], latest: list[str]) -> list[Conflict]:
    allergens = _allergens(all_lines)
    if not allergens:
        return []
    out: list[Conflict] = []
    seen: set[str] = set()
    for line in latest:
        if _ALLERGY_LINE.search(line) or not _MED_LINE.search(line) or _MED_INACTIVE.search(line):
            continue
        low = line.casefold()
        for allergen, source in allergens.items():
            drugs = _CLASSES.get(allergen, (allergen,))
            hit = next((d for d in drugs if re.search(rf"(?<![\w-]){re.escape(d)}(?![\w-])", low)), None)
            if hit and allergen not in seen:
                seen.add(allergen)
                label = allergen.upper() if allergen in ("pcn", "hit") else allergen
                why = f"allergy listed ({label}) and {hit} on a medication line"
                out.append(Conflict("allergy", why, [_clean(source), _clean(line)]))
    return out


# ---------------------------------------------------------------- fever
_AFEBRILE = re.compile(r"\bafebrile\b|\bno fevers?\b", re.IGNORECASE)
_TEMP = re.compile(r"\b(?:T\s*max|Tmax|Tm|T|temp(?:erature)?)\b\s*(?:of|was|:|=)?\s*"
                   r"(?P<v>\d{2,3}(?:\.\d)?)\s*(?:°\s*)?(?P<u>[CF])?\b")


def _fever_conflicts(lines: list[str]) -> list[Conflict]:
    afebrile = [ln for ln in lines if _AFEBRILE.search(ln)]
    if not afebrile:
        return []
    for line in lines:
        for m in _TEMP.finditer(line):
            v = float(m.group("v"))
            unit = m.group("u") or ("C" if 30 <= v <= 45 else "F" if 90 <= v <= 113 else "")
            if (unit == "C" and 38.0 <= v <= 45) or (unit == "F" and 100.4 <= v <= 113):
                other = next((a for a in afebrile if a != line), afebrile[0])
                return [Conflict("fever", f"afebrile, but a temperature of {m.group(0).strip()}",
                                 [_clean(other), _clean(line)])]
    return []


# ---------------------------------------------------------------- report
@dataclass
class Conflict:
    kind: str           # one of KINDS
    why: str            # what was paired, in plain words
    lines: list[str]    # the chart lines, verbatim

    def __post_init__(self) -> None:
        self.lines = list(dict.fromkeys(self.lines))  # both sides on one line: quote it once

    def to_text(self) -> str:
        quoted = "\n".join(f"    “{ln}”" for ln in self.lines)
        return f"{self.kind}: {self.why}\n{quoted}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "why": self.why, "lines": list(self.lines)}


@dataclass
class ConflictReport:
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.conflicts

    def to_text(self) -> str:
        if not self.conflicts:
            return ""
        out = ["Possible contradictions (latest note — check which line is right)"]
        out.extend(f"- {c.to_text()}" for c in self.conflicts)
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"conflicts": [c.to_dict() for c in self.conflicts], "text": self.to_text()}


def _clean(line: str) -> str:
    return _BULLET.sub("", line.strip())[:240]


def _usable(text: str) -> list[str]:
    """Lines of ``text``; a line joins the one before only when it plainly continues it
    (starts lowercase). Looser rejoining would pair unrelated short plan lines."""
    out: list[str] = []
    for line in text.split("\n"):
        if out and re.match(r"\s*[a-z]", line) and not re.search(r"[.:;!?]\s*$", out[-1]) \
                and not _BULLET.match(line):
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
    return [ln for ln in out if ln.strip() and not _HEADING.match(ln)]


def build(text: str) -> ConflictReport:
    """Pairs of latest-note lines that can't both be true (allergies read the whole chart)."""
    from .bundle import latest_note

    if not text.strip():
        return ConflictReport()
    latest = _usable(latest_note(text))
    found: list[Conflict] = []
    for check in (_side_conflicts, _diet_conflicts, _code_conflicts, _ac_conflicts, _fever_conflicts):
        found.extend(check(latest))
    found.extend(_allergy_conflicts(_usable(text), latest))
    order = {k: i for i, k in enumerate(KINDS)}
    found.sort(key=lambda c: order.get(c.kind, len(KINDS)))
    return ConflictReport(found)
