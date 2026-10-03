"""Rule health report over run history."""

from chartcleaner.engine import clean_text
from chartcleaner.rule_health import report


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [r"^Printed by .*$", r"^Never present$"], "boilerplate": [],
           "epic_phi_patterns": [], "literal_replacements": [[r"\w+", "x"]], "clinical_headers": [],
           "nlp_redaction": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}}}
    cfg.update(over)
    return cfg


def _runs(n: int) -> list[dict]:
    text = "Printed by Epic\nPatient stable\nPlan unchanged\n"
    return [clean_text(text, base(), wrap=False).to_history_dict("test") for _ in range(n)]


def test_flags_never_matched_and_very_broad_rules():
    rows = {r["pattern"]: r for r in report(base(), _runs(6))}
    assert rows[r"^Printed by .*$"]["status"] == "ok"
    assert rows[r"^Printed by .*$"]["hits"] == 6
    assert rows[r"^Never present$"]["status"] == "never matched"
    assert rows[r"\w+"]["status"] == "very broad"


def test_needs_enough_instrumented_runs():
    assert {r["status"] for r in report(base(), _runs(2))} == {"not enough runs yet"}
    legacy = [{"stages": [{"id": "metadata_lines", "details": {}}], "lines_before": 3}] * 10
    assert {r["runs"] for r in report(base(), legacy)} == {0}


def test_problems_sort_first():
    statuses = [r["status"] for r in report(base(), _runs(6))]
    assert statuses[:2] == ["very broad", "never matched"]
