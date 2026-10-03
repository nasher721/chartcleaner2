"""Opt-in condensing stages: imaging impression, hospital day (and their wiring)."""

import pytest

from chartcleaner.compactors import hospital_day, imaging
from chartcleaner.engine import BUILTIN_STAGE_IDS, Pipeline, clean_text, validate_config


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}}}
    cfg.update(over)
    return cfg


REPORT = """CT HEAD WITHOUT CONTRAST 10/02/2026
INDICATION: headache
TECHNIQUE: axial images
COMPARISON: 10/01/2026
FINDINGS: Stable SAH in basal cisterns.
Ventricles normal.
IMPRESSION: Stable SAH. No new hemorrhage.

Assessment and Plan:
Impression: SAH day 5
"""


def test_imaging_keeps_title_and_impression_only():
    out, n, details = imaging.run(REPORT, {"imaging_impression": {"enabled": True}})
    assert out.startswith("CT HEAD WITHOUT CONTRAST 10/02/2026\nIMPRESSION: Stable SAH.")
    assert "FINDINGS" not in out and "TECHNIQUE" not in out
    assert "Assessment and Plan:\nImpression: SAH day 5" in out
    assert n == 1 and details == {"reports_trimmed": 1}


def test_imaging_keep_findings():
    out, _, _ = imaging.run(REPORT, {"imaging_impression": {"enabled": True, "keep": ["findings"]}})
    assert "FINDINGS: Stable SAH" in out and "TECHNIQUE" not in out


@pytest.mark.parametrize("text", [
    "FINDINGS: small effusion\nNo impression given.\n",
    "History of Present Illness: fell\nFINDINGS: bruise\nPlan: ice\nImpression: contusion\n",
])
def test_imaging_leaves_non_reports_alone(text):
    assert imaging.run(text, {"imaging_impression": {"enabled": True}})[0] == text


def test_hospital_day_append_with_pod():
    text = "Admission Date: 09/30/2026\nNote 10/02/2026: stable. Prior 01/02/2020."
    out, n, details = hospital_day.run(text, {"hospital_day": {
        "enabled": True, "surgery_dates": ["2026-10-01"]}})
    assert "Note 10/02/2026 (HD#3, POD#1)" in out and "01/02/2020." in out
    assert n == 2 and details["admit_date"] == "2026-09-30"


def test_hospital_day_replace_and_explicit_admit():
    out, _, _ = hospital_day.run("seen 2026-10-05", {"hospital_day": {
        "enabled": True, "admit_date": "2026-10-01", "style": "replace"}})
    assert out == "seen HD#5"


def test_hospital_day_without_admission_is_a_noted_noop():
    out, n, details = hospital_day.run("seen 10/05/2026", {"hospital_day": {"enabled": True}})
    assert (out, n) == ("seen 10/05/2026", 0) and "admission" in details["note"]


def test_hospital_day_is_idempotent():
    cfg = {"hospital_day": {"enabled": True, "admit_date": "2026-10-01"}}
    once = hospital_day.run("seen 10/02/2026", cfg)[0]
    assert hospital_day.run(once, cfg)[0] == once == "seen 10/02/2026 (HD#2)"


def test_stages_are_off_by_default_in_the_pipeline():
    assert clean_text(REPORT, base(), wrap=False).text.count("FINDINGS") == 1


def test_hospital_day_runs_before_timestamp_removal():
    order = [s.id for s in Pipeline(base()).stages]
    assert order.index("hospital_day") < order.index("timestamps")
    cfg = base(hospital_day={"enabled": True, "admit_date": "2026-10-01", "style": "replace"},
               timestamp_removal={"enabled": True})
    assert clean_text("Seen 10/03/2026.", cfg, wrap=False).text == "Seen HD#3."


def test_saved_orders_without_new_stages_place_them_at_their_anchor():
    old_order = [s for s in BUILTIN_STAGE_IDS if s not in ("hospital_day", "imaging_impression")]
    order = [s.id for s in Pipeline(base(stage_order=old_order)).stages]
    assert order.index("hospital_day") == order.index("unicode_normalize") + 1
    assert order.index("imaging_impression") == order.index("sections") + 1


@pytest.mark.parametrize("group", [
    {"imaging_impression": {"keep": ["everything"]}},
    {"hospital_day": {"admit_date": "yesterday"}},
    {"hospital_day": {"surgery_dates": ["10/01/2026"]}},
    {"hospital_day": {"style": "inline"}},
])
def test_invalid_options_are_rejected(group):
    errors, _ = validate_config(base(**group))
    assert errors
