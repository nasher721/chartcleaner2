"""Configuration validation for Chart Cleaner pipelines.

Validates the structure and syntax of config.json rules, stage options,
deduplication thresholds, NLP settings, and audit rules.

The schemas live in per-group modules (``config_schema_base``,
``config_schema_regex``, ``config_schema_options``, ``config_schema_review``,
``config_schema_pipeline``); this module owns the ``ConfigValidator`` class so
existing imports keep working.
"""

from __future__ import annotations

from .config_schema_base import (
    KNOWN_CONFIG_KEYS,
    KNOWN_PRESIDIO_ENTITIES,
    REQUIRED_CONFIG_KEYS,
    ValidatorBase,
)
from .config_schema_options import OptionsSchemaMixin
from .config_schema_pipeline import PipelineSchemaMixin
from .config_schema_regex import RegexSchemaMixin
from .config_schema_review import ReviewSchemaMixin

__all__ = [
    "REQUIRED_CONFIG_KEYS",
    "KNOWN_CONFIG_KEYS",
    "KNOWN_PRESIDIO_ENTITIES",
    "ConfigValidator",
]


class ConfigValidator(
    ValidatorBase,
    RegexSchemaMixin,
    OptionsSchemaMixin,
    ReviewSchemaMixin,
    PipelineSchemaMixin,
):
    """Validates a Chart Cleaner configuration dictionary against expected schemas."""

    def validate(self) -> tuple[list[str], list[str]]:
        """Run all validation passes and return accumulated (errors, warnings)."""
        self._validate_required_regex_lists()
        self._validate_required_regex_pairs()
        self._validate_optional_regex_pairs()
        self._validate_abbreviations()
        self._validate_clinical_headers()
        self._validate_option_groups()
        self._validate_stage_options()
        self._validate_deduplication()
        self._validate_nlp()
        self._validate_tokenization()
        self._validate_headers_engine()
        self._validate_ingest()
        self._validate_audit()
        self._validate_wrapper_and_order()
        self._validate_custom_rules()
        self._validate_local_llm()
        self._validate_cleaning_validation()
        self._check_unknown_keys()
        return self.errors, self.warnings
