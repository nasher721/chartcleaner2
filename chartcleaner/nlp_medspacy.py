"""Optional medspaCy integration: clinical section detection.

When `medspacy` is installed, its Sectionizer (rule-based, from the medspaCy
project — Mayo Clinic ecosystem) recognizes clinical section titles
(Assessment/Plan, Hospital Course, HPI, …) even when typed in ways the static
header list misses. The engine's Header-promotion stage uses
:func:`medspacy_sections` when config ``headers_engine`` is ``"medspacy"``.

Everything degrades gracefully: if the import or setup fails, the app simply
keeps using the shipped regex header list.
"""

from __future__ import annotations

import re
from importlib.util import find_spec

__all__ = ["medspacy_available", "medspacy_sections"]

_CACHE: dict = {"nlp": None, "sectionizer": None, "failed": False}

_TRAILING_PUNCT = re.compile(r"[\s:：]+$")


def medspacy_available() -> bool:
    return find_spec("medspacy") is not None


def _get_stack():
    if _CACHE["sectionizer"] is not None or _CACHE["failed"]:
        return _CACHE["nlp"], _CACHE["sectionizer"]
    try:
        import spacy
        from medspacy.section_detection import Sectionizer

        nlp = spacy.blank("en")
        sectionizer = Sectionizer(nlp, rules="default")
        _CACHE.update(nlp=nlp, sectionizer=sectionizer)
    except Exception:
        _CACHE["failed"] = True
        return None, None
    return _CACHE["nlp"], _CACHE["sectionizer"]


def medspacy_sections(text: str) -> list[tuple[str, int, int]]:
    """[(clean_title, start, end)] spans of section titles in `text`.

    `start`/`end` cover the raw title span (including its trailing colon) so
    the caller can replace it in place; `clean_title` is colon-free. Titles
    longer than 120 characters are ignored (they are usually false positives).
    Raises RuntimeError when medspaCy is unusable — callers fall back.
    """
    nlp, sectionizer = _get_stack()
    if sectionizer is None or nlp is None:
        raise RuntimeError("medspaCy is not usable in this environment")
    doc = sectionizer(nlp(text))  # the component needs a Doc, not a str
    out: list[tuple[str, int, int]] = []
    for title in doc._.section_titles:
        raw = title.text
        clean = _TRAILING_PUNCT.sub("", raw.strip())
        if clean and len(clean) <= 120:
            out.append((clean, title.start_char, title.end_char))
    return out
