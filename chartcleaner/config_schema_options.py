"""Stage option-group schemas: whitespace through note templates (split from
``config_validator.py``)."""

from __future__ import annotations

import re

__all__ = ["OptionsSchemaMixin"]


class OptionsSchemaMixin:
    """Validates per-stage option groups (mixed into ``ConfigValidator``)."""

    cfg: dict
    default_options: dict[str, dict]
    errors: list[str]
    warnings: list[str]

    def _validate_option_groups(self) -> None:
        self._validate_whitespace_options()
        self._validate_bullet_options()
        self._validate_header_options()
        self._validate_unicode_options()
        self._validate_timestamp_options()
        self._validate_section_options()
        self._validate_caps_options()
        self._validate_line_length_options()
        self._validate_imaging_options()
        self._validate_hospital_day_options()
        self._validate_compactor_options()
        self._validate_note_profiles()
        self._validate_prompt_templates()
        self._validate_note_templates()
        self._check_option_group("fact_check", {"enabled": True},
                                 {"enabled": ((bool,), "true/false")})
        self._check_option_group(
            "clinical_identifiers",
            {"enabled": False, "replacement": None, "redact_npi": True, "redact_dea": True,
             "redact_udi": True},
            {"enabled": ((bool,), "true/false"), "replacement": ((str, type(None)), "text or null"),
             "redact_npi": ((bool,), "true/false"), "redact_dea": ((bool,), "true/false"),
             "redact_udi": ((bool,), "true/false")})

    def _validate_prompt_templates(self) -> None:
        templates = self.cfg.get("prompt_templates")
        if templates is None:
            return
        if not isinstance(templates, list):
            self.errors.append("prompt_templates: must be a list")
            return
        for i, t in enumerate(templates):
            if not (isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip()
                    and isinstance(t.get("template"), str)):
                self.errors.append(f"prompt_templates[{i}]: needs a name and a template")
            elif t.get("format", "text") not in ("text", "markdown", "xml"):
                self.errors.append(f"prompt_templates[{i}].format: must be text, markdown or xml")

    def _validate_note_templates(self) -> None:
        templates = self.cfg.get("note_templates")
        if templates is None:
            return
        if not isinstance(templates, list):
            self.errors.append("note_templates: must be a list")
            return
        for i, t in enumerate(templates):
            if not (isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip()
                    and isinstance(t.get("template"), str)):
                self.errors.append(f"note_templates[{i}]: needs a name and a template")

    def _validate_note_profiles(self) -> None:
        profiles = self.cfg.get("note_profiles")
        if profiles is None:
            return
        detect = profiles.get("detect", {}) if isinstance(profiles, dict) else None
        if not isinstance(detect, dict) or not all(
                isinstance(k, str) and isinstance(v, list) for k, v in detect.items()):
            self.errors.append("note_profiles.detect: must map note types to lists of phrases")
            return
        for kind, phrases in detect.items():
            for i, phrase in enumerate(phrases):
                self._check_regex(phrase, f"note_profiles.detect.{kind}[{i}]", re.IGNORECASE)

    def _validate_compactor_options(self) -> None:
        labs = self._check_option_group(
            "lab_compaction", self.default_options["lab_compaction"],
            {"enabled": ((bool,), "true/false"), "style": ((str,), "line/fishbone"),
             "latest_only": ((bool,), "true/false"), "keep_reference_ranges": ((bool,), "true/false")})
        if labs is not None and labs.get("style", "line") not in ("line", "fishbone"):
            self.errors.append('lab_compaction.style: must be "line" or "fishbone"')
        meds = self._check_option_group(
            "med_normalize", self.default_options["med_normalize"],
            {"enabled": ((bool,), "true/false"), "drop_fields": ((list,), "a list of regex strings"),
             "route_map": ((dict,), "an object of phrase -> route"),
             "frequency_map": ((dict,), "an object of phrase -> frequency")})
        if meds is not None:
            for i, pattern in enumerate(meds.get("drop_fields") or []):
                self._check_regex(pattern, f"med_normalize.drop_fields[{i}]", re.IGNORECASE)
            from .abbreviation_safety import is_blocked
            for key in ("route_map", "frequency_map"):
                for phrase, short in (meds.get(key) or {}).items():
                    if isinstance(short, str) and is_blocked(short, str(phrase)):
                        self.errors.append(f"med_normalize.{key}: “{short}” for “{phrase}” is a "
                                           "Do Not Use abbreviation")
        vit = self._check_option_group("vitals_summary", self.default_options["vitals_summary"],
                                       {"enabled": ((bool,), "true/false"),
                                        "window_hours": ((int, float), "hours, 0 = all readings")})
        if vit is not None and "window_hours" in vit and (
                isinstance(vit["window_hours"], bool)
                or not isinstance(vit["window_hours"], (int, float))
                or vit["window_hours"] < 0):
            self.errors.append("vitals_summary.window_hours: must be a non-negative number of hours")
        neuro = self._check_option_group(
            "neuro_summary", self.default_options["neuro_summary"],
            {"enabled": ((bool,), "true/false"), "neuro_checks": ((bool,), "true/false"),
             "evd": ((bool,), "true/false"), "sodium": ((bool,), "true/false"),
             "drips": ((bool,), "true/false"), "icp_threshold": ((int, float), "a number")})
        if neuro is not None and isinstance(neuro.get("icp_threshold"), bool):
            self.errors.append("neuro_summary.icp_threshold: wrong type (expected a number)")

    def _validate_imaging_options(self) -> None:
        from .compactors.imaging import KEEPABLE
        im = self._check_option_group(
            "imaging_impression", self.default_options["imaging_impression"],
            {"enabled": ((bool,), "true/false"), "keep": ((list,), "a list of section names")})
        if im is not None and isinstance(im.get("keep"), list):
            for name in im["keep"]:
                if name not in KEEPABLE:
                    self.errors.append(f"imaging_impression.keep: unknown section {name!r} "
                                       f"(use {', '.join(KEEPABLE)})")

    def _validate_hospital_day_options(self) -> None:
        from datetime import date
        hd = self._check_option_group(
            "hospital_day", self.default_options["hospital_day"],
            {"enabled": ((bool,), "true/false"), "admit_date": ((str,), '"auto" or YYYY-MM-DD'),
             "surgery_dates": ((list,), "a list of YYYY-MM-DD dates"), "style": ((str,), "append/replace")})
        if hd is None:
            return
        dates = [hd.get("admit_date")] if hd.get("admit_date") not in (None, "auto") else []
        dates += hd.get("surgery_dates") if isinstance(hd.get("surgery_dates"), list) else []
        for value in dates:
            try:
                date.fromisoformat(str(value))
            except ValueError:
                self.errors.append(f"hospital_day: {value!r} is not a YYYY-MM-DD date")
        if hd.get("style", "append") not in ("append", "replace"):
            self.errors.append('hospital_day.style: must be "append" or "replace"')

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
