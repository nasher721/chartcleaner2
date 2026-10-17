"""Review/redaction schemas: abbreviations, NLP, tokenization, audit (split from
``config_validator.py``)."""

from __future__ import annotations

import re
from typing import Any

from .audit import AUDIT_CHECK_IDS
from .config_schema_base import KNOWN_PRESIDIO_ENTITIES

__all__ = ["ReviewSchemaMixin"]


class ReviewSchemaMixin:
    """Validates redaction/review groups (mixed into ``ConfigValidator``)."""

    cfg: dict
    errors: list[str]
    warnings: list[str]

    def _validate_abbreviations(self) -> None:
        if "abbreviations" not in self.cfg:
            return
        group = self.cfg["abbreviations"]
        if not isinstance(group, dict):
            self.errors.append("abbreviations: must be an object")
            return
        disabled = group.get("disabled", [])
        if not isinstance(disabled, list) or not all(
                isinstance(term, str) and term.strip() for term in disabled):
            self.errors.append("abbreviations.disabled: must be a list of non-empty terms")
        if "scope" in group:
            scope = group["scope"]
            if (not isinstance(scope, dict) or scope.get("mode") not in ("all", "only", "except")
                    or not isinstance(scope.get("sections", []), list)
                    or not all(isinstance(x, str) for x in scope.get("sections", []))):
                self.errors.append('abbreviations.scope: must be {"mode": "all"|"only"|"except", '
                                   '"sections": [names]}')
        if "level" in group:
            from .abbreviations import LEVELS
            if group["level"] not in LEVELS:
                self.errors.append("abbreviations.level: must be " + ", ".join(f'"{x}"' for x in LEVELS))
        prefer = group.get("expand_prefer", {})
        if not isinstance(prefer, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in prefer.items()):
            self.errors.append("abbreviations.expand_prefer: must map abbreviations to meanings")
        rejected = group.get("rejected_suggestions", [])
        if not isinstance(rejected, list) or not all(isinstance(x, str) for x in rejected):
            self.errors.append("abbreviations.rejected_suggestions: must be a list of phrases")
        custom = group.get("custom", [])
        if not isinstance(custom, list):
            self.errors.append("abbreviations.custom: must be a list")
            return
        seen = set()
        for i, entry in enumerate(custom):
            label = f"abbreviations.custom[{i}]"
            if not isinstance(entry, dict):
                self.errors.append(f"{label}: must be an object")
                continue
            for key in ("term", "replacement"):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    self.errors.append(f"{label}.{key}: must be non-empty text")
            if not isinstance(entry.get("enabled", True), bool):
                self.errors.append(f"{label}.enabled: must be true/false")
            if not isinstance(entry.get("acknowledged", False), bool):
                self.errors.append(f"{label}.acknowledged: must be true/false")
            if "pack" in entry and not isinstance(entry["pack"], str):
                self.errors.append(f"{label}.pack: must be text")
            if "sections" in entry and not (isinstance(entry["sections"], list) and all(
                    isinstance(x, str) and x.strip() for x in entry["sections"])):
                self.errors.append(f"{label}.sections: must be a list of section names")
            term = entry.get("term")
            if isinstance(term, str):
                key = term.strip().casefold()
                if key in seen:
                    self.errors.append(f"{label}.term: duplicate term")
                seen.add(key)

    def _validate_nlp(self) -> None:
        n = self.cfg.get("nlp_redaction") or {}
        if not isinstance(n, dict):
            self.errors.append("nlp_redaction: must be an object")
        else:
            if not isinstance(n.get("enabled", True), bool):
                self.errors.append("nlp_redaction.enabled: must be true/false")
            ents = n.get("entities")
            if ents is not None:
                if not isinstance(ents, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in ents.items()
                ):
                    self.errors.append("nlp_redaction.entities: must map entity type -> replacement text")
                else:
                    for k in ents:
                        if k not in KNOWN_PRESIDIO_ENTITIES:
                            self.warnings.append(f"nlp_redaction.entities: '{k}' is not a built-in Presidio entity type")
            st = n.get("score_threshold")
            if st is not None and (not isinstance(st, (int, float)) or not 0 <= st <= 1):
                self.warnings.append("nlp_redaction.score_threshold: expected number between 0 and 1")

        al = self.cfg.get("nlp_allow_list")
        if al is not None and (not isinstance(al, list) or not all(isinstance(x, str) for x in al)):
            self.errors.append("nlp_allow_list: must be a list of strings")

    def _validate_tokenization(self) -> None:
        t = self.cfg.get("tokenization")
        if t is None:
            return
        if not isinstance(t, dict):
            self.errors.append("tokenization: must be an object")
            return
        if not isinstance(t.get("enabled", False), bool):
            self.errors.append("tokenization.enabled: must be true/false")
        pfx = t.get("prefix")
        if pfx is not None and (not isinstance(pfx, str) or not re.fullmatch(r"[A-Za-z0-9_]{0,8}", pfx)):
            self.errors.append("tokenization.prefix: must be 0–8 letters/digits/_")

    def _validate_headers_engine(self) -> None:
        he = self.cfg.get("headers_engine")
        if he is not None and he not in ("regex", "medspacy"):
            self.errors.append("headers_engine: must be 'regex' or 'medspacy'")

    def _validate_ingest(self) -> None:
        ing = self.cfg.get("ingest")
        if ing is None:
            return
        if not isinstance(ing, dict):
            self.errors.append("ingest: must be an object")
        elif "engine" in ing and ing["engine"] not in ("auto", "builtin", "markitdown", "docling"):
            self.errors.append("ingest.engine: must be one of auto, builtin, markitdown, docling")

    def _validate_audit(self) -> None:
        a = self.cfg.get("audit")
        if a is None:
            return
        if not isinstance(a, dict):
            self.errors.append("audit: must be an object")
            return
        if not isinstance(a.get("enabled", True), bool):
            self.errors.append("audit.enabled: must be true/false")
        mf = a.get("max_findings")
        if mf is not None and (not isinstance(mf, int) or mf < 1):
            self.warnings.append("audit.max_findings: expected positive integer")
        checks = a.get("checks")
        if checks is not None:
            self._validate_audit_checks(checks)

    def _validate_audit_checks(self, checks: Any) -> None:
        if not isinstance(checks, dict):
            self.errors.append("audit.checks: must be an object")
            return
        for cid, c in checks.items():
            if cid not in AUDIT_CHECK_IDS:
                self.warnings.append(f"audit.checks: unknown check '{cid}' (ignored)")
                continue
            if not isinstance(c, dict):
                self.errors.append(f"audit.checks.{cid}: must be an object")
                continue
            if not isinstance(c.get("enabled", True), bool):
                self.errors.append(f"audit.checks.{cid}: must be true/false")
            if cid == "long_digits":
                md = c.get("min_digits")
                if md is not None and (not isinstance(md, int) or not 4 <= md <= 20):
                    self.warnings.append("audit.checks.long_digits.min_digits: expected integer 4–20")
            elif cid == "label_names":
                labs = c.get("labels")
                if labs is not None and (not isinstance(labs, list) or not all(isinstance(x, str) and x for x in labs)):
                    self.errors.append("audit.checks.label_names.labels: must be a list of strings")
            elif cid == "residual_chrome":
                pats = c.get("patterns")
                if pats is not None:
                    if not isinstance(pats, list):
                        self.errors.append("audit.checks.residual_chrome.patterns: must be a list of regex strings")
                    else:
                        for i, p in enumerate(pats):
                            self._check_regex(p, f"audit.checks.residual_chrome.patterns[{i}]", re.IGNORECASE | re.MULTILINE)
