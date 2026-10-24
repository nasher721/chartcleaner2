"""Destination-aware cleaning policies.

A destination is a run-local override, never a saved configuration change. It
makes the safety-relevant defaults explicit while leaving the user's PHI and
metadata rules in place. An empty/None destination preserves legacy behavior.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass

__all__ = ["DESTINATIONS", "LOSSY_STAGES", "DestinationPolicy", "apply_destination",
           "destination_options", "get_destination"]

# Stages that intentionally summarize or delete clinical detail. Destination
# policies never touch metadata, PHI, learned-rule, whitespace, or audit stages.
LOSSY_STAGES = (
    "imaging_impression", "lab_compaction", "med_normalize", "vitals_summary",
    "neuro_summary", "mar_summary", "flowsheet_tables", "vent_summary",
    "duplicate_notes", "fuzzy_dedup", "fuzzy_line_dedup", "sections",
)


@dataclass(frozen=True)
class DestinationPolicy:
    key: str
    label: str
    description: str
    validation_mode: str = "enforce"
    abbreviation_level: str = "conservative"
    preserve_condensing: bool = False
    wrap_output: bool | None = None
    recommended_format: str = "text"

    def public_dict(self) -> dict:
        return asdict(self)


DESTINATIONS = {
    "external_ai": DestinationPolicy(
        "external_ai", "Share with external AI",
        "Enforce clinical-edit validation, use conservative abbreviations, and disable "
        "condensing. Review remaining PHI before sending; de-identification is not guaranteed.",
        wrap_output=True, recommended_format="xml",
    ),
    "personal_review": DestinationPolicy(
        "personal_review", "Personal clinical review",
        "Enforce clinical-edit validation while retaining your configured condensing stages.",
        abbreviation_level="standard", preserve_condensing=True,
    ),
    "epic_paste": DestinationPolicy(
        "epic_paste", "Paste back into Epic",
        "Preserve clinical detail, enforce edit validation, use conservative abbreviations, "
        "and omit the patient-chart wrapper. Choose Epic-safe output when copying.",
        wrap_output=False, recommended_format="smartphrase",
    ),
    "archive": DestinationPolicy(
        "archive", "Detailed local archive",
        "Preserve detail with custom-only abbreviations and no condensing; this is not an "
        "unmodified legal record or a guarantee of de-identification.",
        abbreviation_level="custom_only", wrap_output=False, recommended_format="markdown",
    ),
}


def get_destination(key: str | None) -> DestinationPolicy | None:
    """Resolve a destination key; empty means no override."""
    if key in (None, ""):
        return None
    if not isinstance(key, str) or key not in DESTINATIONS:
        raise ValueError(f"Unknown destination {key!r}; expected one of {', '.join(DESTINATIONS)}")
    return DESTINATIONS[key]


def destination_options() -> dict[str, str]:
    """Labels suitable for a UI select, excluding the legacy no-override option."""
    return {key: policy.label for key, policy in DESTINATIONS.items()}


def apply_destination(config: dict, key: str | None) -> tuple[dict, DestinationPolicy | None]:
    """Return a deep-copied config with run-local destination overrides."""
    policy = get_destination(key)
    if policy is None:
        return config, None
    cfg = deepcopy(config)
    validation = dict(cfg.get("cleaning_validation") or {})
    validation["mode"] = policy.validation_mode
    cfg["cleaning_validation"] = validation
    abbreviations = dict(cfg.get("abbreviations") or {})
    abbreviations["level"] = policy.abbreviation_level
    cfg["abbreviations"] = abbreviations
    if policy.wrap_output is not None:
        cfg["wrap_output"] = policy.wrap_output
    if not policy.preserve_condensing:
        stage_options = dict(cfg.get("stage_options") or {})
        for stage_id in LOSSY_STAGES:
            options = dict(stage_options.get(stage_id) or {})
            options["enabled"] = False
            stage_options[stage_id] = options
        cfg["stage_options"] = stage_options
        # These stages use their own config-group switch rather than stage_options.
        for group in ("duplicate_note_detection", "fuzzy_dedup", "fuzzy_line_dedup"):
            settings = dict(cfg.get(group) or {})
            settings["enabled"] = False
            cfg[group] = settings
    return cfg, policy
