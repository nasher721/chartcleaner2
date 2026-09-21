"""Built-in stage runners for the Chart Cleaner pipeline.

Each stage runner implements the contract:
    runner(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]
returning (new_text, match_count, stage_details).
"""

from __future__ import annotations

import re
import textwrap
import threading
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .engine import CleanContext

DEFAULT_NOTE_SPLIT = (
    r"(?=^(?:Progress Notes by .+|Attestation signed by .+|"
    r"[A-Z][a-zA-Z,.\s\-]+ at \d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s*(?:[APap]\.?[Mm]\.?)?.*)$)"
)

DEFAULT_NLP_ENTITIES = {
    "PERSON": "[REDACTED_NAME]",
    "PHONE_NUMBER": "[REDACTED_PHONE]",
    "EMAIL_ADDRESS": "[REDACTED_EMAIL]",
}

DEFAULT_WHITESPACE = {
    "crlf_to_lf": False,
    "trim_trailing": True,
    "collapse_blank_lines": True,
    "collapse_spaces": False,
    "strip_leading": False,
    "normalize_tabs": False,
    "final_trim": True,
}

DEFAULT_BULLETS = {
    "style": "- ",
    "normalize_numbered": False,
    "skip_empty": False,
    "extra_glyphs": [],
}

DEFAULT_HEADER_OPTIONS = {
    "level": 2,
    "bold": False,
    "keep_colon": False,
    "uppercase_only": False,
}

DEFAULT_UNICODE = {
    "quotes": False,
    "dashes": False,
    "nbsp": False,
    "zero_width": False,
    "ellipsis": False,
    "ligatures": False,
}

DEFAULT_TIMESTAMPS = {
    "enabled": False,
    "replacement": "",
    "builtin_patterns": True,
    "extra_patterns": [],
    "remove_clock_times": False,
}

DEFAULT_SECTIONS = {
    "mode": "off",
    "sections": [],
    "boundary_headers": [],
    "keep_preamble": True,
}

DEFAULT_CAPS = {
    "mode": "off",
    "min_chars": 40,
    "preserve_words": [],
}

DEFAULT_LINE_LENGTH = {
    "mode": "off",
    "max_chars": 200,
    "marker": "…",
}

UNICODE_REPLACEMENTS = {
    "quotes": [("\u201c", '"'), ("\u201d", '"'), ("\u2018", "'"), ("\u2019", "'"),
               ("\u201a", ","), ("\u201e", '"')],
    "dashes": [("\u2013", "-"), ("\u2014", "-"), ("\u2212", "-")],
    "nbsp": [("\u00a0", " ")],
    "zero_width": [("\u200b", ""), ("\u200c", ""), ("\u200d", ""), ("\ufeff", "")],
    "ellipsis": [("\u2026", "...")],
    "ligatures": [("\ufb00", "ff"), ("\ufb01", "fi"), ("\ufb02", "fl"),
                  ("\ufb03", "ffi"), ("\ufb04", "ffl")],
}

BUILTIN_TIMESTAMP_PATTERNS = [
    r"\b\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?(?:\s?(?:Z|[+-]\d{2}:?\d{2}))?\b",
    r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"(?:,?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)?\b",
]

CLOCK_TIME_PATTERN = r"(?<![\d:])\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?\b(?![\d:])"


class NlpUnavailable(Exception):
    """Raised when the Presidio/spaCy stack is not usable on this machine."""


def get_stage_options(cfg: dict, sid: str) -> dict:
    """Return options from config['stage_options'][sid] if present."""
    so = cfg.get("stage_options")
    if not isinstance(so, dict):
        return {}
    opts = so.get(sid)
    return opts if isinstance(opts, dict) else {}


# ---------------------------------------------------------------------------
# Regex Stage Runners
# ---------------------------------------------------------------------------

def run_regex_list(
    text: str, cfg: dict, ctx: CleanContext, key: str, flags: int, sid: str | None = None
) -> tuple[str, int, dict]:
    """Execute a list of regex patterns and delete matches."""
    if sid and get_stage_options(cfg, sid).get("case_sensitive"):
        flags &= ~re.IGNORECASE
    total = 0
    for pattern in cfg[key]:
        r = re.compile(pattern, flags=flags)
        text, n = r.subn("", text)
        total += n
    return text, total, {}


