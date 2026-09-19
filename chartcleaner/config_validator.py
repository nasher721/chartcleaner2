"""Configuration validation for Chart Cleaner pipelines.

Validates the structure and syntax of config.json rules, stage options,
deduplication thresholds, NLP settings, and audit rules.
"""

from __future__ import annotations

import re
from typing import Any

from .audit import AUDIT_CHECK_IDS

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
        "stage_options",
        "clinical_identifiers",
    }
)

KNOWN_PRESIDIO_ENTITIES = frozenset({
    "PERSON", "LOCATION", "ORGANIZATION", "NRP", "DATE_TIME", "PHONE_NUMBER",
    "EMAIL_ADDRESS", "URL", "IP_ADDRESS", "MEDICAL_LICENSE", "US_SSN",
    "US_DRIVER_LICENSE", "US_BANK_NUMBER", "CREDIT_CARD", "IBAN_CODE",
})


class ConfigValidator:
    """Validates a Chart Cleaner configuration dictionary against expected schemas."""

    def __init__(self, cfg: dict, builtin_stage_ids: list[str], default_options: dict[str, dict]):
        self.cfg = cfg
        self.builtin_stage_ids = builtin_stage_ids
        self.default_options = default_options
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def validate(self) -> tuple[list[str], list[str]]:
        """Run all validation passes and return accumulated (errors, warnings)."""
        self._validate_required_regex_lists()
        self._validate_required_regex_pairs()
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
        self._check_unknown_keys()
        return self.errors, self.warnings

    # -- primitives ---------------------------------------------------------

    def _check_regex(self, pattern: Any, context: str, flags: int = 0) -> None:
        """Verify that a value is a valid non-empty regular expression."""
        if not isinstance(pattern, str) or not pattern:
            self.errors.append(f"{context}: pattern must be a non-empty string")
            return
        try:
            re.compile(pattern, flags=flags)
        except re.error as e:
            self.errors.append(f"{context}: invalid regex ({e})")

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

    # -- stage validations --------------------------------------------------

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
            for i, pair in enumerate(val):
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    self.errors.append(f"{key}[{i}]: must be a two-element [pattern, replacement] list")
                    continue
                self._check_regex(pair[0], f"{key}[{i}] pattern", re.IGNORECASE)
                if not isinstance(pair[1], str):
                    self.errors.append(f"{key}[{i}] replacement: must be a string")

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

    def _validate_option_groups(self) -> None:
        self._validate_whitespace_options()
        self._validate_bullet_options()
        self._validate_header_options()
        self._validate_unicode_options()
        self._validate_timestamp_options()
        self._validate_section_options()
        self._validate_caps_options()
        self._validate_line_length_options()

    def _validate_whitespace_options(self) -> None:
        defaults = self.default_options["whitespace"]
        self._check_option_group(
            "whitespace", defaults, {k: ((bool,), "true/false") for k in defaults}
        )

    def _validate_bullet_options(self) -> None:
        defaults = self.default_options["bullets"]
        self._check_option_group(
            "bullets", defaults,
            {
                "style": ((str,), "a short string"),
                "normalize_numbered": ((bool,), "true/false"),
                "skip_empty": ((bool,), "true/false"),
            },
        )

    def _validate_header_options(self) -> None:
        defaults = self.default_options["header_options"]
        ho = self._check_option_group(
            "header_options", defaults,
            {
                "level": ((int,), "integer 1–6"),
                "bold": ((bool,), "true/false"),
                "keep_colon": ((bool,), "true/false"),
                "uppercase_only": ((bool,), "true/false"),
            },
        )
        if ho is not None and isinstance(ho.get("level"), int) and not 1 <= ho["level"] <= 6:
            self.warnings.append("header_options.level: expected 1–6")

    def _validate_unicode_options(self) -> None:
        defaults = self.default_options["unicode_normalize"]
        self._check_option_group(
            "unicode_normalize", defaults, {k: ((bool,), "true/false") for k in defaults}
        )

    def _validate_timestamp_options(self) -> None:
        defaults = self.default_options["timestamp_removal"]
        ts = self._check_option_group(
            "timestamp_removal", defaults,
            {
                "enabled": ((bool,), "true/false"),
                "replacement": ((str,), "a string"),
                "builtin_patterns": ((bool,), "true/false"),
                "extra_patterns": ((list,), "a list of regex strings"),
                "remove_clock_times": ((bool,), "true/false"),
            },
        )
        if ts is not None and isinstance(ts.get("extra_patterns"), list):
            for i, p in enumerate(ts["extra_patterns"]):
                self._check_regex(p, f"timestamp_removal.extra_patterns[{i}]", re.IGNORECASE)

    def _validate_section_options(self) -> None:
        defaults = self.default_options["section_filter"]
        sf = self._check_option_group(
            "section_filter", defaults,
            {
                "mode": ((str,), "'off', 'drop' or 'keep'"),
                "sections": ((list,), "a list of header names"),
                "boundary_headers": ((list,), "a list of header names"),
                "keep_preamble": ((bool,), "true/false"),
            },
        )
        if sf is not None:
            mode = sf.get("mode", "off")
            if isinstance(mode, str) and mode not in ("off", "drop", "keep"):
                self.errors.append("section_filter.mode: must be 'off', 'drop' or 'keep'")
            self._check_name_list(sf.get("sections"), "section_filter.sections")
            if sf.get("boundary_headers"):
                self._check_name_list(sf.get("boundary_headers"), "section_filter.boundary_headers")

    def _validate_caps_options(self) -> None:
        defaults = self.default_options["caps_normalize"]
        cp = self._check_option_group(
            "caps_normalize", defaults,
            {
                "mode": ((str,), "'off' or 'sentence'"),
                "min_chars": ((int,), "a positive integer"),
                "preserve_words": ((list,), "a list of words"),
            },
        )
        if cp is not None:
            if isinstance(cp.get("mode"), str) and cp["mode"] not in ("off", "sentence"):
                self.errors.append("caps_normalize.mode: must be 'off' or 'sentence'")
            if isinstance(cp.get("min_chars"), int) and cp["min_chars"] < 1:
                self.warnings.append("caps_normalize.min_chars: expected a positive integer")

    def _validate_line_length_options(self) -> None:
        defaults = self.default_options["line_length"]
        ll = self._check_option_group(
            "line_length", defaults,
            {
                "mode": ((str,), "'off', 'truncate' or 'wrap'"),
                "max_chars": ((int,), "an integer ≥ 10"),
                "marker": ((str,), "a short string"),
            },
        )
        if ll is not None:
            if isinstance(ll.get("mode"), str) and ll["mode"] not in ("off", "truncate", "wrap"):
                self.errors.append("line_length.mode: must be 'off', 'truncate' or 'wrap'")
            if isinstance(ll.get("max_chars"), int) and ll["max_chars"] < 10:
                self.warnings.append("line_length.max_chars: expected an integer ≥ 10")

    def _validate_stage_options(self) -> None:
        so = self.cfg.get("stage_options")
        if so is None:
            return
        if not isinstance(so, dict):
            self.errors.append("stage_options: must be an object mapping stage id -> options")
            return
        for sid, opts in so.items():
            if not isinstance(opts, dict):
                self.errors.append(f"stage_options.{sid}: must be an object")
                continue
            if "case_sensitive" in opts and not isinstance(opts["case_sensitive"], bool):
                self.errors.append(f"stage_options.{sid}.case_sensitive: must be true/false")
            if "enabled" in opts and not isinstance(opts["enabled"], bool):
                self.errors.append(f"stage_options.{sid}.enabled: must be true/false")

    def _validate_deduplication(self) -> None:
        self._validate_duplicate_notes()
        self._validate_fuzzy_dedup()

    def _validate_duplicate_notes(self) -> None:
        d = self.cfg.get("duplicate_note_detection") or {}
        if not isinstance(d, dict):
            self.errors.append("duplicate_note_detection: must be an object")
            return
        if not isinstance(d.get("enabled", True), bool):
            self.errors.append("duplicate_note_detection.enabled: must be true/false")
        if "split_pattern" in d:
            self._check_regex(
                d["split_pattern"], "duplicate_note_detection.split_pattern", re.MULTILINE | re.IGNORECASE
            )
        for intkey, lo, hi in (("min_body_chars", 0, 1_000_000), ("similarity_threshold", 50, 100)):
            v = d.get(intkey)
            if v is not None and (not isinstance(v, int) or not lo <= v <= hi):
                self.warnings.append(f"duplicate_note_detection.{intkey}: expected integer {lo}–{hi}")

    def _validate_fuzzy_dedup(self) -> None:
        f = self.cfg.get("fuzzy_dedup") or {}
        if not isinstance(f, dict):
            self.errors.append("fuzzy_dedup: must be an object")
            return
        if not isinstance(f.get("enabled", True), bool):
            self.errors.append("fuzzy_dedup.enabled: must be true/false")
        t = f.get("threshold", 95)
        if not isinstance(t, int) or not 50 <= t <= 100:
            self.warnings.append("fuzzy_dedup.threshold: expected integer 50–100")
        m = f.get("min_chars", 100)
        if not isinstance(m, int) or m < 0:
            self.warnings.append("fuzzy_dedup.min_chars: expected non-negative integer")

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
                self.errors.append(f"audit.checks.{cid}.enabled: must be true/false")
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

    def _validate_wrapper_and_order(self) -> None:
        if "wrap_output" in self.cfg and not isinstance(self.cfg["wrap_output"], bool):
            self.errors.append("wrap_output: must be true/false")
        tag = self.cfg.get("wrap_tag")
        if tag is not None and (not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", tag)):
            self.errors.append("wrap_tag: must be a simple tag name (letters, digits, _ - .)")

        order = self.cfg.get("stage_order")
        if order is not None:
            if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
                self.errors.append("stage_order: must be a list of stage ids")
            else:
                for sid in order:
                    if sid not in self.builtin_stage_ids and not sid.startswith("custom:"):
                        self.warnings.append(f"stage_order: unknown stage id '{sid}' (ignored)")

    def _validate_custom_rules(self) -> None:
        cr = self.cfg.get("custom_rules")
        if cr is not None and not isinstance(cr, dict):
            self.errors.append("custom_rules: must map script name -> {enabled: bool}")

    def _check_unknown_keys(self) -> None:
        for k in self.cfg:
            if k not in KNOWN_CONFIG_KEYS:
                self.warnings.append(f"unknown config key '{k}' (kept as-is, ignored by engine)")
