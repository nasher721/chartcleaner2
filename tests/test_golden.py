"""Golden-file test: the engine's output on sample_chart.txt must not change silently.

The NLP (Presidio) stage is disabled here so the golden file depends only on
the deterministic regex/fuzzy rules, not on the installed spaCy model version.
If you change rules on purpose, regenerate and commit the new golden file:

    CHARTCLEANER_REGEN_GOLDEN=1 python -m pytest tests/test_golden.py
"""

import os
from pathlib import Path

from chartcleaner.engine import Pipeline, load_default_config

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).parent / "golden" / "sample_chart.cleaned.txt"


def _cleaned() -> str:
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    result = Pipeline(cfg, custom_dir=None).run(
        (ROOT / "sample_chart.txt").read_text(encoding="utf-8"))
    return result.text


def test_output_matches_golden():
    actual = _cleaned()
    if os.environ.get("CHARTCLEANER_REGEN_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(actual, encoding="utf-8")
    assert GOLDEN.exists(), "golden file missing — run once with CHARTCLEANER_REGEN_GOLDEN=1"
    assert actual == GOLDEN.read_text(encoding="utf-8"), (
        "Cleaning output changed. If the rule change is intentional, regenerate with "
        "CHARTCLEANER_REGEN_GOLDEN=1 and commit the new golden file.")
