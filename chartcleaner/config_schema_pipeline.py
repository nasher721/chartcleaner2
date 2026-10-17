"""Pipeline schemas: stage options, dedup, wrapper/order, custom rules, local LLM
(split from ``config_validator.py``)."""

from __future__ import annotations

import re

__all__ = ["PipelineSchemaMixin"]


class PipelineSchemaMixin:
    """Validates pipeline-assembly groups (mixed into ``ConfigValidator``)."""

    cfg: dict
    builtin_stage_ids: list[str]
    errors: list[str]
    warnings: list[str]

    def _validate_stage_options(self) -> None:
        so = self.cfg.get("stage_options")
        if so is None:
            return
        if not isinstance(so, dict):
            self.errors.append("stage_options: must be an object mapping stage id -> options")
            return
        scope_sids = ("metadata_lines", "boilerplate", "learned_rules")
        for sid, opts in so.items():
            if not isinstance(opts, dict):
                self.errors.append(f"stage_options.{sid}: must be an object")
                continue
            if "case_sensitive" in opts and not isinstance(opts["case_sensitive"], bool):
                self.errors.append(f"stage_options.{sid}.case_sensitive: must be true/false")
            if "enabled" in opts and not isinstance(opts["enabled"], bool):
                self.errors.append(f"stage_options.{sid}.enabled: must be true/false")
            if "exceptions" in opts and not (isinstance(opts["exceptions"], list) and all(
                    isinstance(x, str) for x in opts["exceptions"])):
                self.errors.append(f"stage_options.{sid}.exceptions: must be a list of text")
            if "preserve_tables" in opts and not isinstance(opts["preserve_tables"], bool):
                self.errors.append(f"stage_options.{sid}.preserve_tables: must be true/false")
            if sid in scope_sids and "scope" in opts:
                scope = opts["scope"]
                if (not isinstance(scope, dict)
                        or scope.get("mode") not in ("all", "only", "except")
                        or not isinstance(scope.get("sections", []), list)
                        or not all(isinstance(x, str) for x in scope.get("sections", []))):
                    self.errors.append(
                        f"stage_options.{sid}.scope: must be "
                        f'{{"mode": "all"|"only"|"except", "sections": [names]}}')

    def _validate_deduplication(self) -> None:
        self._validate_duplicate_notes()
        self._validate_fuzzy_dedup()
        self._validate_fuzzy_line_dedup()

    def _validate_fuzzy_line_dedup(self) -> None:
        f = self.cfg.get("fuzzy_line_dedup") or {}
        if not isinstance(f, dict):
            self.errors.append("fuzzy_line_dedup: must be an object")
            return
        if not isinstance(f.get("enabled", False), bool):
            self.errors.append("fuzzy_line_dedup.enabled: must be true/false")
        t = f.get("threshold", 92)
        if not isinstance(t, int) or not 50 <= t <= 100:
            self.warnings.append("fuzzy_line_dedup.threshold: expected integer 50–100")
        m = f.get("min_chars", 8)
        if not isinstance(m, int) or m < 0:
            self.warnings.append("fuzzy_line_dedup.min_chars: expected non-negative integer")

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

    def _validate_local_llm(self) -> None:
        from .summarizer import DEFAULT_LLM  # lazy: keeps urllib out of every engine import
        llm = self._check_option_group(
            "local_llm",
            DEFAULT_LLM,
            {
                "base_url": ((str,), "a string URL"),
                "model": ((str,), "a string model name"),
                "prompt_preset": ((str,), "a preset name"),
                "custom_prompt": ((str,), "a string prompt"),
                "grounding_threshold": (((int, float),), "a number 0-100"),
                "context_tokens": ((int,), "an integer token limit (1024-131072)"),
            },
        )
        if llm is not None and "context_tokens" in llm and (
            isinstance(llm["context_tokens"], bool)
            or not isinstance(llm["context_tokens"], int)
            or not 1024 <= llm["context_tokens"] <= 131072
        ):
            self.errors.append("local_llm.context_tokens: must be an integer between 1024 and 131072")