def run_regex_pairs(
    text: str, cfg: dict, ctx: CleanContext, key: str, phi: bool = False, sid: str | None = None
) -> tuple[str, int, dict]:
    """Execute a list of [pattern, replacement] pairs."""
    flags = re.IGNORECASE
    if sid and get_stage_options(cfg, sid).get("case_sensitive"):
        flags = 0
    total = 0
    for pattern, replacement in cfg[key]:
        r = re.compile(pattern, flags=flags)
        text, n = r.subn(replacement, text)
        total += n
    details = {"phi": {"pattern_redactions": total}} if phi else {}
    return text, total, details


# ---------------------------------------------------------------------------
# Text Normalization Runners
# ---------------------------------------------------------------------------

def run_whitespace(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Clean whitespace based on configured rules (line endings, spaces, blank lines)."""
    options = {**DEFAULT_WHITESPACE, **(cfg.get("whitespace") or {})}
    total_modifications = 0

    if options.get("crlf_to_lf"):
        text, k = re.subn(r"\r\n?", "\n", text)
        total_modifications += k

    if options.get("strip_leading"):
        text, k = re.subn(r"^[ \t]+", "", text, flags=re.MULTILINE)
        total_modifications += k

    if options.get("normalize_tabs"):
        text, k = re.subn(r"\t", "    ", text)
        total_modifications += k

    if options.get("trim_trailing"):
        text, k = re.subn(r"[ \t]+$", "", text, flags=re.MULTILINE)
        total_modifications += k

    if options.get("collapse_spaces"):
        text, k = re.subn(r"[ ]{2,}", " ", text)
        total_modifications += k

    if options.get("collapse_blank_lines"):
        text, k = re.subn(r"\n{3,}", "\n\n", text)
        total_modifications += k

    if options.get("final_trim"):
        stripped = text.strip()
        if stripped != text:
            total_modifications += 1
            text = stripped

    return text, total_modifications, {}


def run_bullets(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Standardize or strip bullet glyphs and numbered lists."""
    options = {**DEFAULT_BULLETS, **(cfg.get("bullets") or {})}
    style = str(options.get("style") if options.get("style") is not None else "- ")
    extra_glyphs = "".join(g for g in (options.get("extra_glyphs") or []) if isinstance(g, str))
    glyphs = "[•\\-*" + re.escape(extra_glyphs) + "]"
    total = 0

    if options.get("skip_empty"):
        text, k = re.subn(rf"^\s*{glyphs}[ \t]*$\n?", "", text, flags=re.MULTILINE)
        total += k

    if options.get("normalize_numbered"):
        text, k = re.subn(rf"^\s*\d+[.)]\s+", style, text, flags=re.MULTILINE)
        total += k

    text, k = re.subn(rf"^\s*{glyphs}\s+", style, text, flags=re.MULTILINE)
    total += k
    return text, total, {}


def run_unicode(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Normalize unicode characters such as curly quotes, dashes, and ligatures."""
    options = {**DEFAULT_UNICODE, **(cfg.get("unicode_normalize") or {})}
    total = 0
    for opt, pairs in UNICODE_REPLACEMENTS.items():
        if not options.get(opt):
            continue
        for old, new in pairs:
            text, k = re.subn(re.escape(old), new, text)
            total += k
    return text, total, {}


def run_caps(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Normalize ALL-CAPS lines to sentence case, preserving medical acronyms."""
    options = {**DEFAULT_CAPS, **(cfg.get("caps_normalize") or {})}
    if str(options.get("mode") or "off") != "sentence":
        return text, 0, {}

    min_chars = _get_positive_int(options.get("min_chars"), default=40)
    preserve_re = _build_preserve_regex(options.get("preserve_words"))

    lines = text.split("\n")
    changed = 0
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if len(trimmed) < min_chars or not trimmed.isupper() or not any(c.isalpha() for c in trimmed):
            continue

        sentence = trimmed.lower()
        sentence = sentence[0].upper() + sentence[1:]
        if preserve_re:
            sentence = preserve_re.sub(lambda m: m.group(1).upper(), sentence)

        indent = line[:len(line) - len(line.lstrip())]
        lines[i] = indent + sentence
        changed += 1

    return "\n".join(lines), changed, {}


def _get_positive_int(val: Any, default: int) -> int:
    try:
        return max(1, int(val or default))
    except (TypeError, ValueError):
        return default


def _build_preserve_regex(words: Any) -> re.Pattern | None:
    keep = [w for w in (words or []) if isinstance(w, str) and w]
    if not keep:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in keep) + r")\b", re.IGNORECASE)


