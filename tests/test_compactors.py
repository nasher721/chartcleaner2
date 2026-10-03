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


# ---- labs -------------------------------------------------------------------
from chartcleaner.compactors import labs, meds, vitals  # noqa: E402

LAB_TABLE = ("Lab Results (10/02/2026 05:12)\nComponent\tValue\tRef Range\n"
             "Sodium\t132\tL\t135 - 145 mmol/L\nPotassium\t4.1\t\t3.5 - 5.1 mmol/L\n"
             "Chloride\t102\t\t98 - 107 mmol/L\nCO2\t24\t\t22 - 29 mmol/L\nBUN\t18\t\t7 - 20 mg/dL\n"
             "Creatinine\t0.9\t\t0.6 - 1.2 mg/dL\nGlucose\t132\tH\t70 - 99 mg/dL\n"
             "WBC\t12.1\tH\t4.0 - 11.0 K/uL\nHemoglobin\t11.4\tL\t12.0 - 16.0 g/dL\n"
             "Hematocrit\t34.0\tL\t36 - 46 %\nPlatelets\t244\t\t150 - 400 K/uL\nPlan: recheck in AM")
ON = {"enabled": True}


def test_lab_table_becomes_panel_lines():
    out, n, _ = labs.run(LAB_TABLE, {"lab_compaction": ON})
    assert out == ("Lab Results (10/02/2026 05:12)\n"
                   "BMP: Na 132 (L), K 4.1, Cl 102, CO2 24, BUN 18, Cr 0.9, Glu 132 (H)\n"
                   "CBC: WBC 12.1 (H), Hgb 11.4 (L), Hct 34.0 (L), Plt 244\nPlan: recheck in AM")
    assert n == 1


def test_lab_fishbone_and_ranges():
    fish = labs.run(LAB_TABLE, {"lab_compaction": {**ON, "style": "fishbone"}})[0]
    assert "132 | 102 | 18  /\n" in fish and "12.1 >--< 244" in fish
    ranges = labs.run(LAB_TABLE, {"lab_compaction": {**ON, "keep_reference_ranges": True}})[0]
    assert "Na 132 (L) [135 - 145]" in ranges


GRID = "Recent Labs\nLab 10/01/26 0500 10/02/26 0430\nNA 138 135*\nK 4.1 3.4*\nCREATININE 0.9 1.1\n"


def test_lab_grid_trend_and_latest():
    assert "BMP (10/01/26 0500 → 10/02/26 0430): Na 138 → 135 (abnl)" in labs.run(GRID, {"lab_compaction": ON})[0]
    latest = labs.run(GRID, {"lab_compaction": {**ON, "latest_only": True}})[0]
    assert "BMP (10/02/26 0430): Na 135 (abnl), K 3.4 (abnl), Cr 1.1" in latest


def test_lab_blocks_with_unknown_lines_or_too_short_stay():
    odd = "Sodium\t132\nPotassium\t4.1\nWidgetase\t7\n"
    assert labs.run(odd, {"lab_compaction": ON})[0] == odd
    custom = labs.run(odd, {"lab_compaction": {**ON, "aliases": {"widgetase": "Wdg"}}})[0]
    assert custom.startswith("BMP: Na 132, K 4.1") and "Other: Wdg 7" in custom


# ---- meds -------------------------------------------------------------------
MEDS = """Current Outpatient Medications
Medication Sig Dispense Refill
• atorvastatin (LIPITOR) 40 mg tablet Take 1 tablet (40 mg total) by mouth daily. 90 tablet 3
• insulin glargine (LANTUS) 100 unit/mL injection Inject 10 Units subcutaneously at bedtime.
• acetaminophen (TYLENOL) 500 mg tablet Take 2 tablets (1,000 mg total) by mouth every 6 hours as needed for pain. Not Taking
• aspirin 81 mg tablet Take 1 tablet by mouth daily. [Held]

Allergies:
Penicillin - rash
"""


