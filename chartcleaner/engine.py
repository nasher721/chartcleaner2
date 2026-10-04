"""Chart Cleaner engine: a configurable, stage-based cleaning pipeline.

Both the CLI (``medical_cleaner.py``) and the desktop app (``app.py``) build on
this module. A pipeline is assembled from ``config.json`` plus any custom
script rules found in ``custom_rules/``; every stage records statistics (match
counts, character deltas) so each run can be tracked, charted, and compared.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config_validator import ConfigValidator, REQUIRED_CONFIG_KEYS
from .custom_rules import (
    CUSTOM_RULE_TEMPLATE,
    CustomRuleError,
    apply_custom_rule,
    list_custom_rules,
    load_custom_module,
)
from . import fact_check as fact_check_mod
from .compactors import hospital_day as hospital_day_compactor
from .compactors import imaging as imaging_compactor
from .compactors import labs as labs_compactor
from .compactors import meds as meds_compactor
from .compactors import neuro as neuro_compactor
from .compactors import vitals as vitals_compactor
from .stages import (
    DEFAULT_BULLETS,
    DEFAULT_CAPS,
    DEFAULT_HEADER_OPTIONS,
    DEFAULT_LINE_LENGTH,
    DEFAULT_NLP_ENTITIES,
    DEFAULT_NOTE_SPLIT,
    DEFAULT_SECTIONS,
    DEFAULT_TIMESTAMPS,
    DEFAULT_UNICODE,
    DEFAULT_WHITESPACE,
    KIND_TO_RUNNER,
    NlpUnavailable,
    RUNNERS,
    get_stage_options,
    rule_id,
)

__all__ = [
    "ConfigError",
    "NlpUnavailable",
    "CustomRuleError",
    "StageStat",
    "STAGE_ANCHORS",
    "rule_id",
    "RunResult",
    "CleanContext",
    "Pipeline",
    "PIPELINE_MODES",
    "clean_text",
    "load_config",
    "save_config",
    "load_default_config",
    "validate_config",
    "list_custom_rules",
    "CUSTOM_RULE_TEMPLATE",
    "BUILTIN_STAGE_IDS",
    "STAGE_LABELS",
    "DEFAULT_WHITESPACE",
    "DEFAULT_BULLETS",
    "DEFAULT_HEADER_OPTIONS",
    "DEFAULT_UNICODE",
    "DEFAULT_TIMESTAMPS",
    "DEFAULT_SECTIONS",
    "DEFAULT_CAPS",
    "DEFAULT_LINE_LENGTH",
]

SCRIPT_DIR = Path(__file__).resolve().parent

BUILTIN_STAGE_IDS = [
    "metadata_lines",
    "boilerplate",
    "tokenize_phi",
    "phi_patterns",
    "clinical_identifiers",
    "nlp_redaction",
    "literal_replacements",
    "learned_rules",
    "unicode_normalize",
    "hospital_day",
    "timestamps",
    "sections",
    "imaging_impression",
    "lab_compaction",
    "med_normalize",
    "vitals_summary",
    "neuro_summary",
    "whitespace",
    "duplicate_notes",
    "fuzzy_dedup",
    "headers",
    "caps_normalize",
    "bullets",
    "medical_abbreviations",
    "line_length",
]

# Where a builtin goes when a saved ``stage_order`` predates it: right after
# its anchor stage instead of the end. Only stages added after this mechanism
# are listed, so older saved orders keep producing the output they always did.
STAGE_ANCHORS: dict[str, str] = {
    "hospital_day": "unicode_normalize",    # must label dates before timestamps can strip them
    "imaging_impression": "sections",
    "lab_compaction": "imaging_impression",
    "med_normalize": "lab_compaction",
    "vitals_summary": "med_normalize",
    "neuro_summary": "vitals_summary",
}

STAGE_LABELS = {
    "metadata_lines": "EMR line metadata",
    "boilerplate": "Boilerplate blocks",
    "phi_patterns": "Structured PHI patterns",
    "clinical_identifiers": "Clinical identifiers (NPI/DEA/UDI)",
    "nlp_redaction": "NLP redaction (Presidio)",
    "tokenize_phi": "Reversible tokenization",
    "literal_replacements": "Literal replacements",
    "medical_abbreviations": "Medical abbreviations",
    "learned_rules": "Learned rules (highlight → rule)",
    "unicode_normalize": "Unicode normalization",
    "timestamps": "Timestamp removal",
    "hospital_day": "Hospital day labels",
    "imaging_impression": "Imaging: keep impression",
    "lab_compaction": "Lab table compaction",
    "med_normalize": "Medication list cleanup",
    "vitals_summary": "Vitals & I/O summary",
    "neuro_summary": "Neuro ICU summaries",
    "sections": "Section keep/drop",
    "whitespace": "Whitespace cleanup",
    "duplicate_notes": "Duplicate note folding",
    "fuzzy_dedup": "Fuzzy paragraph dedup",
    "headers": "Header promotion",
    "caps_normalize": "ALL-CAPS normalization",
    "bullets": "Bullet normalization",
    "line_length": "Long-line handling",
}

# kind drives the editor the app shows for a stage
STAGE_KINDS = {
    "metadata_lines": "regex_lines",
    "boilerplate": "regex_lines",
    "phi_patterns": "regex_pairs",
    "clinical_identifiers": "clinical_identifiers",
    "nlp_redaction": "nlp",
    "tokenize_phi": "tokenize",
    "literal_replacements": "regex_pairs",
    "medical_abbreviations": "abbreviations",
    "learned_rules": "learned",
    "unicode_normalize": "unicode",
    "timestamps": "timestamps",
    "hospital_day": "hospital_day",
    "imaging_impression": "imaging",
    "lab_compaction": "labs",
    "med_normalize": "meds",
    "vitals_summary": "vitals",
    "neuro_summary": "neuro",
    "sections": "sections",
    "whitespace": "whitespace",
    "duplicate_notes": "dedup_notes",
    "fuzzy_dedup": "fuzzy",
    "headers": "headers",
    "caps_normalize": "caps",
    "bullets": "bullets",
    "line_length": "line_length",
}

# Backwards-compatibility alias for app.py imports
_load_custom_module = load_custom_module
_stage_opts = get_stage_options


class ConfigError(Exception):
    """Raised when config.json is missing or invalid."""


# ---------------------------------------------------------------------------
# Configuration Helpers
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> dict:
    """Load and parse JSON configuration from file path."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ConfigError(f"Config file not found: {path}") from e
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        raise ConfigError(f"Config missing required key(s): {', '.join(missing)}")
    return cfg


