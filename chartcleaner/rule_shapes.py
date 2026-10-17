"""Rule shapes between "exactly this text" and a hand-written regex.

A *shape* is a small, readable description of a learned rule::

    {"kind": "line", "text": "Electronically signed by {name} on {date}",
     "template": True, "replacement": "", "case_sensitive": False,
     "whole_words": True}

``kind`` says how much goes:

* ``text``        — the phrase wherever it appears (what highlighting saves);
* ``line``        — every whole line that contains the phrase;
* ``line_start``  — every line that *starts* with the phrase;
* ``until``       — from the phrase up to a stop: ``until`` is
  ``"blank_line"``, ``"next_heading"`` or ``{"marker": "text"}`` (the stop
  itself is kept).

With ``template`` the text may hold placeholders (:data:`PLACEHOLDERS`), so one
rule covers every signature, date and name instead of one rule per chart.

:func:`build` compiles a shape to the ``[pattern, replacement]`` pair the
``learned_rules`` stage already runs, so nothing downstream changes; the shape
itself is kept beside the pair in ``stage_options.learned_rules.rule_meta``
(see :mod:`rule_meta`) for display, editing and the plain-text export.
:func:`generalize` proposes shapes for a highlighted snippet. Every pattern is
bounded (no unbounded repeat inside another), so :mod:`regex_risk` passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .regex_risk import risks as regex_risks

__all__ = ["KINDS", "PLACEHOLDERS", "STOPS", "Candidate", "build", "describe", "generalize",
           "generalized_text", "normalize", "placeholders_in", "template_regex"]

KINDS = ("text", "line", "line_start", "until")
STOPS = ("blank_line", "next_heading")

_MONTH = (r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]{0,6}\.?")
# placeholder -> (regex fragment, what it stands for). Fragments are bounded and
# never contain ``^``/``$`` so they work inside any shape.
PLACEHOLDERS: dict[str, tuple[str, str]] = {
    "date": (r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|" + _MONTH
             + r" \d{1,2},? \d{4})", "a date (10/14/2026, 2026-10-14, Oct 14, 2026)"),
    "time": (r"\d{1,2}:\d{2}(?::\d{2})?(?: ?[AaPp]\.?[Mm]\.?)?", "a time (08:12, 8:12 PM)"),
    "number": (r"\d{1,12}(?:[.,]\d{1,12}){0,3}", "a number"),
    # Case-sensitive even inside a case-insensitive rule: a name is capitalized.
    "name": (r"(?-i:[A-Z][A-Za-z'’.-]{0,30}(?:,? [A-Z][A-Za-z'’.-]{0,30}){0,4})",
             "a capitalized name (Jane Doe, J. Smith)"),
    "word": (r"[^\s]{1,60}", "any one word"),
    "any": (r"[^\n]{0,120}?", "anything on the same line"),
}
_PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")

# A heading line: "Plan:" / "ASSESSMENT AND PLAN" (what next_heading stops at).
_HEADING = (r"(?:[A-Z][A-Za-z0-9 /&(),'-]{0,60}:[ \t]*(?:\n|$)"
            r"|(?-i:[A-Z][A-Z0-9 /&(),'-]{2,60})[ \t]*(?:\n|$))")
# The most an ``until`` rule removes in one match: a missing stop must never
# take the rest of the chart with it.
MAX_BLOCK_CHARS = 5000


def placeholders_in(text: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(text or "")


def template_regex(text: str) -> str:
    """Escape ``text`` literally, except ``{date}``-style placeholders."""
    out, pos = [], 0
    for m in _PLACEHOLDER_RE.finditer(text):
        out.append(re.escape(text[pos:m.start()]))
        out.append(PLACEHOLDERS[m.group(1)][0])
        pos = m.end()
    out.append(re.escape(text[pos:]))
    return "".join(out)


def normalize(shape: dict) -> dict:
    """A complete, validated copy of ``shape`` (raises ``ValueError``)."""
    if not isinstance(shape, dict):
        raise ValueError("A rule shape must be an object.")
    kind = shape.get("kind", "text")
    if kind not in KINDS:
        raise ValueError(f"Unknown rule kind {kind!r}.")
    text = shape.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter some text for the rule first.")
    replacement = shape.get("replacement", "")
    if not isinstance(replacement, str):
        raise ValueError("Replacement must be text.")
    template = bool(shape.get("template", False))
    if template and not placeholders_in(text):
        template = False
    out = {"kind": kind, "text": text if kind == "text" else text.strip(),
           "replacement": replacement, "case_sensitive": bool(shape.get("case_sensitive", False)),
           "whole_words": bool(shape.get("whole_words", True)), "template": template}
    if kind in ("line", "line_start", "until") and replacement:
        raise ValueError("Line and block rules remove text; use a text rule to replace.")
    if kind == "until":
        until = shape.get("until", "blank_line")
        if isinstance(until, dict):
            marker = until.get("marker")
            if not isinstance(marker, str) or not marker.strip():
                raise ValueError("Enter the text the removal should stop at.")
            until = {"marker": marker.strip()}
        elif until not in STOPS:
            raise ValueError("Stop at a blank line, the next heading, or a marker.")
        out["until"] = until
    return out


def _body(shape: dict) -> str:
    text = shape["text"]
    return template_regex(text) if shape["template"] else re.escape(text)


def build(shape: dict) -> list[str]:
    """``[pattern, replacement]`` for ``shape`` (the learned_rules pair format)."""
    from .highlight_rules import make_rule

    shape = normalize(shape)
    kind = shape["kind"]
    if kind == "text" and not shape["template"]:
        # Byte-identical to what highlighting has always saved, so a shape and
        # an older literal rule for the same text are one rule, not two.
        return make_rule(shape["text"], shape["replacement"],
                         case_sensitive=shape["case_sensitive"], whole_words=shape["whole_words"])
    body = _body(shape)
    if kind == "text":
        if shape["whole_words"]:
            body = r"(?<!\w)" + body + r"(?!\w)"
        pattern = body
    elif kind == "line":
        pattern = r"^[^\n]{0,2000}?" + body + r"[^\n]{0,2000}(?:\n|\Z)"
    elif kind == "line_start":
        pattern = r"^[ \t]{0,40}" + body + r"[^\n]{0,2000}(?:\n|\Z)"
    else:
        until = shape["until"]
        if until == "blank_line":
            stop = r"(?=\n[ \t]*\n|\Z)"
        elif until == "next_heading":
            stop = r"(?=\n[ \t]*" + _HEADING + r"|\Z)"
        else:
            stop = "(?=" + re.escape(until["marker"]) + ")"
        pattern = r"^[ \t]{0,40}" + body + r"[\s\S]{0," + str(MAX_BLOCK_CHARS) + "}?" + stop
    flags = "m-i" if shape["case_sensitive"] else "im"
    replacement = shape["replacement"].replace("\\", "\\\\")
    return [f"(?{flags}:{pattern})", replacement]


def describe(shape: dict) -> str:
    """One readable sentence: “Remove whole lines containing “…””."""
    try:
        shape = normalize(shape)
    except ValueError:
        return "Advanced rule"
    text = f"“{shape['text']}”"
    kind = shape["kind"]
    if kind == "text":
        verb = f"Replace {text} with “{shape['replacement']}”" if shape["replacement"] else f"Remove {text}"
    elif kind == "line":
        verb = f"Remove whole lines containing {text}"
    elif kind == "line_start":
        verb = f"Remove lines starting with {text}"
    else:
        until = shape["until"]
        stop = {"blank_line": "the next blank line", "next_heading": "the next heading"}.get(
            until if isinstance(until, str) else "", f"“{until['marker']}”" if isinstance(until, dict) else "")
        verb = f"Remove from {text} up to {stop}"
    notes = []
    if shape["template"]:
        notes.append("placeholders: " + ", ".join(sorted(set(placeholders_in(shape["text"])))))
    if shape["case_sensitive"]:
        notes.append("match case")
    if kind == "text" and not shape["whole_words"]:
        notes.append("inside words")
    return verb + (f" ({'; '.join(notes)})" if notes else "")


# ---------------------------------------------------------------------------
# Generalize a highlighted snippet
# ---------------------------------------------------------------------------

_DATE = re.compile(PLACEHOLDERS["date"][0], re.IGNORECASE)
_TIME = re.compile(PLACEHOLDERS["time"][0])
_NUMBER = re.compile(r"(?<![\w{])\d+(?:[.,]\d+)*(?![\w}])")
# Two to four capitalized words (or initials), optionally "Last, First".
_NAME = re.compile(r"(?<![\w{])(?:Dr\.? )?[A-Z][a-z'’-]+(?:,? (?:[A-Z]\.|[A-Z][a-z'’-]+)){1,3}(?![\w}])")
# Words that start a sentence or a field label, never a name.
_NOT_NAMES = {"electronically", "signed", "reviewed", "cosigned", "attested", "patient", "note",
              "progress", "the", "this", "please", "printed", "page", "date", "time"}
_PREFIX_STOP = re.compile(r"\{|:|\d")


@dataclass
class Candidate:
    shape: dict
    label: str
    pattern: str

    def to_dict(self) -> dict:
        return {"shape": dict(self.shape), "label": self.label, "pattern": self.pattern}


def _replace_names(text: str) -> str:
    def repl(m: re.Match) -> str:
        words = m.group(0).replace(",", "").split()
        if words[0].casefold().rstrip(".") in _NOT_NAMES:
            return m.group(0)
        return "{name}"
    return _NAME.sub(repl, text)


def generalized_text(text: str, *, names: bool = True) -> str:
    """``text`` with its dates, times, numbers (and names) as ``{placeholders}``."""
    out = _DATE.sub("{date}", text)
    out = _TIME.sub("{time}", out)
    out = _NUMBER.sub("{number}", out)
    if names:
        out = _replace_names(out)
    return out


def generalize(text: str, *, case_sensitive: bool = False) -> list[Candidate]:
    """Rules that would catch ``text`` *and* its look-alikes, narrowest first.

    For “Electronically signed by Jane Doe, MD on 10/14/2026 08:12” that is the
    exact text, the text with its date/time/numbers as placeholders, the same
    with the name too, and “lines starting with Electronically signed by”.
    Duplicates and patterns :mod:`regex_risk` flags are dropped.
    """
    snippet = (text or "").replace("\r\n", "\n").strip()
    if not snippet:
        return []
    one_line = "\n" not in snippet
    seen: set[str] = set()
    out: list[Candidate] = []

    def add(shape: dict, label: str) -> None:
        try:
            pattern = build({"case_sensitive": case_sensitive, **shape})[0]
        except ValueError:
            return
        if pattern in seen or regex_risks(pattern):
            return
        seen.add(pattern)
        out.append(Candidate(normalize({"case_sensitive": case_sensitive, **shape}), label, pattern))

    add({"kind": "text", "text": snippet}, "Exactly this text")
    if one_line:
        add({"kind": "line", "text": snippet}, "Any line containing this text")
    numbers = generalized_text(snippet, names=False)
    if numbers != snippet and one_line:
        add({"kind": "line", "text": numbers, "template": True},
            "Lines like this with any date, time or number")
    named = generalized_text(snippet, names=True)
    if named not in (snippet, numbers) and one_line:
        add({"kind": "line", "text": named, "template": True},
            "Lines like this with any name, date, time or number")
    if one_line:
        stop = _PREFIX_STOP.search(named)
        prefix = named[:stop.start()] if stop else named
        prefix = prefix.rstrip(" ,;-").strip()
        if len(prefix) >= 6 and prefix != snippet and len(prefix.split()) >= 2:
            add({"kind": "line_start", "text": prefix}, f"Every line starting with “{prefix}”")
    else:
        first = snippet.split("\n", 1)[0].strip()
        if len(first) >= 4:
            add({"kind": "until", "text": first, "until": "blank_line"},
                "From the first line up to the next blank line")
            add({"kind": "until", "text": first, "until": "next_heading"},
                "From the first line up to the next heading")
    return out