def test_med_lines_are_reduced_to_drug_dose_route_frequency():
    out = meds.run(MEDS, {"med_normalize": ON})[0]
    assert out == ("Current Outpatient Medications\n"
                   "• atorvastatin 40 mg PO daily\n"
                   "• insulin glargine 100 unit/mL injection 10 Units subcut at bedtime\n"
                   "• acetaminophen 1,000 mg PO q6h PRN pain\n"
                   "• aspirin 81 mg PO daily (held)\n\n"
                   "Allergies:\nPenicillin - rash\n")


def test_meds_never_lose_a_dose_or_touch_other_sections():
    out = meds.run(MEDS, {"med_normalize": ON})[0]
    assert "10 Units" in out and "Penicillin - rash" in out
    plain = "Plan: take 1 tablet by mouth daily of something\n"
    assert meds.run(plain, {"med_normalize": ON})[0] == plain


def test_med_maps_cannot_produce_do_not_use():
    errors, _ = validate_config(base(med_normalize={"enabled": True, "frequency_map": {"daily": "QD"}}))
    assert any("Do Not Use" in e for e in errors)
    assert not any(v in ("QD", "qd", "SC", "SQ", "HS") for v in {**meds.ROUTES, **meds.FREQUENCIES}.values())


# ---- vitals -----------------------------------------------------------------
FLOWSHEET = """ 10/02/26 0400 10/02/26 0800 10/02/26 1200
BP: 132/78 141/82 118/70
Pulse: 88 104 92
Temp: 37.1 °C (98.8 °F) 38.4 °C (101.1 °F) 37.6 °C (99.7 °F)
SpO2: 97 % 94 % 96 %
Intake 2,450 ml
Output 1,800 ml
Net 650 ml
Vitals: Temp 36.8 C, HR 72, BP 138/76, RR 16, SpO2 98% on room air."""


def test_vitals_flowsheet_and_io_summaries():
    out, n, _ = vitals.run(FLOWSHEET, {"vitals_summary": ON})
    assert out.splitlines() == [
        "Vitals (3 readings): BP 118/70–141/82 (last 118/70); HR 88–104 (last 92); "
        "T 37.1–38.4 °C (last 37.6); SpO2 94–97% (last 96)",
        "I/O: 2,450 in / 1,800 out (net +650 mL)",
        "Vitals: Temp 36.8 C, HR 72, BP 138/76, RR 16, SpO2 98% on room air.",
    ]
    assert n == 2


def test_vitals_rows_with_text_are_left_alone():
    text = "Pulse: 88 104 irregular\nBP: 132/78 141/82\n"
    assert vitals.run(text, {"vitals_summary": ON})[0] == text


def test_new_stages_follow_anchors_and_are_off_by_default():
    order = [s.id for s in Pipeline(base()).stages]
    assert order.index("sections") < order.index("imaging_impression") < order.index("lab_compaction") \
        < order.index("med_normalize") < order.index("vitals_summary") < order.index("whitespace")
    old = [s for s in BUILTIN_STAGE_IDS if s not in ("lab_compaction", "med_normalize", "vitals_summary")]
    order = [s.id for s in Pipeline(base(stage_order=old)).stages]
    assert order.index("vitals_summary") == order.index("lab_compaction") + 2
    assert clean_text(LAB_TABLE + "\n" + MEDS, base(), wrap=False).text.count("Sodium") == 1


def test_grids_still_parse_after_hospital_day_labels():
    cfg = base(hospital_day={"enabled": True, "admit_date": "2026-10-01"},
               lab_compaction={"enabled": True}, vitals_summary={"enabled": True})
    out = clean_text(GRID + "\n" + FLOWSHEET, cfg, wrap=False).text
    assert "0430\n" not in out and "1200\n" not in out  # date header rows were consumed
    assert "BMP (10/01/26 (HD#1) 0500 → 10/02/26 (HD#2) 0430)" in out
    assert out.count("Vitals (3 readings)") == 1
