"""Validator base: key registries and shared check primitives (split from
``config_validator.py``)."""

from __future__ import annotations

import re
from typing import Any

from .regex_risk import risks as regex_risks

__all__ = [
    "REQUIRED_CONFIG_KEYS",
    "KNOWN_CONFIG_KEYS",
    "KNOWN_PRESIDIO_ENTITIES",
    "ValidatorBase",
]

REQUIRED_CONFIG_KEYS = (
    "emr_line_metadata",
    "boilerplate",
    "epic_phi_patterns",
    "literal_replacements",
    "clinical_headers",
)

KNOWN_CONFIG_KEYS = frozenset(
    set(REQUIRED_CONFIG_KEYS)
    | {
        "duplicate_note_detection",
        "fuzzy_dedup",
        "fuzzy_line_dedup",
        "nlp_redaction",
        "nlp_allow_list",
        "wrap_output",
        "wrap_tag",
        "stage_order",
        "custom_rules",
        "audit",
        "tokenization",
        "headers_engine",
        "ingest",
        "whitespace",
        "bullets",
        "header_options",
        "unicode_normalize",
        "timestamp_removal",
        "section_filter",
        "caps_normalize",
        "line_length",
        "imaging_impression",
        "hospital_day",
        "lab_compaction",
        "med_normalize",
        "vitals_summary",
        "neuro_summary",
        "mar_summary",
        "flowsheet_tables",
        "vent_summary",
        "note_order",
        "note_profiles",
        "prompt_templates",
        "note_templates",
        "stage_options",
        "learned_rules",
        "abbreviations",
        "clinical_identifiers",
        "local_llm",
        "fact_check",
        "cleaning_validation",
        "rule_contracts",
    }
)

KNOWN_PRESIDIO_ENTITIES = frozenset({
    "PERSON", "LOCATION", "ORGANIZATION", "NRP", "DATE_TIME", "PHONE_NUMBER",
    "EMAIL_ADDRESS", "URL", "IP_ADDRESS", "MEDICAL_LICENSE", "US_SSN",
    "US_DRIVER_LICENSE", "US_BANK_NUMBER", "CREDIT_CARD", "IBAN_CODE",
})


class ValidatorBase:
    """Shared state and check primitives for the schema mixins."""

    def __init__(self, cfg: dict, builtin_stage_ids: list[str], default_options: dict[str, dict]):
        self.cfg = cfg
        self.builtin_stage_ids = builtin_stage_ids
        self.default_options = default_options
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def _check_regex(self, pattern: Any, context: str, flags: int = 0) -> None:
        """Verify that a value is a valid non-empty regular expression."""
        if not isinstance(pattern, str) or not pattern:
            self.errors.append(f"{context}: pattern must be a non-empty string")
            return
        try:
            re.compile(pattern, flags=flags)
        except re.error as e:
            self.errors.append(f"{context}: invalid regex ({e})")
            return
        for risk in regex_risks(pattern, flags):
            self.warnings.append(f"{context}: {risk}")

    def _check_option_group(
        self, key: str, defaults: dict, types: dict[str, tuple[tuple[type, ...], str]]
    ) -> dict | None:
        """Validate type correctness for an options dictionary."""
        val = self.cfg.get(key)
        if val is None:
            return None
        if not isinstance(val, dict):
            self.errors.append(f"{key}: must be an object")
            return None
        for fname, (ok_types, extra) in types.items():
            if fname in val and not isinstance(val[fname], ok_types):
                self.errors.append(f"{key}.{fname}: wrong type (expected {extra})")
        for k in val:
            if k not in defaults:
                self.warnings.append(f"{key}.{k}: unknown option (kept as-is, ignored)")
        return val

    def _check_name_list(self, val: Any, context: str) -> None:
        """Verify that a value is a list of valid header/section regex patterns."""
        if not isinstance(val, list) or not all(isinstance(x, str) and x for x in val):
            self.errors.append(f"{context}: must be a list of non-empty names")
            return
        for i, name in enumerate(val):
            try:
                re.compile(rf"^\s*({name})\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
            except re.error as e:
                self.errors.append(f"{context}[{i}] {name!r}: invalid regex ({e})")

    def _validate_pair_list(self, key: str, val: list) -> None:
        for i, pair in enumerate(val):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                self.errors.append(f"{key}[{i}]: must be a two-element [pattern, replacement] list")
                continue
            self._check_regex(pair[0], f"{key}[{i}] pattern", re.IGNORECASE)
            if not isinstance(pair[1], str):
                self.errors.append(f"{key}[{i}] replacement: must be a string")
            elif isinstance(pair[0], str):
                try:
                    re.compile(pair[0], re.IGNORECASE).sub(pair[1], "")
                except re.error as exc:
                    self.errors.append(f"{key}[{i}]: invalid pattern or replacement ({exc})")

    def _check_unknown_keys(self) -> None:
        for k in self.cfg:
            if k not in KNOWN_CONFIG_KEYS:
                self.warnings.append(f"unknown config key '{k}' (kept as-is, ignored by engine)")