def save_config(config: dict, path: str | Path) -> None:
    """Atomically write config, keeping a one-generation .bak beside it."""
    path = Path(path)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_default_config() -> dict:
    """The shipped factory-default config, embedded inside the package."""
    return load_config(SCRIPT_DIR / "default_config.json")


def validate_config(cfg: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) without raising."""
    defaults = {
        "whitespace": DEFAULT_WHITESPACE,
        "bullets": DEFAULT_BULLETS,
        "header_options": DEFAULT_HEADER_OPTIONS,
        "unicode_normalize": DEFAULT_UNICODE,
        "timestamp_removal": DEFAULT_TIMESTAMPS,
        "section_filter": DEFAULT_SECTIONS,
        "caps_normalize": DEFAULT_CAPS,
        "line_length": DEFAULT_LINE_LENGTH,
        "imaging_impression": imaging_compactor.DEFAULTS,
        "hospital_day": hospital_day_compactor.DEFAULTS,
        "lab_compaction": labs_compactor.DEFAULTS,
        "med_normalize": meds_compactor.DEFAULTS,
        "vitals_summary": vitals_compactor.DEFAULTS,
        "neuro_summary": neuro_compactor.DEFAULTS,
    }
    validator = ConfigValidator(cfg, BUILTIN_STAGE_IDS, defaults)
    return validator.validate()


# ---------------------------------------------------------------------------
# Execution Context & Statistics Models
# ---------------------------------------------------------------------------

@dataclass
class StageStat:
    id: str
    label: str
    kind: str
    enabled: bool = True
    skipped: bool = False
    error: str | None = None
    matches: int = 0
    chars_before: int = 0
    chars_after: int = 0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "enabled": self.enabled,
            "skipped": self.skipped,
            "error": self.error,
            "matches": self.matches,
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
            "details": self.details,
        }


@dataclass
class RunResult:
    text: str
    wrapped: bool
    stages: list[StageStat]
    warnings: list[str]
    duration_ms: float
    chars_before: int = 0
    words_before: int = 0
    lines_before: int = 0
    # FactReport from fact_check (holds chart text; history keeps summary only)
    fact_check: Any = None

    @property
    def chars_after(self) -> int:
        return len(self.text)

    @property
    def words_after(self) -> int:
        return len(self.text.split())

    @property
    def lines_after(self) -> int:
        return self.text.count("\n") + 1

    @property
    def reduction(self) -> float:
        if not self.chars_before:
            return 0.0
        return round(100.0 * (1.0 - self.chars_after / self.chars_before), 1)

    def phi_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.stages:
            for k, v in (s.details.get("phi") or {}).items():
                out[k] = out.get(k, 0) + int(v)
        return out

    def summary(self) -> str:
        phi = sum(self.phi_counts().values())
        return (
            f"{self.chars_before:,} → {self.chars_after:,} chars "
            f"({self.reduction:+.1f}%), {phi} PHI item(s) redacted"
        )

    def to_history_dict(self, source: str) -> dict:
        def slim_details(details: dict) -> dict:
            # Token maps and tracked changes hold chart text; history never does.
            return {k: v for k, v in details.items() if k not in {"token_map", "changes"}}

        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source,
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
            "words_before": self.words_before,
            "words_after": self.words_after,
            "lines_before": self.lines_before,
            "lines_after": self.lines_after,
            "reduction": self.reduction,
            "duration_ms": round(self.duration_ms, 1),
            "wrapped": self.wrapped,
            "stages": [
                {**s.to_dict(), "details": slim_details(s.details)}
                for s in self.stages
            ],
            "warnings": self.warnings,
            **({"fact_check": self.fact_check.summary()} if self.fact_check else {}),
        }


class CleanContext:
    """Handed to custom script rules: lets them report counts and messages."""

    def __init__(self, config: dict, *, track_changes: bool = False):
        self._config = config
        # When True, built-in stages record each change in details["changes"].
        self.track_changes = track_changes
        self.counters: dict[str, int] = {}
        self.logs: list[str] = []

    def count(self, key: str, n: int = 1) -> None:
        """Increment a named counter (shown in the run's stage statistics)."""
        self.counters[key] = self.counters.get(key, 0) + int(n)

    def log(self, message: str) -> None:
        """Add a note that is shown in the run details."""
        self.logs.append(str(message))

    @property
    def config(self) -> dict:
        """Read-only view of the full config.json."""
        return self._config


@dataclass
class StageSpec:
    id: str
    label: str
    kind: str
    enabled: bool
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline Implementation
# ---------------------------------------------------------------------------

# "clean" runs the full pipeline; the other two run a single abbreviation pass.
PIPELINE_MODES = ("clean", "abbreviations", "expand")


class Pipeline:
    """The ordered cleaning pipeline built from a config dict."""

    def __init__(self, config: dict, custom_dir: str | Path | None = None, *,
                 mode: str = "clean"):
        if mode not in PIPELINE_MODES:
            raise ValueError(f"Unknown cleaning mode: {mode}")
        self.mode = mode
        missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config] if mode == "clean" else []
        if missing:
            raise ConfigError(f"Config missing required key(s): {', '.join(missing)}")
        self.config = config
        self.custom_dir = Path(custom_dir) if custom_dir else None
        self.stages: list[StageSpec] = self._build()

    # -- construction -------------------------------------------------------

    # stages switched on/off by their own config group: sid -> (group, default)
    _GROUP_ENABLED = {
        "nlp_redaction": ("nlp_redaction", True),
        "tokenize_phi": ("tokenization", False),
        "clinical_identifiers": ("clinical_identifiers", False),
        "duplicate_notes": ("duplicate_note_detection", True),
        "fuzzy_dedup": ("fuzzy_dedup", True),
    }

    def _builtin_enabled(self, sid: str) -> bool:
        cfg = self.config
        if sid in self._GROUP_ENABLED:
            group, default = self._GROUP_ENABLED[sid]
            return bool((cfg.get(group) or {}).get("enabled", default))
        so = get_stage_options(cfg, sid)
        if "enabled" in so:
            return bool(so["enabled"])
        return True

    def _build(self) -> list[StageSpec]:
        if self.mode == "abbreviations":
            return [StageSpec("medical_abbreviations", STAGE_LABELS["medical_abbreviations"],
                              "abbreviations", True)]
        if self.mode == "expand":
            return [StageSpec("expand_abbreviations", "Expand abbreviations", "expand", True)]
        customs = list_custom_rules(self.custom_dir)
        by_name = {c["name"]: c for c in customs}
        order = self.config.get("stage_order") or list(BUILTIN_STAGE_IDS)

        resolved: list[str] = []
        for sid in order:
            if sid in BUILTIN_STAGE_IDS:
                resolved.append(sid)
            elif sid.startswith("custom:") and sid[len("custom:"):] in by_name:
                resolved.append(sid)
        for sid in BUILTIN_STAGE_IDS:
            if sid not in resolved and sid != "medical_abbreviations":
                anchor = STAGE_ANCHORS.get(sid)
                if anchor in resolved:
                    resolved.insert(resolved.index(anchor) + 1, sid)
                else:
                    resolved.append(sid)
        for c in customs:
            sid = f"custom:{c['name']}"
            if sid not in resolved:
                resolved.append(sid)

        if "medical_abbreviations" not in resolved:
            # Legacy orders may put line_length before PHI or learned rules.
            # Only insert before it when no cleaning rules follow it.
            index = len(resolved) - 1 if resolved[-1] == "line_length" else len(resolved)
            resolved.insert(index, "medical_abbreviations")

        stages: list[StageSpec] = []
        for sid in resolved:
            if sid.startswith("custom:"):
                meta = by_name[sid[len("custom:"):]]
                enabled = bool(((self.config.get("custom_rules") or {}).get(meta["name"]) or {}).get("enabled", True))
                stages.append(StageSpec(id=sid, label=meta["label"], kind="custom",
                                        enabled=enabled, meta=meta))
            else:
                stages.append(StageSpec(id=sid, label=STAGE_LABELS[sid],
                                        kind=KIND_TO_RUNNER[sid],
                                        enabled=self._builtin_enabled(sid)))
        return stages

    # -- inspection ----------------------------------------------------------

    def stage_table(self) -> list[dict]:
        """Plain-dict stage list for UI rendering."""
        return [
            {
                "id": s.id,
                "label": s.label,
                "kind": s.kind,
                "enabled": s.enabled,
                "meta": s.meta if s.kind == "custom" else {},
            }
            for s in self.stages
        ]

    # -- execution -----------------------------------------------------------

    def run(self, text: str, wrap: bool | None = None, *,
            track_changes: bool = False, fact_check: bool | None = None,
            cache: Any = None) -> RunResult:
        """Execute all pipeline stages in sequence and return the RunResult.

        ``track_changes`` records each regex/abbreviation change in the
        stage's ``details["changes"]`` (in memory only; never in history).
        ``fact_check`` (default: config ``fact_check.enabled``, clean mode
        only) compares clinical facts before and after every stage and puts a
        :class:`~chartcleaner.fact_check.FactReport` in ``result.fact_check``.
        ``cache`` (a :class:`~chartcleaner.stage_cache.StageCache`) reuses a
        builtin stage's output when its input text and own settings are unchanged.
        """
        started = time.perf_counter()
        ctx = CleanContext(self.config, track_changes=track_changes)
        chars_before = len(text)
        words_before = len(text.split())
        lines_before = text.count("\n") + 1
        if fact_check is None:
            fact_check = self.mode == "clean" and bool(
                (self.config.get("fact_check") or fact_check_mod.DEFAULTS).get("enabled", True))
        if fact_check and len(text) > fact_check_mod.MAX_CHARS:
            fact_check = False
            size_note = (f"Clinical-facts check skipped: input over "
                         f"{fact_check_mod.MAX_CHARS:,} characters")
        else:
            size_note = None
        tracker = fact_check_mod.FactTracker(text) if fact_check else None

        text, stage_stats, warnings = self._execute_stages(text, ctx, tracker, cache)
        if size_note:
            warnings.append(size_note)
        text, wrapped, wrapper_stat = self._apply_wrapper(
            text, False if self.mode != "clean" else wrap)
        if wrapper_stat:
            stage_stats.append(wrapper_stat)
        report = None
        if tracker is not None:
            try:
                report = tracker.report(text)
            except Exception as e:  # the check must never break a clean
                warnings.append(f"Clinical-facts check failed: {type(e).__name__}: {e}")

        return RunResult(
            text=text,
            wrapped=wrapped,
            stages=stage_stats,
            warnings=warnings,
            duration_ms=(time.perf_counter() - started) * 1000,
            chars_before=chars_before,
            words_before=words_before,
            lines_before=lines_before,
            fact_check=report,
        )

    def stage_io(self, text: str, sid: str, *, cache: Any = None) -> tuple[str, str, StageStat]:
        """Run stages up to and including ``sid``: ``(text_before, text_after, stat)``.

        Powers the Pipeline page's "show what this stage does" preview. Stages
        after ``sid`` don't run; ``cache`` makes repeated previews cheap.
        """
        if sid not in {spec.id for spec in self.stages}:
            raise KeyError(f"Stage {sid!r} is not in this pipeline")
        ctx = CleanContext(self.config, track_changes=True)
        for spec, before, text, st, _warning in self._iter_stages(text, ctx, cache):
            if spec.id == sid:
                return before, text, st
        raise AssertionError("unreachable")  # pragma: no cover

    def _iter_stages(self, text: str, ctx: CleanContext, cache: Any = None):
        """Run the stages lazily: yield ``(spec, text_before, text_after, stat, warning)``."""
        for spec in self.stages:
            st = StageStat(id=spec.id, label=spec.label, kind=spec.kind, enabled=spec.enabled)
            st.chars_before = len(text)
            before = text
            text, warning = self._execute_single_stage(spec, text, ctx, st, cache)
            st.chars_after = len(text)
            yield spec, before, text, st, warning

    def _execute_stages(
        self, text: str, ctx: CleanContext, tracker: Any = None, cache: Any = None
    ) -> tuple[str, list[StageStat], list[str]]:
        stats: list[StageStat] = []
        warnings: list[str] = []
        for spec, before, text, st, warning in self._iter_stages(text, ctx, cache):
            if warning:
                warnings.append(warning)
            stats.append(st)
            if tracker is not None and text != before:
                tracker.after_stage(spec.id, spec.label, text)
        return text, stats, warnings

    def _execute_single_stage(
        self, spec: StageSpec, text: str, ctx: CleanContext, st: StageStat, cache: Any = None
    ) -> tuple[str, str | None]:
        """Run one stage, filling ``st`` in place; returns ``(text, warning)``."""
        warning = None
        try:
            if not spec.enabled:
                st.skipped = True
            elif spec.kind == "custom":
                text, n, details, skip_reason = apply_custom_rule(spec, text, ctx)
                if skip_reason:
                    st.skipped = True
                    st.details["note"] = skip_reason
                else:
                    st.matches, st.details = n, details
            else:
                use = cache is not None
                key = cache.key(spec.id, text, self.config, ctx.track_changes) if use else None
                hit = cache.get(key) if use else None
                if hit is not None:
                    text, n, details = hit
                else:
                    out, n, details = RUNNERS[spec.kind](text, self.config, ctx)
                    if use:
                        cache.put(key, out, n, details)
                    text = out
                st.matches, st.details = n, details
        except NlpUnavailable as e:
            st.skipped = True
            st.error = str(e)
            warning = f"NLP redaction skipped: {e}"
        except Exception as e:
            st.error = f"{type(e).__name__}: {e}"
            warning = f"Stage '{spec.label}' failed: {st.error}"

        return text, warning

    def _apply_wrapper(
        self, text: str, wrap: bool | None
    ) -> tuple[str, bool, StageStat | None]:
        should_wrap = bool(self.config.get("wrap_output", True)) if wrap is None else bool(wrap)
        if not should_wrap:
            return text, False, None

        tag = str(self.config.get("wrap_tag") or "patient_chart")
        stat = StageStat(id="wrapper", label=f"<{tag}> wrapper", kind="wrapper")
        stat.chars_before = len(text)
        wrapped_text = f"<{tag}>\n{text}\n</{tag}>"
        stat.chars_after = len(wrapped_text)
        return wrapped_text, True, stat


def clean_text(
    text: str,
    config: dict,
    custom_dir: str | Path | None = None,
    wrap: bool | None = None,
    *,
    mode: str = "clean",
    track_changes: bool = False,
    fact_check: bool | None = None,
) -> RunResult:
    """One-shot convenience: build a pipeline and run it."""
    return Pipeline(config, custom_dir=custom_dir, mode=mode).run(
        text, wrap=wrap, track_changes=track_changes, fact_check=fact_check)
