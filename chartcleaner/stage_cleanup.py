"""Text-normalization runners: whitespace, bullets, unicode, caps, line length,
headers (split from ``stages.py``)."""

from __future__ import annotations

import re
import textwrap
from typing import TYPE_CHECKING, Any, Callable

from .stage_core import _compiled

if TYPE_CHECKING:
    from .engine import CleanContext

__all__ = [
    "DEFAULT_WHITESPACE",
    "DEFAULT_BULLETS",
    "DEFAULT_HEADER_OPTIONS",
    "DEFAULT_UNICODE",
    "DEFAULT_CAPS",
    "DEFAULT_LINE_LENGTH",
    "UNICODE_REPLACEMENTS",
    "run_whitespace",
    "run_bullets",
    "run_unicode",
    "run_caps",
    "run_line_length",
    "run_headers",
]

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
            # Literal (non-regex) replacements: str.replace swaps substrings
            # directly and is much cheaper than a regex pass per character.
            n = text.count(old)
            if n:
                text = text.replace(old, new)
                total += n
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
        r = _compiled(pat, re.IGNORECASE | re.MULTILINE)
        text, n = r.subn(make_replacement, text)
        total += n
    return text, total, {}
