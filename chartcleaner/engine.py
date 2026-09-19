"""Chart Cleaner engine: a configurable, stage-based cleaning pipeline.

Both the CLI (``medical_cleaner.py``) and the desktop app (``app.py``) build on
this module. A pipeline is assembled from ``config.json`` plus any custom
script rules found in ``custom_rules/``; every stage records statistics (match
counts, character deltas) so each run can be tracked, charted, and compared.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "ConfigError",
    "NlpUnavailable",
    "CustomRuleError",
    "StageStat",
    "RunResult",
    "CleanContext",
    "Pipeline",
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

REQUIRED_CONFIG_KEYS = (
    "emr_line_metadata",
    "boilerplate",
    "epic_phi_patterns",
    "literal_replacements",
    "clinical_headers",
)

BUILTIN_STAGE_IDS = [
    "metadata_lines",
    "boilerplate",
    "tokenize_phi",
    "phi_patterns",
    "nlp_redaction",
    "literal_replacements",
    "unicode_normalize",
    "timestamps",
    "sections",
    "whitespace",
    "duplicate_notes",
    "fuzzy_dedup",
    "headers",
    "caps_normalize",
    "bullets",
    "line_length",
]

STAGE_LABELS = {
    "metadata_lines": "EMR line metadata",
    "boilerplate": "Boilerplate blocks",
    "phi_patterns": "Structured PHI patterns",
    "nlp_redaction": "NLP redaction (Presidio)",
    "tokenize_phi": "Reversible tokenization",
    "literal_replacements": "Literal replacements",
    "unicode_normalize": "Unicode normalization",
    "timestamps": "Timestamp removal",
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
    "nlp_redaction": "nlp",
    "tokenize_phi": "tokenize",
    "literal_replacements": "regex_pairs",
    "unicode_normalize": "unicode",
    "timestamps": "timestamps",
    "sections": "sections",
    "whitespace": "whitespace",
    "duplicate_notes": "dedup_notes",
    "fuzzy_dedup": "fuzzy",
    "headers": "headers",
    "caps_normalize": "caps",
    "bullets": "bullets",
    "line_length": "line_length",
}

DEFAULT_NOTE_SPLIT = r"(?=^(?:Progress Notes by .+|Attestation signed by .+)$)"

DEFAULT_NLP_ENTITIES = {
    "PERSON": "[REDACTED_NAME]",
    "PHONE_NUMBER": "[REDACTED_PHONE]",
    "EMAIL_ADDRESS": "[REDACTED_EMAIL]",
}

# Per-stage option groups. Every default below reproduces the stage's original
# hardcoded behavior, so existing configs are unaffected until a key is set.
DEFAULT_WHITESPACE = {
    "crlf_to_lf": False,        # convert CRLF / lone CR to LF
    "trim_trailing": True,      # remove trailing spaces/tabs per line
    "collapse_blank_lines": True,  # 3+ newlines -> one blank line
    "collapse_spaces": False,   # runs of 2+ spaces -> one space
    "strip_leading": False,     # remove leading indentation on every line
    "normalize_tabs": False,    # tabs -> four spaces
    "final_trim": True,         # strip blank edges of the whole text
}
DEFAULT_BULLETS = {
    "style": "- ",              # replacement marker ("" strips the marker)
    "normalize_numbered": False,  # "1." / "1)" prefixes -> style marker
    "skip_empty": False,        # drop bullet lines that have no text
    "extra_glyphs": [],         # extra leading chars treated as bullets
}
DEFAULT_HEADER_OPTIONS = {
    "level": 2,                 # markdown heading level (# = 1, ## = 2, ...)
    "bold": False,              # emit "**Header**" instead of markdown "#"
    "keep_colon": False,        # keep a trailing colon on promoted headers
    "uppercase_only": False,    # only promote lines written in ALL CAPS
}
DEFAULT_UNICODE = {
    "quotes": False,            # curly quotes -> straight
    "dashes": False,            # en/em/minus dashes -> hyphen
    "nbsp": False,              # non-breaking spaces -> normal spaces
    "zero_width": False,        # strip zero-width joiners/BOM characters
    "ellipsis": False,          # … -> ...
    "ligatures": False,         # ﬁ/ﬂ/... -> fi/fl/...
}
DEFAULT_TIMESTAMPS = {
    "enabled": False,
    "replacement": "",
    "builtin_patterns": True,   # include the shipped date/time patterns
    "extra_patterns": [],       # user regexes, run after the built-ins
    "remove_clock_times": False,  # also strip 12:30 / 12:30:45 pm style times
}
DEFAULT_SECTIONS = {
    "mode": "off",              # off | drop (remove listed) | keep (only listed)
    "sections": [],             # header names to drop or keep
    "boundary_headers": [],     # what counts as a section header (empty = use
                                # the Header stage's clinical_headers list)
    "keep_preamble": True,      # text before the first section header
}
DEFAULT_CAPS = {
    "mode": "off",              # off | sentence
    "min_chars": 40,            # only lines at least this long are rewritten
    "preserve_words": [],       # acronyms kept uppercase (MRI, ICU, ...)
}
DEFAULT_LINE_LENGTH = {
    "mode": "off",              # off | truncate | wrap
    "max_chars": 200,
    "marker": "…",              # appended when truncating
}


class ConfigError(Exception):
    """Raised when config.json is missing or invalid."""


class NlpUnavailable(Exception):
    """Raised when the Presidio/spaCy stack is not usable on this machine."""


class CustomRuleError(Exception):
    """Raised when a custom script rule cannot run."""


# ---------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------

def load_config(path: str | Path) -> dict:
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


def _compile(pattern: str, flags: int) -> re.Pattern:
    return re.compile(pattern, flags=flags)


def validate_config(cfg: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) without raising."""
    errors: list[str] = []
    warnings: list[str] = []

    def check_regex(pattern: Any, context: str, flags: int) -> None:
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"{context}: pattern must be a non-empty string")
            return
        try:
            re.compile(pattern, flags=flags)
        except re.error as e:
            errors.append(f"{context}: invalid regex ({e})")

    for key in REQUIRED_CONFIG_KEYS[:2]:
        val = cfg.get(key)
        if not isinstance(val, list):
            errors.append(f"{key}: must be a list of regex strings")
            continue
        if not val:
            warnings.append(f"{key}: empty list — stage will do nothing")
        flags = re.IGNORECASE | re.MULTILINE
        if key == "boilerplate":
            flags |= re.DOTALL
        for i, p in enumerate(val):
            check_regex(p, f"{key}[{i}]", flags)

    for key in ("epic_phi_patterns", "literal_replacements"):
        val = cfg.get(key)
        if not isinstance(val, list):
            errors.append(f"{key}: must be a list of [pattern, replacement] pairs")
            continue
        for i, pair in enumerate(val):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                errors.append(f"{key}[{i}]: must be a two-element [pattern, replacement] list")
                continue
            check_regex(pair[0], f"{key}[{i}] pattern", re.IGNORECASE)
            if not isinstance(pair[1], str):
                errors.append(f"{key}[{i}] replacement: must be a string")

    headers = cfg.get("clinical_headers")
    if not isinstance(headers, list) or not all(isinstance(h, str) and h for h in headers):
        errors.append("clinical_headers: must be a list of non-empty header names")
    else:
        for h in headers:
            try:
                re.compile(rf"^\s*({h})\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
            except re.error as e:
                errors.append(f"clinical_headers[{h!r}]: invalid regex ({e})")

    def check_option_group(key: str, defaults: dict, types: dict[str, tuple]) -> dict | None:
        val = cfg.get(key)
        if val is None:
            return None
        if not isinstance(val, dict):
            errors.append(f"{key}: must be an object")
            return None
        for fname, (ok_types, extra) in types.items():
            if fname not in val:
                continue
            v = val[fname]
            if not isinstance(v, ok_types):
                errors.append(f"{key}.{fname}: wrong type (expected {extra})")
        for k in val:
            if k not in defaults:
                warnings.append(f"{key}.{k}: unknown option (kept as-is, ignored)")
        return val

    def check_name_list(val: Any, context: str) -> None:
        if not isinstance(val, list) or not all(isinstance(x, str) and x for x in val):
            errors.append(f"{context}: must be a list of non-empty names")
            return
        for i, name in enumerate(val):
            try:
                re.compile(rf"^\s*({name})\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
            except re.error as e:
                errors.append(f"{context}[{i}] {name!r}: invalid regex ({e})")

    check_option_group("whitespace", DEFAULT_WHITESPACE,
                       {k: ((bool,), "true/false") for k in DEFAULT_WHITESPACE})

    check_option_group("bullets", DEFAULT_BULLETS, {
        "style": ((str,), "a short string"),
        "normalize_numbered": ((bool,), "true/false"),
        "skip_empty": ((bool,), "true/false"),
    })

    ho = check_option_group("header_options", DEFAULT_HEADER_OPTIONS, {
        "level": ((int,), "integer 1–6"),
        "bold": ((bool,), "true/false"),
        "keep_colon": ((bool,), "true/false"),
        "uppercase_only": ((bool,), "true/false"),
    })
    if ho is not None and isinstance(ho.get("level"), int) and not 1 <= ho["level"] <= 6:
        warnings.append("header_options.level: expected 1–6")

    check_option_group("unicode_normalize", DEFAULT_UNICODE,
                       {k: ((bool,), "true/false") for k in DEFAULT_UNICODE})

    ts = check_option_group("timestamp_removal", DEFAULT_TIMESTAMPS, {
        "enabled": ((bool,), "true/false"),
        "replacement": ((str,), "a string"),
        "builtin_patterns": ((bool,), "true/false"),
        "extra_patterns": ((list,), "a list of regex strings"),
        "remove_clock_times": ((bool,), "true/false"),
    })
    if ts is not None and isinstance(ts.get("extra_patterns"), list):
        for i, p in enumerate(ts["extra_patterns"]):
            check_regex(p, f"timestamp_removal.extra_patterns[{i}]", re.IGNORECASE)

    sf = check_option_group("section_filter", DEFAULT_SECTIONS, {
        "mode": ((str,), "'off', 'drop' or 'keep'"),
        "sections": ((list,), "a list of header names"),
        "boundary_headers": ((list,), "a list of header names"),
        "keep_preamble": ((bool,), "true/false"),
    })
    if sf is not None:
        mode = sf.get("mode", "off")
        if isinstance(mode, str) and mode not in ("off", "drop", "keep"):
            errors.append("section_filter.mode: must be 'off', 'drop' or 'keep'")
        check_name_list(sf.get("sections"), "section_filter.sections")
        if sf.get("boundary_headers"):
            check_name_list(sf.get("boundary_headers"), "section_filter.boundary_headers")

    cp = check_option_group("caps_normalize", DEFAULT_CAPS, {
        "mode": ((str,), "'off' or 'sentence'"),
        "min_chars": ((int,), "a positive integer"),
        "preserve_words": ((list,), "a list of words"),
    })
    if cp is not None:
        if isinstance(cp.get("mode"), str) and cp["mode"] not in ("off", "sentence"):
            errors.append("caps_normalize.mode: must be 'off' or 'sentence'")
        if isinstance(cp.get("min_chars"), int) and cp["min_chars"] < 1:
            warnings.append("caps_normalize.min_chars: expected a positive integer")

    ll = check_option_group("line_length", DEFAULT_LINE_LENGTH, {
        "mode": ((str,), "'off', 'truncate' or 'wrap'"),
        "max_chars": ((int,), "an integer ≥ 10"),
        "marker": ((str,), "a short string"),
    })
    if ll is not None:
        if isinstance(ll.get("mode"), str) and ll["mode"] not in ("off", "truncate", "wrap"):
            errors.append("line_length.mode: must be 'off', 'truncate' or 'wrap'")
        if isinstance(ll.get("max_chars"), int) and ll["max_chars"] < 10:
            warnings.append("line_length.max_chars: expected an integer ≥ 10")

    so = cfg.get("stage_options")
    if so is not None:
        if not isinstance(so, dict):
            errors.append("stage_options: must be an object mapping stage id -> options")
        else:
            for sid, opts in so.items():
                if not isinstance(opts, dict):
                    errors.append(f"stage_options.{sid}: must be an object")
                    continue
                if "case_sensitive" in opts and not isinstance(opts["case_sensitive"], bool):
                    errors.append(f"stage_options.{sid}.case_sensitive: must be true/false")
                if "enabled" in opts and not isinstance(opts["enabled"], bool):
                    errors.append(f"stage_options.{sid}.enabled: must be true/false")

    d = cfg.get("duplicate_note_detection") or {}
    if not isinstance(d, dict):
        errors.append("duplicate_note_detection: must be an object")
    else:
        if not isinstance(d.get("enabled", True), bool):
            errors.append("duplicate_note_detection.enabled: must be true/false")
        if "split_pattern" in d:
            check_regex(d["split_pattern"], "duplicate_note_detection.split_pattern", re.MULTILINE | re.IGNORECASE)
        for intkey, lo, hi in (("min_body_chars", 0, 1_000_000), ("similarity_threshold", 50, 100)):
            v = d.get(intkey)
            if v is not None and (not isinstance(v, int) or not lo <= v <= hi):
                warnings.append(f"duplicate_note_detection.{intkey}: expected integer {lo}–{hi}")

    f = cfg.get("fuzzy_dedup") or {}
    if not isinstance(f, dict):
        errors.append("fuzzy_dedup: must be an object")
    else:
        if not isinstance(f.get("enabled", True), bool):
            errors.append("fuzzy_dedup.enabled: must be true/false")
        t = f.get("threshold", 95)
        if not isinstance(t, int) or not 50 <= t <= 100:
            warnings.append("fuzzy_dedup.threshold: expected integer 50–100")
        m = f.get("min_chars", 100)
        if not isinstance(m, int) or m < 0:
            warnings.append("fuzzy_dedup.min_chars: expected non-negative integer")

    n = cfg.get("nlp_redaction") or {}
    if not isinstance(n, dict):
        errors.append("nlp_redaction: must be an object")
    else:
        if not isinstance(n.get("enabled", True), bool):
            errors.append("nlp_redaction.enabled: must be true/false")
        ents = n.get("entities")
        if ents is not None:
            if not isinstance(ents, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in ents.items()):
                errors.append("nlp_redaction.entities: must map entity type -> replacement text")
            else:
                known = {"PERSON", "LOCATION", "ORGANIZATION", "NRP", "DATE_TIME", "PHONE_NUMBER",
                         "EMAIL_ADDRESS", "URL", "IP_ADDRESS", "MEDICAL_LICENSE", "US_SSN",
                         "US_DRIVER_LICENSE", "US_BANK_NUMBER", "CREDIT_CARD", "IBAN_CODE"}
                for k in ents:
                    if k not in known:
                        warnings.append(f"nlp_redaction.entities: '{k}' is not a built-in Presidio entity type")
        st = n.get("score_threshold")
        if st is not None and (not isinstance(st, (int, float)) or not 0 <= st <= 1):
            warnings.append("nlp_redaction.score_threshold: expected number between 0 and 1")

    al = cfg.get("nlp_allow_list")
    if al is not None and (not isinstance(al, list) or not all(isinstance(x, str) for x in al)):
        errors.append("nlp_allow_list: must be a list of strings")

    t = cfg.get("tokenization")
    if t is not None:
        if not isinstance(t, dict):
            errors.append("tokenization: must be an object")
        else:
            if not isinstance(t.get("enabled", False), bool):
                errors.append("tokenization.enabled: must be true/false")
            pfx = t.get("prefix")
            if pfx is not None and (not isinstance(pfx, str) or not re.fullmatch(r"[A-Za-z0-9_]{0,8}", pfx)):
                errors.append("tokenization.prefix: must be 0–8 letters/digits/_")

    he = cfg.get("headers_engine")
    if he is not None and he not in ("regex", "medspacy"):
        errors.append("headers_engine: must be 'regex' or 'medspacy'")

    ing = cfg.get("ingest")
    if ing is not None:
        if not isinstance(ing, dict):
            errors.append("ingest: must be an object")
        elif "engine" in ing and ing["engine"] not in ("auto", "builtin", "markitdown", "docling"):
            errors.append("ingest.engine: must be one of auto, builtin, markitdown, docling")

    a = cfg.get("audit")
    if a is not None:
        if not isinstance(a, dict):
            errors.append("audit: must be an object")
        else:
            if not isinstance(a.get("enabled", True), bool):
                errors.append("audit.enabled: must be true/false")
            mf = a.get("max_findings")
            if mf is not None and (not isinstance(mf, int) or mf < 1):
                warnings.append("audit.max_findings: expected positive integer")
            checks = a.get("checks")
            if checks is not None:
                if not isinstance(checks, dict):
                    errors.append("audit.checks: must be an object")
                else:
                    from .audit import AUDIT_CHECK_IDS
                    for cid, c in checks.items():
                        if cid not in AUDIT_CHECK_IDS:
                            warnings.append(f"audit.checks: unknown check '{cid}' (ignored)")
                            continue
                        if not isinstance(c, dict):
                            errors.append(f"audit.checks.{cid}: must be an object")
                            continue
                        if not isinstance(c.get("enabled", True), bool):
                            errors.append(f"audit.checks.{cid}.enabled: must be true/false")
                        if cid == "long_digits":
                            md = c.get("min_digits")
                            if md is not None and (not isinstance(md, int) or not 4 <= md <= 20):
                                warnings.append("audit.checks.long_digits.min_digits: expected integer 4–20")
                        if cid == "label_names":
                            labs = c.get("labels")
                            if labs is not None and (not isinstance(labs, list)
                                                     or not all(isinstance(x, str) and x for x in labs)):
                                errors.append("audit.checks.label_names.labels: must be a list of strings")
                        if cid == "residual_chrome":
                            pats = c.get("patterns")
                            if pats is not None:
                                if not isinstance(pats, list):
                                    errors.append("audit.checks.residual_chrome.patterns: must be a list of regex strings")
                                else:
                                    for i, p in enumerate(pats):
                                        check_regex(p, f"audit.checks.residual_chrome.patterns[{i}]",
                                                    re.IGNORECASE | re.MULTILINE)

    if "wrap_output" in cfg and not isinstance(cfg["wrap_output"], bool):
        errors.append("wrap_output: must be true/false")
    tag = cfg.get("wrap_tag")
    if tag is not None and (not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", tag)):
        errors.append("wrap_tag: must be a simple tag name (letters, digits, _ - .)")

    order = cfg.get("stage_order")
    if order is not None:
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            errors.append("stage_order: must be a list of stage ids")
        else:
            for sid in order:
                if sid not in BUILTIN_STAGE_IDS and not sid.startswith("custom:"):
                    warnings.append(f"stage_order: unknown stage id '{sid}' (ignored)")

    cr = cfg.get("custom_rules")
    if cr is not None and not isinstance(cr, dict):
        errors.append("custom_rules: must map script name -> {enabled: bool}")

    known_keys = set(REQUIRED_CONFIG_KEYS) | {
        "duplicate_note_detection", "fuzzy_dedup", "nlp_redaction", "nlp_allow_list",
        "wrap_output", "wrap_tag", "stage_order", "custom_rules", "audit",
        "tokenization", "headers_engine", "ingest",
        "whitespace", "bullets", "header_options", "unicode_normalize",
        "timestamp_removal", "section_filter", "caps_normalize", "line_length",
        "stage_options",
    }
    for k in cfg:
        if k not in known_keys:
            warnings.append(f"unknown config key '{k}' (kept as-is, ignored by engine)")

    return errors, warnings


# ---------------------------------------------------------------------------
# run statistics
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
            "id": self.id, "label": self.label, "kind": self.kind,
            "enabled": self.enabled, "skipped": self.skipped, "error": self.error,
            "matches": self.matches, "chars_before": self.chars_before,
            "chars_after": self.chars_after, "details": self.details,
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
        return (f"{self.chars_before:,} → {self.chars_after:,} chars "
                f"({self.reduction:+.1f}%), {phi} PHI item(s) redacted")

    def to_history_dict(self, source: str) -> dict:
        def slim_details(details: dict) -> dict:
            # the token map can be large and sensitive — counts only in history
            if "token_map" in details:
                details = {k: v for k, v in details.items() if k != "token_map"}
            return details

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
            "stages": [{**s.to_dict(), "details": slim_details(s.details)}
                       for s in self.stages],
            "warnings": self.warnings,
        }


class CleanContext:
    """Handed to custom script rules: lets them report counts and messages."""

    def __init__(self, config: dict):
        self._config = config
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


# ---------------------------------------------------------------------------
# builtin stage runners: fn(text, config, ctx) -> (text, matches, details)
# ---------------------------------------------------------------------------

def _stage_opts(cfg: dict, sid: str) -> dict:
    """Per-stage options from config['stage_options'][sid] (missing -> {})."""
    so = cfg.get("stage_options")
    if not isinstance(so, dict):
        return {}
    opts = so.get(sid)
    return opts if isinstance(opts, dict) else {}


def _run_regex_list(text: str, cfg: dict, ctx: CleanContext, key: str, flags: int,
                    sid: str | None = None):
    if sid and _stage_opts(cfg, sid).get("case_sensitive"):
        flags &= ~re.IGNORECASE
    total = 0
    for pattern in cfg[key]:
        r = _compile(pattern, flags)
        text, n = r.subn("", text)
        total += n
    return text, total, {}


def _run_regex_pairs(text: str, cfg: dict, ctx: CleanContext, key: str, phi: bool = False,
                     sid: str | None = None):
    flags = re.IGNORECASE
    if sid and _stage_opts(cfg, sid).get("case_sensitive"):
        flags = 0
    total = 0
    for pattern, replacement in cfg[key]:
        r = _compile(pattern, flags)
        text, n = r.subn(replacement, text)
        total += n
    return text, total, ({"phi": {"pattern_redactions": total}} if phi else {})


def _run_whitespace(text: str, cfg: dict, ctx: CleanContext):
    o = {**DEFAULT_WHITESPACE, **(cfg.get("whitespace") or {})}
    n = 0
    if o.get("crlf_to_lf"):
        text, k = re.subn(r"\r\n?", "\n", text)
        n += k
    if o.get("strip_leading"):
        text, k = re.subn(r"^[ \t]+", "", text, flags=re.MULTILINE)
        n += k
    if o.get("normalize_tabs"):
        text, k = re.subn(r"\t", "    ", text)
        n += k
    if o.get("trim_trailing"):
        text, k = re.subn(r"[ \t]+$", "", text, flags=re.MULTILINE)
        n += k
    if o.get("collapse_spaces"):
        text, k = re.subn(r"[ ]{2,}", " ", text)
        n += k
    if o.get("collapse_blank_lines"):
        text, k = re.subn(r"\n{3,}", "\n\n", text)
        n += k
    if o.get("final_trim"):
        stripped = text.strip()
        if stripped != text:
            n += 1
        text = stripped
    return text, n, {}


def _run_bullets(text: str, cfg: dict, ctx: CleanContext):
    o = {**DEFAULT_BULLETS, **(cfg.get("bullets") or {})}
    style = str(o.get("style") if o.get("style") is not None else "- ")
    glyphs = "[•\\-*" + re.escape("".join(g for g in (o.get("extra_glyphs") or []) if isinstance(g, str))) + "]"
    total = 0
    if o.get("skip_empty"):
        text, k = re.subn(rf"^\s*{glyphs}[ \t]*$\n?", "", text, flags=re.MULTILINE)
        total += k
    if o.get("normalize_numbered"):
        text, k = re.subn(rf"^\s*\d+[.)]\s+", style, text, flags=re.MULTILINE)
        total += k
    text, k = re.subn(rf"^\s*{glyphs}\s+", style, text, flags=re.MULTILINE)
    total += k
    return text, total, {}


def _run_headers(text: str, cfg: dict, ctx: CleanContext):
    o = {**DEFAULT_HEADER_OPTIONS, **(cfg.get("header_options") or {})}
    try:
        level = max(1, min(6, int(o.get("level") or 2)))
    except (TypeError, ValueError):
        level = 2
    bold = bool(o.get("bold"))
    keep_colon = bool(o.get("keep_colon"))
    upper_only = bool(o.get("uppercase_only"))

    def styled(title: str, colon: str) -> str:
        if bold:
            return f"**{title}{colon}**"
        return f"{'#' * level} {title}{colon}"

    def make_replacement(match: re.Match) -> str:
        if upper_only:
            letters = [c for c in match.group(1) if c.isalpha()]
            if letters and not all(c.isupper() for c in letters):
                return match.group(0)
        colon = (match.group(2) or "") if keep_colon else ""
        return styled(match.group(1), colon)

    total = 0
    engine = (cfg.get("headers_engine") or "regex").lower()
    if engine == "medspacy":
        try:
            from .nlp_medspacy import medspacy_sections
            sections = medspacy_sections(text)
            if sections:
                # Replace longest-first so nested title fragments are safe.
                out, n = text, 0
                for title, start, end in sorted(sections, key=lambda s: -s[1]):
                    out = out[:start] + styled(title, "") + out[end:]
                    n += 1
                return out, n, {"engine": "medspacy"}
        except Exception as e:
            ctx.log(f"medspaCy section engine unavailable ({e}); using regex headers.")
    for h in cfg["clinical_headers"]:
        pat = rf"^\s*({h})(\s*:)?\s*$" if keep_colon else rf"^\s*({h})\s*:?\s*$"
        r = _compile(pat, re.IGNORECASE | re.MULTILINE)
        text, n = r.subn(make_replacement, text)
        total += n
    return text, total, {}


def _run_duplicate_notes(text: str, cfg: dict, ctx: CleanContext):
    dcfg = cfg.get("duplicate_note_detection") or {}
    if dcfg.get("enabled") is False:
        return text, 0, {}
    split_pattern = dcfg.get("split_pattern", DEFAULT_NOTE_SPLIT)
    min_body = int(dcfg.get("min_body_chars", 400))
    threshold = int(dcfg.get("similarity_threshold", 90))
    from thefuzz import fuzz

    parts = _compile(split_pattern, re.MULTILINE | re.IGNORECASE).split(text)
    if len(parts) <= 1:
        return text, 0, {}

    kept = [parts[0]]
    seen_bodies: list[str] = []
    dropped = 0

    def body_after_first_line(segment: str) -> str:
        s = segment.strip()
        if not s or "\n" not in s:
            return ""
        return s.split("\n", 1)[1].strip()

    for seg in parts[1:]:
        body = body_after_first_line(seg)
        if len(body) < min_body:
            kept.append(seg)
            continue
        b_low = body.lower()
        if any(_fuzz_ratio(b_low, p.lower()) >= threshold for p in seen_bodies):
            dropped += 1
            continue
        seen_bodies.append(body)
        kept.append(seg)

    return "".join(kept), dropped, {"notes_kept": len(kept)}


def _fuzz_ratio(a: str, b: str) -> float:
    from thefuzz import fuzz
    return fuzz.ratio(a, b)


def _run_fuzzy_dedup(text: str, cfg: dict, ctx: CleanContext):
    fcfg = cfg.get("fuzzy_dedup") or {}
    if fcfg.get("enabled") is False:
        return text, 0, {}
    threshold = int(fcfg.get("threshold", 95))
    min_chars = int(fcfg.get("min_chars", 100))

    paragraphs = text.split("\n\n")
    deduped: list[str] = []
    dropped = 0
    for p in paragraphs:
        p_clean = p.strip()
        if len(p_clean) < min_chars:
            deduped.append(p)
            continue
        is_duplicate = False
        for saved in deduped:
            if len(saved.strip()) > min_chars:
                if _fuzz_ratio(p_clean.lower(), saved.strip().lower()) >= threshold:
                    is_duplicate = True
                    break
        if is_duplicate:
            dropped += 1
        else:
            deduped.append(p)
    return "\n\n".join(deduped), dropped, {"paragraphs_kept": len(deduped)}


# --- new optional stages -----------------------------------------------------

_UNICODE_MAPS = {
    "quotes": [("\u201c", '"'), ("\u201d", '"'), ("\u2018", "'"), ("\u2019", "'"),
               ("\u201a", ","), ("\u201e", '"')],
    "dashes": [("\u2013", "-"), ("\u2014", "-"), ("\u2212", "-")],
    "nbsp": [("\u00a0", " ")],
    "zero_width": [("\u200b", ""), ("\u200c", ""), ("\u200d", ""), ("\ufeff", "")],
    "ellipsis": [("\u2026", "...")],
    "ligatures": [("\ufb00", "ff"), ("\ufb01", "fi"), ("\ufb02", "fl"),
                  ("\ufb03", "ffi"), ("\ufb04", "ffl")],
}


def _run_unicode(text: str, cfg: dict, ctx: CleanContext):
    o = {**DEFAULT_UNICODE, **(cfg.get("unicode_normalize") or {})}
    total = 0
    for opt, pairs in _UNICODE_MAPS.items():
        if not o.get(opt):
            continue
        for old, new in pairs:
            text, k = re.subn(re.escape(old), new, text)
            total += k
    return text, total, {}


BUILTIN_TIMESTAMP_PATTERNS = [
    r"\b\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?(?:\s?(?:Z|[+-]\d{2}:?\d{2}))?\b",
    r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"(?:,?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)?\b",
]
CLOCK_TIME_PATTERN = r"(?<![\d:])\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?\b(?![\d:])"


def _run_timestamps(text: str, cfg: dict, ctx: CleanContext):
    o = {**DEFAULT_TIMESTAMPS, **(cfg.get("timestamp_removal") or {})}
    if not o.get("enabled"):
        return text, 0, {}
    repl = str(o.get("replacement") or "")
    pats = list(o.get("extra_patterns") or [])
    if o.get("builtin_patterns", True):
        pats = BUILTIN_TIMESTAMP_PATTERNS + pats
    total = 0
    for p in pats:
        if not isinstance(p, str) or not p:
            continue
        try:
            r = re.compile(p, re.IGNORECASE)
        except re.error:
            ctx.log(f"bad timestamp pattern skipped: {p!r}")
            continue
        text, k = r.subn(repl, text)
        total += k
    if o.get("remove_clock_times"):
        text, k = re.subn(CLOCK_TIME_PATTERN, repl, text)
        total += k
    return text, total, {}


def _run_sections(text: str, cfg: dict, ctx: CleanContext):
    o = {**DEFAULT_SECTIONS, **(cfg.get("section_filter") or {})}
    mode = str(o.get("mode") or "off")
    if mode not in ("drop", "keep"):
        return text, 0, {}
    names = [n for n in (o.get("sections") or []) if isinstance(n, str) and n]
    if not names:
        return text, 0, {}
    boundary = [b for b in (o.get("boundary_headers") or []) if isinstance(b, str) and b]
    if not boundary:
        boundary = [h for h in (cfg.get("clinical_headers") or []) if isinstance(h, str) and h]
    if not boundary:
        boundary = names
    flags = re.IGNORECASE | re.MULTILINE
    try:
        bounds = [(m.start(), m.group(1))
                  for m in re.finditer(rf"^[ \t]*({'|'.join(boundary)})[ \t]*:?[ \t]*$", text, flags)]
        member_re = re.compile(rf"^[ \t]*(?:{'|'.join(names)})[ \t]*:?[ \t]*$", flags)
    except re.error as e:
        ctx.log(f"section_filter: bad header name ({e}); stage skipped.")
        return text, 0, {}
    if not bounds:
        return text, 0, {}

    keep_preamble = bool(o.get("keep_preamble", True))
    first_start = bounds[0][0]
    out = text[:first_start] if keep_preamble else ""
    pos = first_start
    removed: list[str] = []
    for i, (start, header) in enumerate(bounds):
        seg_end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        is_member = member_re.match(text[start:seg_end].split("\n", 1)[0] + "\n") is not None
        remove = is_member if mode == "drop" else not is_member
        if remove:
            removed.append(header.strip())
            pos = seg_end
        else:
            out += text[pos:seg_end]
            pos = seg_end
    out += text[pos:]
    return out, len(removed), {"sections_removed": removed}


def _run_caps(text: str, cfg: dict, ctx: CleanContext):
    o = {**DEFAULT_CAPS, **(cfg.get("caps_normalize") or {})}
    if str(o.get("mode") or "off") != "sentence":
        return text, 0, {}
    try:
        min_chars = max(1, int(o.get("min_chars") or 40))
    except (TypeError, ValueError):
        min_chars = 40
    keep = [w for w in (o.get("preserve_words") or []) if isinstance(w, str) and w]
    keep_re = (re.compile(r"\b(" + "|".join(re.escape(w) for w in keep) + r")\b", re.IGNORECASE)
               if keep else None)
    changed = 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if len(s) < min_chars or not s.isupper() or not any(c.isalpha() for c in s):
            continue
        low = s.lower()
        low = low[0].upper() + low[1:]
        if keep_re:
            low = keep_re.sub(lambda m: m.group(1).upper(), low)
        indent = line[:len(line) - len(line.lstrip())]
        lines[i] = indent + low
        changed += 1
    return "\n".join(lines), changed, {}


def _run_line_length(text: str, cfg: dict, ctx: CleanContext):
    import textwrap

    o = {**DEFAULT_LINE_LENGTH, **(cfg.get("line_length") or {})}
    mode = str(o.get("mode") or "off")
    try:
        max_chars = int(o.get("max_chars") or 0)
    except (TypeError, ValueError):
        max_chars = 0
    if mode == "off" or max_chars < 10:
        return text, 0, {}
    marker = str(o.get("marker") or "…")
    count = 0
    out: list[str] = []
    for line in text.split("\n"):
        if len(line) <= max_chars:
            out.append(line)
            continue
        if mode == "truncate":
            out.append(line[:max_chars].rstrip() + marker)
        else:  # wrap, re-adding the line's own indentation to every piece
            indent = line[:len(line) - len(line.lstrip())]
            body = line[len(indent):]
            wrapped = textwrap.wrap(body, width=max(1, max_chars - len(indent)),
                                    break_long_words=False, break_on_hyphens=False,
                                    subsequent_indent=indent) or [""]
            out.append(indent + wrapped[0])
            out.extend(wrapped[1:])
        count += 1
    return "\n".join(out), count, {}


# --- Presidio NLP stage -----------------------------------------------------

_PRESIDIO_CACHE: dict[str, Any] = {}


def _nlp_stack(entities_key: tuple, allow: frozenset, threshold):
    """Build (or reuse) the Presidio engines for a given entity signature."""
    sig = (entities_key, allow, threshold)
    if _PRESIDIO_CACHE.get("sig") == sig:
        return _PRESIDIO_CACHE["analyzer"], _PRESIDIO_CACHE["anonymizer"]
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import SpacyNlpEngine
        from presidio_anonymizer import AnonymizerEngine
    except Exception as e:  # ImportError or incompatible install
        raise NlpUnavailable(
            f"Presidio could not be imported ({e}). Run install.sh / install.bat to add it."
        ) from e
    if find_spec("en_core_web_sm") is None:
        raise NlpUnavailable("spaCy model 'en_core_web_sm' is not installed in this environment.")
    try:
        nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
    except Exception as e:
        raise NlpUnavailable(f"Could not initialize the NLP engine ({e}).") from e
    _PRESIDIO_CACHE.clear()
    _PRESIDIO_CACHE.update(sig=sig, analyzer=analyzer, anonymizer=AnonymizerEngine())
    return _PRESIDIO_CACHE["analyzer"], _PRESIDIO_CACHE["anonymizer"]


def _run_nlp(text: str, cfg: dict, ctx: CleanContext):
    from presidio_anonymizer.entities import OperatorConfig

    ncfg = cfg.get("nlp_redaction") or {}
    entities: dict[str, str] = dict(ncfg.get("entities") or DEFAULT_NLP_ENTITIES)
    allow = frozenset(t.lower() for t in (cfg.get("nlp_allow_list") or []))
    threshold = ncfg.get("score_threshold")

    analyzer, anonymizer = _nlp_stack(tuple(sorted(entities.items())), allow, threshold)
    results = analyzer.analyze(text=text, entities=list(entities), language="en")
    if threshold is not None:
        results = [r for r in results if r.score >= threshold]
    filtered = [r for r in results if text[r.start:r.end].lower() not in allow]
    operators = {
        etype: OperatorConfig("replace", {"new_value": repl})
        for etype, repl in entities.items()
    }
    new_text = anonymizer.anonymize(text=text, analyzer_results=filtered, operators=operators).text
    counts: dict[str, int] = {}
    for r in filtered:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return new_text, len(filtered), {"phi": counts}


def _run_tokenize(text: str, cfg: dict, ctx: CleanContext):
    """Reversible tokenization: PHI values → [[Tn]] codes (map in details)."""
    from .tokens import DEFAULT_TOKEN_CONFIG, tokenize

    tcfg = {**DEFAULT_TOKEN_CONFIG, **(cfg.get("tokenization") or {})}
    labels: list[str] = []
    try:
        from .audit import DEFAULT_NAME_LABELS, get_audit_config
        labels = list(get_audit_config(cfg)["checks"]["label_names"].get("labels")
                      or DEFAULT_NAME_LABELS)
    except Exception:
        pass
    new_text, mapping = tokenize(
        text, cfg.get("epic_phi_patterns") or [], labels=labels,
        prefix=str(tcfg.get("prefix") or "T"))
    details: dict = {"tokens_issued": len(mapping)}
    if mapping:
        details["token_map"] = mapping
        details["phi"] = {"tokenized": len(mapping)}
    return new_text, len(mapping), details


RUNNERS: dict[str, Callable] = {
    "regex_lines": lambda t, c, x: _run_regex_list(t, c, x, "emr_line_metadata", re.IGNORECASE | re.MULTILINE, sid="metadata_lines"),
    "regex_lines_dotall": lambda t, c, x: _run_regex_list(t, c, x, "boilerplate", re.IGNORECASE | re.DOTALL | re.MULTILINE, sid="boilerplate"),
    "regex_pairs_phi": lambda t, c, x: _run_regex_pairs(t, c, x, "epic_phi_patterns", phi=True, sid="phi_patterns"),
    "nlp": _run_nlp,
    "tokenize": _run_tokenize,
    "regex_pairs": lambda t, c, x: _run_regex_pairs(t, c, x, "literal_replacements", sid="literal_replacements"),
    "unicode": _run_unicode,
    "timestamps": _run_timestamps,
    "sections": _run_sections,
    "whitespace": _run_whitespace,
    "dedup_notes": _run_duplicate_notes,
    "fuzzy": _run_fuzzy_dedup,
    "headers": _run_headers,
    "caps": _run_caps,
    "bullets": _run_bullets,
    "line_length": _run_line_length,
}

KIND_TO_RUNNER = {
    "metadata_lines": "regex_lines",
    "boilerplate": "regex_lines_dotall",
    "phi_patterns": "regex_pairs_phi",
    "nlp_redaction": "nlp",
    "tokenize_phi": "tokenize",
    "literal_replacements": "regex_pairs",
    "unicode_normalize": "unicode",
    "timestamps": "timestamps",
    "sections": "sections",
    "whitespace": "whitespace",
    "duplicate_notes": "dedup_notes",
    "fuzzy_dedup": "fuzzy",
    "headers": "headers",
    "caps_normalize": "caps",
    "bullets": "bullets",
    "line_length": "line_length",
}


# ---------------------------------------------------------------------------
# custom script rules
# ---------------------------------------------------------------------------

CUSTOM_RULE_TEMPLATE = '''"""Custom cleaning rule — edit freely and save.

Contract: implement  clean(text, ctx) -> str  and it becomes a pipeline stage.

Helpers available on `ctx`:
  ctx.count("name", n)  — count something; totals show up in the Stats tracker
  ctx.log("message")    — a note shown in the run details
  ctx.config            — read-only view of config.json

MODULE (top-level) settings:
  LABEL        display name in the app (default: file name)
  DESCRIPTION  one-line explanation
  PLACEHOLDER  keep True while the rule is a stub; it is skipped until False
"""

LABEL = "New custom rule"
DESCRIPTION = "Describe what this rule does."
PLACEHOLDER = True

import re


def clean(text: str, ctx) -> str:
    # Example: collapse runs of 4+ newlines down to two.
    new_text, n = re.subn(r"\\n{4,}", "\\n\\n", text)
    ctx.count("collapses", n)
    return new_text
'''


def _load_custom_module(path: Path):
    name = f"chartcleaner_custom_{path.stem}"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CustomRuleError(f"Cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def list_custom_rules(custom_dir: str | Path | None) -> list[dict]:
    """Metadata for each custom rule file, sorted by name."""
    if custom_dir is None:
        return []
    directory = Path(custom_dir)
    if not directory.exists():
        return []
    out: list[dict] = []
    for p in sorted(directory.glob("*.py")):
        if p.name.startswith("_"):
            continue
        meta = {
            "name": p.stem,
            "file": str(p),
            "label": p.stem,
            "description": "",
            "placeholder": False,
            "error": None,
        }
        try:
            mod = _load_custom_module(p)
            meta["label"] = str(getattr(mod, "LABEL", p.stem) or p.stem)
            meta["description"] = str(getattr(mod, "DESCRIPTION", "") or "")
            meta["placeholder"] = bool(getattr(mod, "PLACEHOLDER", False))
            if not callable(getattr(mod, "clean", None)):
                meta["error"] = "Script has no clean(text, ctx) function"
        except Exception as e:
            meta["error"] = f"{type(e).__name__}: {e}"
        out.append(meta)
    return out


def _apply_custom(spec: StageSpec, text: str, ctx: CleanContext):
    """Run one custom rule. Returns (text, matches, details, skip_reason)."""
    meta = spec.meta
    if meta.get("error"):
        raise CustomRuleError(meta["error"])
    mod = _load_custom_module(Path(meta["file"]))
    clean = getattr(mod, "clean", None)
    if not callable(clean):
        raise CustomRuleError("script has no clean(text, ctx) function")
    if getattr(mod, "PLACEHOLDER", False):
        return text, 0, {}, "script is still a placeholder (PLACEHOLDER = True)"

    prev_counters = dict(ctx.counters)
    prev_logs = len(ctx.logs)
    result = clean(text, ctx)
    if not isinstance(result, str):
        raise CustomRuleError("clean() must return a string")
    matches = sum(ctx.counters.values()) - sum(prev_counters.values())
    details = {
        "counters": {
            k: v - prev_counters.get(k, 0)
            for k, v in ctx.counters.items()
            if v - prev_counters.get(k, 0)
        },
        "logs": ctx.logs[prev_logs:],
    }
    return result, matches, details, None


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

@dataclass
class StageSpec:
    id: str
    label: str
    kind: str
    enabled: bool
    meta: dict = field(default_factory=dict)


class Pipeline:
    """The ordered cleaning pipeline built from a config dict."""

    def __init__(self, config: dict, custom_dir: str | Path | None = None):
        missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
        if missing:
            raise ConfigError(f"Config missing required key(s): {', '.join(missing)}")
        self.config = config
        self.custom_dir = Path(custom_dir) if custom_dir else None
        self.stages: list[StageSpec] = self._build()

    # -- construction -------------------------------------------------------

    def _builtin_enabled(self, sid: str) -> bool:
        cfg = self.config
        if sid == "nlp_redaction":
            return bool((cfg.get("nlp_redaction") or {}).get("enabled", True))
        if sid == "tokenize_phi":
            return bool((cfg.get("tokenization") or {}).get("enabled", False))
        if sid == "duplicate_notes":
            return bool((cfg.get("duplicate_note_detection") or {}).get("enabled", True))
        if sid == "fuzzy_dedup":
            return bool((cfg.get("fuzzy_dedup") or {}).get("enabled", True))
        so = _stage_opts(cfg, sid)
        if "enabled" in so:
            return bool(so["enabled"])
        return True

    def _build(self) -> list[StageSpec]:
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
            if sid not in resolved:
                resolved.append(sid)
        for c in customs:
            sid = f"custom:{c['name']}"
            if sid not in resolved:
                resolved.append(sid)

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
            {"id": s.id, "label": s.label, "kind": s.kind, "enabled": s.enabled,
             "meta": s.meta if s.kind == "custom" else {}}
            for s in self.stages
        ]

    # -- execution -----------------------------------------------------------

    def run(self, text: str, wrap: bool | None = None) -> RunResult:
        started = time.perf_counter()
        ctx = CleanContext(self.config)
        warnings: list[str] = []
        stats: list[StageStat] = []
        chars_before, words_before = len(text), len(text.split())
        lines_before = text.count("\n") + 1

        for spec in self.stages:
            st = StageStat(id=spec.id, label=spec.label, kind=spec.kind, enabled=spec.enabled)
            st.chars_before = len(text)
            try:
                if not spec.enabled:
                    st.skipped = True
                elif spec.kind == "custom":
                    text, n, det, skip_reason = _apply_custom(spec, text, ctx)
                    if skip_reason:
                        st.skipped = True
                        st.details["note"] = skip_reason
                    else:
                        st.matches, st.details = n, det
                else:
                    text, n, det = RUNNERS[spec.kind](text, self.config, ctx)
                    st.matches, st.details = n, det
            except NlpUnavailable as e:
                st.skipped = True
                st.error = str(e)
                warnings.append(f"NLP redaction skipped: {e}")
            except Exception as e:  # one bad stage must not kill the run
                st.error = f"{type(e).__name__}: {e}"
                warnings.append(f"Stage '{spec.label}' failed: {st.error}")
            st.chars_after = len(text)
            stats.append(st)

        wrapped = bool(self.config.get("wrap_output", True)) if wrap is None else bool(wrap)
        if wrapped:
            tag = str(self.config.get("wrap_tag") or "patient_chart")
            st = StageStat(id="wrapper", label=f"<{tag}> wrapper", kind="wrapper")
            st.chars_before = len(text)
            text = f"<{tag}>\n{text}\n</{tag}>"
            st.chars_after = len(text)
            stats.append(st)

        return RunResult(
            text=text,
            wrapped=wrapped,
            stages=stats,
            warnings=warnings,
            duration_ms=(time.perf_counter() - started) * 1000,
            chars_before=chars_before,
            words_before=words_before,
            lines_before=lines_before,
        )


def clean_text(text: str, config: dict, custom_dir: str | Path | None = None,
               wrap: bool | None = None) -> RunResult:
    """One-shot convenience: build a pipeline and run it."""
    return Pipeline(config, custom_dir=custom_dir).run(text, wrap=wrap)
