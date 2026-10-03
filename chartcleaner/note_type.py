"""Detect what kind of note a chart is (discharge summary, H&P, progress…).

Each type has header/phrase cues; the type with the most distinct cues wins
when it has at least two and beats the runner-up. ``note_profiles.detect`` in
config.json adds cues (``{"discharge_summary": ["Discharge Plan"]}``) or new
types. Which preset to use per type is a personal preference stored in the
app's prefs (``note_presets``), not in config.json, because applying a preset
replaces config.json.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["NOTE_TYPES", "LABELS", "Detection", "detect"]

NOTE_TYPES: dict[str, list[str]] = {
    "discharge_summary": ["Discharge Summary", "Hospital Course", "Discharge Diagnos[ie]s",
                          "Discharge Medications", "Discharge Instructions", "Discharge Condition",
                          "Disposition", "Follow-?up Appointments"],
    "h_and_p": ["History and Physical", "H&P", "Admission History", "Chief Complaint",
                "History of Present Illness", "Review of Systems", "Past Medical History",
                "Social History", "Family History"],
    "progress": ["Progress Note", "Interval History", "Overnight Events", "24[- ]hour events",
                 "Subjective", "Hospital Day", "Daily Progress"],
    "consult": ["Consult(?:ation)? Note", "Reason for Consult(?:ation)?", "Requesting (?:Physician|Provider)",
                "Thank you for (?:this|the) (?:consult|referral)", "Recommendations"],
    "nursing": ["Nursing (?:Note|Assessment)", "Shift Assessment", "Fall Risk", "Braden",
                "Intake/Output", "Patient Education", "Skin Assessment"],
    "operative": ["Operative Report", "Procedure Note", "Pre-?operative Diagnosis",
                  "Post-?operative Diagnosis", "Estimated Blood Loss", "Anesthesia", "Specimens?"],
    "radiology": ["IMPRESSION", "FINDINGS", "TECHNIQUE", "COMPARISON", "Radiologist"],
}
LABELS = {
    "discharge_summary": "Discharge summary", "h_and_p": "History & physical",
    "progress": "Progress note", "consult": "Consult note", "nursing": "Nursing note",
    "operative": "Operative / procedure note", "radiology": "Radiology report",
}


@dataclass
class Detection:
    note_type: str | None
    label: str
    scores: dict[str, int] = field(default_factory=dict)


def _cues(cfg: dict | None) -> dict[str, list[str]]:
    cues = {k: list(v) for k, v in NOTE_TYPES.items()}
    extra = ((cfg or {}).get("note_profiles") or {}).get("detect") if isinstance(cfg, dict) else None
    if isinstance(extra, dict):
        for kind, phrases in extra.items():
            if isinstance(kind, str) and isinstance(phrases, list):
                cues.setdefault(kind, []).extend(p for p in phrases if isinstance(p, str) and p)
    return cues


def detect(text: str, cfg: dict | None = None) -> Detection:
    head = text[:20000]  # the type shows in headers near the top; keeps this fast
    scores = {}
    for kind, phrases in _cues(cfg).items():
        hits = 0
        for phrase in phrases:
            try:
                if re.search(rf"(?<!\w){phrase}(?!\w)", head, re.IGNORECASE):
                    hits += 1
            except re.error:
                continue
        if hits:
            scores[kind] = hits
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        kind = ranked[0][0]
        return Detection(kind, LABELS.get(kind, kind.replace("_", " ").capitalize()), scores)
    return Detection(None, "Not sure", scores)
