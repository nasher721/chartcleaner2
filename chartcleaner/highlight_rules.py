"""Literal highlight rules using the existing portable regex-pair format."""

from __future__ import annotations

import re


def make_rule(text: str, replacement: str = "", *, case_sensitive: bool = False,
              whole_words: bool = True) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Highlight some non-empty text first.")
    if not isinstance(replacement, str):
        raise ValueError("Replacement must be text.")
    pattern = re.escape(text)
    if whole_words:
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    # Scoped flags preserve each rule's choices regardless of stage defaults.
    pattern = f"(?{'-i' if case_sensitive else 'i'}:{pattern})"
    return [pattern, replacement.replace("\\", "\\\\")]


def describe_rule(pair: list[str]) -> dict | None:
    """Decode only our canonical literal rules; preserve other regexes verbatim."""
    pattern, replacement = pair
    sensitive = pattern.startswith("(?-i:")
    prefix = "(?-i:" if sensitive else "(?i:"
    if not pattern.startswith(prefix) or not pattern.endswith(")"):
        return None
    body = pattern[len(prefix):-1]
    whole_words = body.startswith(r"(?<!\w)") and body.endswith(r"(?!\w)")
    if whole_words:
        body = body[len(r"(?<!\w)"):-len(r"(?!\w)")]
    text = re.sub(r"\\([\s\S])", r"\1", body)
    literal_replacement = replacement.replace("\\\\", "\\")
    result = dict(text=text, replacement=literal_replacement,
                  case_sensitive=sensitive, whole_words=whole_words)
    try:
        return result if make_rule(**result) == list(pair) else None
    except ValueError:
        return None


def replace_selection(current: str, selection: dict, replacement: str) -> str:
    """Reject stale selections instead of applying offsets to a changed chart."""
    start, end = selection.get("start"), selection.get("end")
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(current)
            or current != selection.get("value")
            or current[start:end] != selection.get("text")):
        raise ValueError("The chart changed. Highlight the text again.")
    return current[:start] + replacement + current[end:]


# Array.from converts browser UTF-16 offsets to Python Unicode code-point offsets.
# Commit after pointer release / keyboard selection, never during a drag.
SELECTION_HANDLER = """(event) => {
    if (event.type === 'keyup' && (event.shiftKey || event.ctrlKey || event.metaKey)) return;
    const ta = event.target;
    if (!(ta instanceof HTMLTextAreaElement)) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    if (start === end || !ta.value.slice(start, end).trim()) return;
    emit({start: Array.from(ta.value.slice(0, start)).length,
          end: Array.from(ta.value.slice(0, end)).length,
          text: ta.value.slice(start, end), value: ta.value});
}"""