def run_line_length(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Wrap or truncate lines that exceed maximum character limits."""
    options = {**DEFAULT_LINE_LENGTH, **(cfg.get("line_length") or {})}
    mode = str(options.get("mode") or "off")
    try:
        max_chars = int(options.get("max_chars") or 0)
    except (TypeError, ValueError):
        max_chars = 0

    if mode == "off" or max_chars < 10:
        return text, 0, {}

    marker = str(options.get("marker") or "…")
    count = 0
    out: list[str] = []

    for line in text.split("\n"):
        if len(line) <= max_chars:
            out.append(line)
            continue
        if mode == "truncate":
            out.append(_truncate_line(line, max_chars, marker))
        else:
            out.extend(_wrap_line(line, max_chars))
        count += 1

    return "\n".join(out), count, {}


def _truncate_line(line: str, max_chars: int, marker: str) -> str:
    return line[:max_chars].rstrip() + marker


def _wrap_line(line: str, max_chars: int) -> list[str]:
    indent = line[:len(line) - len(line.lstrip())]
    body = line[len(indent):]
    wrapped = textwrap.wrap(
        body,
        width=max(1, max_chars - len(indent)),
        break_long_words=False,
        break_on_hyphens=False,
        subsequent_indent=indent,
    ) or [""]
    return [indent + wrapped[0]] + wrapped[1:]


# ---------------------------------------------------------------------------
# Content Structure Runners
# ---------------------------------------------------------------------------

def run_headers(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Format clinical section headers as markdown or bold labels."""
    options = {**DEFAULT_HEADER_OPTIONS, **(cfg.get("header_options") or {})}
    try:
        level = max(1, min(6, int(options.get("level") or 2)))
    except (TypeError, ValueError):
        level = 2
    bold = bool(options.get("bold"))
    keep_colon = bool(options.get("keep_colon"))
    upper_only = bool(options.get("uppercase_only"))

    def style_header(title: str, colon: str) -> str:
        if bold:
            return f"**{title}{colon}**"
        return f"{'#' * level} {title}{colon}"

    engine = (cfg.get("headers_engine") or "regex").lower()
    if engine == "medspacy":
        medspacy_result = _try_medspacy_headers(text, ctx, style_header)
        if medspacy_result is not None:
            return medspacy_result

    return _run_regex_headers(text, cfg["clinical_headers"], style_header, keep_colon, upper_only)


def _try_medspacy_headers(
    text: str, ctx: CleanContext, style_header: Callable[[str, str], str]
) -> tuple[str, int, dict] | None:
    try:
        from .nlp_medspacy import medspacy_sections
        sections = medspacy_sections(text)
        if not sections:
            return None
        out, n = text, 0
        for title, start, end in sorted(sections, key=lambda s: -s[1]):
            out = out[:start] + style_header(title, "") + out[end:]
            n += 1
        return out, n, {"engine": "medspacy"}
    except Exception as e:
        ctx.log(f"medspaCy section engine unavailable ({e}); using regex headers.")
        return None


def _run_regex_headers(
    text: str, headers: list[str], style_header: Callable[[str, str], str],
    keep_colon: bool, upper_only: bool
) -> tuple[str, int, dict]:
    def make_replacement(match: re.Match) -> str:
        if upper_only:
            letters = [c for c in match.group(1) if c.isalpha()]
            if letters and not all(c.isupper() for c in letters):
                return match.group(0)
        colon = (match.group(2) or "") if keep_colon else ""
        return style_header(match.group(1), colon)

    total = 0
    for h in headers:
        pat = rf"^\s*({h})(\s*:)?\s*$" if keep_colon else rf"^\s*({h})\s*:?\s*$"
        r = re.compile(pat, re.IGNORECASE | re.MULTILINE)
        text, n = r.subn(make_replacement, text)
        total += n
    return text, total, {}


def run_timestamps(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Strip or replace date/time stamps."""
    options = {**DEFAULT_TIMESTAMPS, **(cfg.get("timestamp_removal") or {})}
    if not options.get("enabled"):
        return text, 0, {}

    repl = str(options.get("replacement") or "")
    patterns = list(options.get("extra_patterns") or [])
    if options.get("builtin_patterns", True):
        patterns = BUILTIN_TIMESTAMP_PATTERNS + patterns

    total = 0
    for p in patterns:
        if not isinstance(p, str) or not p:
            continue
        try:
            r = re.compile(p, re.IGNORECASE)
        except re.error:
            ctx.log(f"bad timestamp pattern skipped: {p!r}")
            continue
        text, k = r.subn(repl, text)
        total += k

    if options.get("remove_clock_times"):
        text, k = re.subn(CLOCK_TIME_PATTERN, repl, text)
        total += k

    return text, total, {}


def run_sections(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Filter document sections by dropping or keeping specified headers."""
    options = {**DEFAULT_SECTIONS, **(cfg.get("section_filter") or {})}
    mode = str(options.get("mode") or "off")
    if mode not in ("drop", "keep"):
        return text, 0, {}

    names = [n for n in (options.get("sections") or []) if isinstance(n, str) and n]
    if not names:
        return text, 0, {}

    boundary = [b for b in (options.get("boundary_headers") or []) if isinstance(b, str) and b]
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

    return _filter_section_bounds(text, bounds, member_re, mode, bool(options.get("keep_preamble", True)))


def _filter_section_bounds(
    text: str, bounds: list[tuple[int, str]], member_re: re.Pattern, mode: str, keep_preamble: bool
) -> tuple[str, int, dict]:
    first_start = bounds[0][0]
    out = text[:first_start] if keep_preamble else ""
    pos = first_start
    removed: list[str] = []

    for i, (start, header) in enumerate(bounds):
        seg_end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        is_member = member_re.match(text[start:seg_end].split("\n", 1)[0] + "\n") is not None
        should_remove = is_member if mode == "drop" else not is_member
        if should_remove:
            removed.append(header.strip())
        else:
            out += text[pos:seg_end]
        pos = seg_end

    out += text[pos:]
    return out, len(removed), {"sections_removed": removed}


# ---------------------------------------------------------------------------
# Deduplication Runners
# ---------------------------------------------------------------------------

def fuzz_ratio(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings using RapidFuzz/TheFuzz."""
    from thefuzz import fuzz
    return fuzz.ratio(a, b)


def run_duplicate_notes(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Fold repeated copy-forward notes based on body similarity."""
    dcfg = cfg.get("duplicate_note_detection") or {}
    if dcfg.get("enabled") is False:
        return text, 0, {}

    split_pattern = dcfg.get("split_pattern", DEFAULT_NOTE_SPLIT)
    min_body = int(dcfg.get("min_body_chars", 400))
    threshold = int(dcfg.get("similarity_threshold", 90))

    parts = re.compile(split_pattern, re.MULTILINE | re.IGNORECASE).split(text)
    if len(parts) <= 1:
        return text, 0, {}

    from rapidfuzz import fuzz as rf_fuzz, process as rf_process

    kept = [parts[0]]
    seen_bodies: list[str] = []
    dropped = 0

    for seg in parts[1:]:
        body = _extract_body(seg)
        if len(body) < min_body:
            kept.append(seg)
            continue
        body_low = body.lower()
        if seen_bodies and rf_process.extractOne(
            body_low, seen_bodies, scorer=rf_fuzz.ratio, score_cutoff=threshold
        ) is not None:
            dropped += 1
            continue
        seen_bodies.append(body_low)
        kept.append(seg)

    return "".join(kept), dropped, {"notes_kept": len(kept)}


def _extract_body(segment: str) -> str:
    s = segment.strip()
    if not s or "\n" not in s:
        return ""
    return s.split("\n", 1)[1].strip()


def run_fuzzy_dedup(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Deduplicate repeated paragraphs using fuzzy string matching."""
    fcfg = cfg.get("fuzzy_dedup") or {}
    if fcfg.get("enabled") is False:
        return text, 0, {}

    threshold = int(fcfg.get("threshold", 95))
    min_chars = int(fcfg.get("min_chars", 100))

    from rapidfuzz import fuzz as rf_fuzz, process as rf_process

    paragraphs = text.split("\n\n")
    deduped: list[str] = []
    eligible: list[str] = []  # lowered, stripped candidates that can match future paragraphs
    dropped = 0

    for p in paragraphs:
        p_clean = p.strip()
        if len(p_clean) < min_chars:
            deduped.append(p)
            continue
        cand_low = p_clean.lower()
        if eligible and rf_process.extractOne(
            cand_low, eligible, scorer=rf_fuzz.ratio, score_cutoff=threshold
        ) is not None:
            dropped += 1
        else:
            deduped.append(p)
            if len(p_clean) > min_chars:
                eligible.append(cand_low)

    return "\n\n".join(deduped), dropped, {"paragraphs_kept": len(deduped)}


# ---------------------------------------------------------------------------
# Privacy & PHI Runners
# ---------------------------------------------------------------------------

class PresidioEngineCache:
    """Manages cached Presidio analyzer and anonymizer engines."""

    def __init__(self):
        self._signature: tuple | None = None
        self._analyzer: Any = None
        self._anonymizer: Any = None
        self._lock = threading.Lock()

    def get_engines(self, entities_key: tuple, allow: frozenset, threshold: float | None):
        sig = (entities_key, allow, threshold)
        if self._signature == sig:
            return self._analyzer, self._anonymizer
        with self._lock:
            if self._signature == sig:
                return self._analyzer, self._anonymizer

        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import SpacyNlpEngine
            from presidio_anonymizer import AnonymizerEngine
        except Exception as e:
            raise NlpUnavailable(
                f"Presidio could not be imported ({e}). Run install.sh / install.bat to add it."
            ) from e

        if find_spec("en_core_web_sm") is None:
            raise NlpUnavailable("spaCy model 'en_core_web_sm' is not installed in this environment.")

        try:
            nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
            analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
            _disable_expensive_spacy_pipes(analyzer.nlp_engine)
            anonymizer = AnonymizerEngine()
        except Exception as e:
            raise NlpUnavailable(f"Could not initialize the NLP engine ({e}).") from e

        self._signature = sig
        self._analyzer = analyzer
        self._anonymizer = anonymizer
        return self._analyzer, self._anonymizer

    def clear(self) -> None:
        self._signature = None
        self._analyzer = None
        self._anonymizer = None


def _disable_expensive_spacy_pipes(nlp_engine: Any) -> None:
    """Drop the spaCy dependency parser from the loaded model.

    The redaction stage consumes PERSON/PHONE_NUMBER/EMAIL entities and token
    lemmas (for context enhancement); none of them depend on the parser, and
    skipping it cuts spaCy annotation time by ~25% on long charts.
    """
    try:
        for nlp in (getattr(nlp_engine, "nlp", None) or {}).values():
            if "parser" in nlp.pipe_names:
                nlp.disable_pipe("parser")
    except Exception:
        pass  # fall back to the untouched full pipeline


_PRESIDIO_CACHE = PresidioEngineCache()


def run_nlp(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Redact PHI entities using Presidio Analyzer and Anonymizer."""
    from presidio_anonymizer.entities import OperatorConfig

    ncfg = cfg.get("nlp_redaction") or {}
    entities: dict[str, str] = dict(ncfg.get("entities") or DEFAULT_NLP_ENTITIES)
    allow = frozenset(t.lower() for t in (cfg.get("nlp_allow_list") or []))
    threshold = ncfg.get("score_threshold")

    analyzer, anonymizer = _PRESIDIO_CACHE.get_engines(
        tuple(sorted(entities.items())), allow, threshold
    )

    results = analyzer.analyze(text=text, entities=list(entities), language="en")
    if threshold is not None:
        results = [r for r in results if r.score >= threshold]

    from .clinical_whitelist import is_clinical_term, is_non_person_text

    protect_clinical = bool(ncfg.get("protect_clinical_terms", True))
    filtered = [
        r for r in results
        if text[r.start:r.end].lower() not in allow
        and not (
            protect_clinical
            and r.entity_type == "PERSON"
            and (is_clinical_term(text[r.start:r.end]) or is_non_person_text(text[r.start:r.end]))
        )
    ]

    operators = {
        etype: OperatorConfig("replace", {"new_value": repl})
        for etype, repl in entities.items()
    }
    new_text = anonymizer.anonymize(text=text, analyzer_results=filtered, operators=operators).text

    counts: dict[str, int] = {}
    for r in filtered:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return new_text, len(filtered), {"phi": counts}


DEFAULT_CLINICAL_IDENTIFIERS = {
    "enabled": False,
    "replacement": None,  # None defaults to [REDACTED_{TYPE}]
    "redact_npi": True,
    "redact_dea": True,
    "redact_udi": True,
}


def run_clinical_identifiers(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Redact algorithmic clinical identifiers (NPI, DEA, UDI) with checksum verification."""
    options = {**DEFAULT_CLINICAL_IDENTIFIERS, **(cfg.get("clinical_identifiers") or {})}
    if not options.get("enabled"):
        return text, 0, {}

    from .clinical_identifiers import scan_clinical_identifiers

    entities = scan_clinical_identifiers(text)
    if not entities:
        return text, 0, {}

    redact_npi = bool(options.get("redact_npi", True))
    redact_dea = bool(options.get("redact_dea", True))
    redact_udi = bool(options.get("redact_udi", True))
    custom_repl = options.get("replacement")

    counts: dict[str, int] = {}
    out = text
    matched = 0

    # Sort descending by start to safely replace in-place
    for ent in sorted(entities, key=lambda e: -e.start):
        if ent.entity_type == "NPI" and not redact_npi:
            continue
        if ent.entity_type == "DEA_NUMBER" and not redact_dea:
            continue
        if ent.entity_type in ("UDI_DEVICE_ID", "ACCESSION_NUMBER") and not redact_udi:
            continue

        repl = str(custom_repl) if custom_repl is not None else f"[REDACTED_{ent.entity_type}]"
        out = out[:ent.start] + repl + out[ent.end:]
        counts[ent.entity_type] = counts.get(ent.entity_type, 0) + 1
        matched += 1

    return out, matched, {"phi": counts} if counts else {}


def run_tokenize(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Replace PHI with reversible tokens ([[Tn]]) and store token mapping."""
    from .tokens import DEFAULT_TOKEN_CONFIG, tokenize

    tcfg = {**DEFAULT_TOKEN_CONFIG, **(cfg.get("tokenization") or {})}
    labels: list[str] = []
    try:
        from .audit import DEFAULT_NAME_LABELS, get_audit_config
        labels = list(get_audit_config(cfg)["checks"]["label_names"].get("labels") or DEFAULT_NAME_LABELS)
    except Exception:
        pass

    new_text, mapping = tokenize(
        text,
        cfg.get("epic_phi_patterns") or [],
        labels=labels,
        prefix=str(tcfg.get("prefix") or "T"),
    )
    details: dict = {"tokens_issued": len(mapping)}
    if mapping:
        details["token_map"] = mapping
        details["phi"] = {"tokenized": len(mapping)}
    return new_text, len(mapping), details


# ---------------------------------------------------------------------------
# Stage Dispatch Mapping
# ---------------------------------------------------------------------------

RUNNERS: dict[str, Callable] = {
    "regex_lines": lambda t, c, x: run_regex_list(t, c, x, "emr_line_metadata", re.IGNORECASE | re.MULTILINE, sid="metadata_lines"),
    "regex_lines_dotall": lambda t, c, x: run_regex_list(t, c, x, "boilerplate", re.IGNORECASE | re.DOTALL | re.MULTILINE, sid="boilerplate"),
    "regex_pairs_phi": lambda t, c, x: run_regex_pairs(t, c, x, "epic_phi_patterns", phi=True, sid="phi_patterns"),
    "clinical_identifiers": run_clinical_identifiers,
    "nlp": run_nlp,
    "tokenize": run_tokenize,
    "regex_pairs": lambda t, c, x: run_regex_pairs(t, c, x, "literal_replacements", sid="literal_replacements"),
    "unicode": run_unicode,
    "timestamps": run_timestamps,
    "sections": run_sections,
    "whitespace": run_whitespace,
    "dedup_notes": run_duplicate_notes,
    "fuzzy": run_fuzzy_dedup,
    "headers": run_headers,
    "caps": run_caps,
    "bullets": run_bullets,
    "line_length": run_line_length,
}

KIND_TO_RUNNER = {
    "metadata_lines": "regex_lines",
    "boilerplate": "regex_lines_dotall",
    "phi_patterns": "regex_pairs_phi",
    "clinical_identifiers": "clinical_identifiers",
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
