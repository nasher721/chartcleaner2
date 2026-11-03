from __future__ import annotations

import subprocess
import sys

from chartcleaner.clean_compute import compute_clean, review_details
from chartcleaner.engine import Pipeline, load_default_config
from chartcleaner.review_state import release_decision


def _cfg():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    return cfg


def test_compute_bundle_preserves_pipeline_validation_and_provenance():
    cfg = _cfg()
    text = "Printed by Dr. Lee\nPlan: history of hypertension. Recheck K 4.1 tomorrow.\n"
    expected = Pipeline(cfg).run(text, track_changes=True)
    got = compute_clean(text, "clean", cfg, None, lazy=False)

    assert got.result.text == expected.text
    assert got.result.validation == expected.validation
    assert [p.to_summary() for p in got.result.ledger.proposals] == [
        p.to_summary() for p in expected.ledger.proposals]
    assert release_decision(got.result, output_text=got.result.text, source=text) == \
        release_decision(expected, output_text=expected.text, source=text)
    assert got.groups and got.finding_rows is not None
    assert got.review == review_details(text, got.result, got.audit)
    assert got.extras["redactions"] is not None


def test_lazy_compute_defers_full_review_and_nonessential_insights():
    got = compute_clean("Plan: recheck K 4.1\n", "clean", _cfg(), None, lazy=True)
    assert got.review is None
    assert set(got.extras) == {"safety", "conflicts", "timeline", "redactions"}


def test_review_html_module_does_not_import_nicegui():
    code = (
        "import sys; import chartcleaner.review_html; "
        "assert 'nicegui' not in sys.modules; "
        "print(chartcleaner.review_html.review_diff_html('a\\n', 'b\\n', None, []))"
    )
    completed = subprocess.run([sys.executable, "-c", code], check=True,
                               capture_output=True, text=True)
    assert "cc-diff" in completed.stdout
