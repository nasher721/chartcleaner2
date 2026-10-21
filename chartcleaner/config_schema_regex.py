"""Regex-list schemas: the required pattern lists and clinical headers (split
from ``config_validator.py``)."""

from __future__ import annotations

import re

from .config_schema_base import REQUIRED_CONFIG_KEYS

__all__ = ["RegexSchemaMixin"]


class RegexSchemaMixin:
    """Validates regex-list groups (mixed into ``ConfigValidator``)."""

    cfg: dict
    errors: list[str]
    warnings: list[str]

    def _validate_required_regex_lists(self) -> None:
        for key in REQUIRED_CONFIG_KEYS[:2]:
            val = self.cfg.get(key)
            if not isinstance(val, list):
                self.errors.append(f"{key}: must be a list of regex strings")
                continue
            if not val:
                self.warnings.append(f"{key}: empty list — stage will do nothing")
            flags = re.IGNORECASE | re.MULTILINE
            if key == "boilerplate":
                flags |= re.DOTALL
            for i, p in enumerate(val):
                self._check_regex(p, f"{key}[{i}]", flags)

    def _validate_required_regex_pairs(self) -> None:
        for key in ("epic_phi_patterns", "literal_replacements"):
            val = self.cfg.get(key)
            if not isinstance(val, list):
                self.errors.append(f"{key}: must be a list of [pattern, replacement] pairs")
                continue
            self._validate_pair_list(key, val)

    def _validate_optional_regex_pairs(self) -> None:
        """learned_rules may be absent (pre-learn configs) but must be well-formed."""
        val = self.cfg.get("learned_rules")
        if val is None:
            return
        if not isinstance(val, list):
            self.errors.append("learned_rules: must be a list of [pattern, replacement] pairs")
            return
        self._validate_pair_list("learned_rules", val)

    def _validate_clinical_headers(self) -> None:
        headers = self.cfg.get("clinical_headers")
        if not isinstance(headers, list) or not all(isinstance(h, str) and h for h in headers):
            self.errors.append("clinical_headers: must be a list of non-empty header names")
            return
        for h in headers:
            try:
                re.compile(rf"^\s*({h})\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
            except re.error as e:
                self.errors.append(f"clinical_headers[{h!r}]: invalid regex ({e})")

    def _validate_rule_contracts(self) -> None:
        """The ``rule_contracts`` sidecar: versioned, keyed entries beside the regex rules."""
        group = self.cfg.get("rule_contracts")
        if group is None:
            return
        if not isinstance(group, dict):
            self.errors.append("rule_contracts: must be an object")
            return
        from .rule_contracts import SCHEMA_VERSION, validate_contract_entry
        version = group.get("schema_version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            self.errors.append("rule_contracts.schema_version: must be a positive integer")
        elif version > SCHEMA_VERSION:
            self.errors.append(f"rule_contracts.schema_version: {version} is newer than this build "
                               f"supports ({SCHEMA_VERSION}); update Chart Cleaner")
        contracts = group.get("contracts", {})
        if not isinstance(contracts, dict):
            self.errors.append("rule_contracts.contracts: must map contract keys to objects")
            return
        for key, entry in contracts.items():
            self.errors.extend(validate_contract_entry(str(key), entry))
        for key in group:
            if key not in ("schema_version", "contracts"):
                self.warnings.append(f"rule_contracts.{key}: unknown option (kept as-is, ignored)")
