"""The required-literal prefilter must never skip a rule that could match."""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from chartcleaner import stage_core
from chartcleaner.engine import Pipeline, load_config, load_default_config
from chartcleaner.regex_prefilter import CASE_TROUBLE, may_match, requirements

ROOT = Path(__file__).resolve().parent.parent

# config key -> flags its stage runs with (see stage_regex / engine RUNNERS)
STAGE_FLAGS = {
    "emr_line_metadata": re.I | re.M,
    "boilerplate": re.I | re.M | re.S,
    "epic_phi_patterns": re.I,
    "literal_replacements": re.I,
}


def _shipped_rules():
    for cfg in (load_default_config(), load_config(ROOT / "config.json")):
        for key, flags in STAGE_FLAGS.items():
            for entry in cfg[key]:
                yield (entry[0] if isinstance(entry, list) else entry), flags


def _texts():
    sample = (ROOT / "sample_chart.txt").read_text(encoding="utf-8")
    rnd = random.Random(7)
    lines = sample.split("\n")
    yield sample
    yield sample.upper()
    yield sample.lower()
    for _ in range(40):
        yield "\n".join(rnd.choice(lines) for _ in range(rnd.randint(1, 30)))


def test_never_skips_a_shipped_rule_that_matches():
    texts = list(_texts())
    for pattern, flags in _shipped_rules():
        rx = re.compile(pattern, flags)
        for text in texts:
            if not may_match(pattern, flags, text):
                assert rx.search(text) is None, pattern


def test_every_shipped_rule_has_a_literal_requirement():
    """Otherwise it scans every chart; a new rule without one is worth a look."""
    missing = sorted({p for p, f in _shipped_rules() if not requirements(p, f)})
    assert missing == []


@pytest.mark.parametrize("pattern,flags,text,expected", [
    (r"Present on Admission:", re.I, "present on admission: yes", True),
    (r"Present on Admission:", 0, "present on admission: yes", False),
    (r"(?-i:SQ[A-Z]{2,})", re.I, "sqlab", False),          # scoped case-sensitive
    (r"(?i:(?<!\w)quiet\ hours(?!\w))", 0, "QUIET HOURS", True),
    (r"(?:Dose|Route):", re.I, "route: po", True),
    (r"(?:Dose|Route):", re.I, "frequency: daily", False),
    (r"(?:foo)?bar", 0, "bar", True),                       # optional part not required
    (r"x{0,3}abc", 0, "abc", True),
    (r"(?:ab){2}c", 0, "abc", True),                        # repeated literal: once is enough to prove
    (r"(?:ab){2}c", 0, "xyz", False),
    (r"(?i)signed", 0, "SIGNED", True),                     # global inline flag
    (r"sig ned", re.X, "signed", True),                     # verbose: spaces ignored
    (r"[", 0, "anything", True),                            # invalid: never skip
    (r"\d+", 0, "no digits", True),                         # no literal: never skip
])
def test_may_match_cases(pattern, flags, text, expected):
    assert may_match(pattern, flags, text) is expected


def test_case_trouble_set_covers_every_character_ignorecase_relates_to_ascii():
    """Derive the set from the regex engine instead of trusting the hand list.

    IGNORECASE compares simple lower-case forms (``_sre.unicode_tolower``) plus
    a case-fix table; ``str.lower`` may also expand a character (İ → i̇).
    """
    _sre = pytest.importorskip("_sre")
    related = set()
    for cp in range(0x80, 0x110000):
        ch = chr(cp)
        if _sre.unicode_tolower(cp) < 0x80 or any(ord(c) < 0x80 for c in ch.lower()):
            related.add(ch)
    try:
        from re import _casefix
        extra = _casefix._EXTRA_CASES
    except ImportError:  # pragma: no cover - Python 3.10
        import sre_compile
        extra = getattr(sre_compile, "_ignorecase_fixes", {})
    for cp, others in extra.items():
        # 3.11+ maps to a tuple of code points; keep this robust to a bare int.
        group = {cp, *(others if isinstance(others, (tuple, list)) else (others,))}
        if any(c < 0x80 for c in group):
            related |= {chr(c) for c in group if c >= 0x80}
    assert related <= CASE_TROUBLE


def test_trouble_characters_disable_case_insensitive_skips():
    kelvin = "\u212a"  # KELVIN SIGN: IGNORECASE relates it to "k"
    text = f"temp 300 {kelvin}"
    assert re.search("k", kelvin, re.I)
    assert may_match("300 k", re.I, text) is True
    assert may_match("ZZZ", re.I, text) is True  # can't rule out once trouble chars appear
    assert may_match("ZZZ", 0, text) is False    # case-sensitive checks still apply


def test_pipeline_output_identical_with_and_without_prefilter(monkeypatch):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    texts = list(_texts())[:12]
    fast = [Pipeline(cfg).run(t, wrap=False, fact_check=False, track_changes=True) for t in texts]
    monkeypatch.setattr(stage_core, "may_match", lambda *a, **k: True)
    slow = [Pipeline(cfg).run(t, wrap=False, fact_check=False, track_changes=True) for t in texts]
    for a, b in zip(fast, slow):
        assert a.text == b.text
        for sa, sb in zip(a.stages, b.stages):
            assert sa.matches == sb.matches
            assert sa.details.get("rule_hits") == sb.details.get("rule_hits")
            assert sa.details.get("changes") == sb.details.get("changes")
