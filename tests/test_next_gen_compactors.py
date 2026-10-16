"""Tests for next-gen compactors: mar_summary, flowsheet_tables, and note_order."""

from __future__ import annotations

import pytest

from chartcleaner.compactors import mar, note_order, tables
from chartcleaner.engine import BUILTIN_STAGE_IDS, Pipeline, clean_text, load_default_config, validate_config

ON = {"enabled": True}


# ---------------------------------------------------------------------------
# 1. MAR & PRN administration condenser (stage ``mar_summary``)
# ---------------------------------------------------------------------------

MAR_BLOCK = """Medications Given (Last 24 Hours):
10/02 01:15  fentanyl 25 mcg IV Push Given
10/02 03:40  fentanyl 25 mcg IV Push Given
10/02 06:10  fentanyl 50 mcg IV Push Given
10/02 04:00  labetalol 10 mg IV Push Given
Plan: continue multimodal analgesia."""

MAR_EQUAL_AND_HELD = """MAR:
01:00 hydromorphone 0.5 mg IV PRN Given
03:30 hydromorphone 0.5 mg IV PRN Given
06:00 hydromorphone 0.5 mg IV PRN Held
08:15 hydromorphone 0.5 mg IV PRN Given"""


def test_mar_condenses_repeated_doses_and_keeps_singletons():
    out, n, details = mar.run(MAR_BLOCK, {"mar_summary": ON})
    assert n == 1 and details["doses_condensed"] == 3
    assert out.splitlines() == [
        "Medications Given (Last 24 Hours):",
        "Fentanyl IV: 100 mcg total over 3 doses (25, 25, 50 mcg; 10/02 01:15–10/02 06:10; last 50 mcg at 10/02 06:10)",
        "10/02 04:00  labetalol 10 mg IV Push Given",
        "Plan: continue multimodal analgesia.",
    ]


def test_mar_equal_doses_and_held_count():
    out, n, _ = mar.run(MAR_EQUAL_AND_HELD, {"mar_summary": ON})
    assert n == 1
    assert out.splitlines() == [
        "MAR:",
        "Hydromorphone IV: 1.5 mg total over 3 doses (3 × 0.5 mg; 01:00–08:15; last 0.5 mg at 08:15; 1 held)",
    ]


def test_mar_drug_first_and_inline_multi_formats():
    drug_first = (
        "lorazepam 1 mg IV Push at 02:00 Given\n"
        "lorazepam 2 mg IV Push at 05:30 Given\n"
    )
    out, n, _ = mar.run(drug_first, {"mar_summary": ON})
    assert n == 1
    assert "Lorazepam IV: 3 mg total over 2 doses (1, 2 mg; 02:00–05:30; last 2 mg at 05:30)" in out

    inline = "fentanyl 25 mcg IV PRN: 01:15 (25 mcg), 04:45 (25 mcg)"
    out_inline, n_inline, _ = mar.run(inline, {"mar_summary": ON})
    assert n_inline == 1
    assert out_inline == "Fentanyl IV: 50 mcg total over 2 doses (2 × 25 mcg; 01:15–04:45; last 25 mcg at 04:45)"


def test_mar_include_scheduled_false_leaves_scheduled_alone():
    mixed = (
        "08:00 acetaminophen 650 mg PO Scheduled Given\n"
        "14:00 acetaminophen 650 mg PO Scheduled Given\n"
        "02:00 fentanyl 25 mcg IV PRN Given\n"
        "05:00 fentanyl 25 mcg IV PRN Given"
    )
    out, _, _ = mar.run(mixed, {"mar_summary": {**ON, "include_scheduled": False}})
    assert "08:00 acetaminophen" in out and "14:00 acetaminophen" in out
    assert "Fentanyl IV: 50 mcg total over 2 doses" in out


def test_mar_unparsed_line_in_block_leaves_whole_block_alone():
    bad = (
        "10/02 01:15 fentanyl 25 mcg IV Push Given\n"
        "10/02 03:40 fentanyl 25 mcg patient became Somnolent after push\n"
        "10/02 06:10 fentanyl 25 mcg IV Push Given"
    )
    assert mar.run(bad, {"mar_summary": ON})[0] == bad


# ---------------------------------------------------------------------------
# 2. Flowsheet table de-tabularizer (stage ``flowsheet_tables``)
# ---------------------------------------------------------------------------

VENT_GRID = """Ventilator\t10/02 0200\t10/02 0600\t10/02 1000
Mode\tAC/VC\tAC/VC\tPSV
FiO2\t60%\t40%\t35%
PEEP\t8\t5\t5
Vt\t450\t460\t480
Plan: SBT this afternoon."""

ABG_TRANSPOSED = """Time\tpH\tpCO2\tpO2\tHCO3
10/02 0200\t7.31\t48\t88\t24
10/02 0600\t7.38\t40\t95\t24"""


def test_flowsheet_time_across_columns_series_and_range():
    out, n, _ = tables.run(VENT_GRID, {"flowsheet_tables": ON})
    assert n == 1
    assert out.splitlines() == [
        "Ventilator (10/02 0200 → 10/02 1000, 3 readings): "
        "Mode AC/VC → AC/VC → PSV (last PSV); "
        "FiO2 60% → 40% → 35% (last 35%); "
        "PEEP 8 → 5 → 5 (last 5); "
        "Vt 450 → 460 → 480 (last 480)",
        "Plan: SBT this afternoon.",
    ]

    ranged, _, _ = tables.run(VENT_GRID, {"flowsheet_tables": {**ON, "style": "range"}})
    assert "FiO2 35–60% (last 35%)" in ranged
    assert "Vt 450–480 (last 480)" in ranged


def test_flowsheet_time_down_rows_transposed_and_unchanged():
    out, n, _ = tables.run(ABG_TRANSPOSED, {"flowsheet_tables": ON})
    assert n == 1
    assert out == (
        "Flowsheet (10/02 0200 → 10/02 0600, 2 readings): "
        "pH 7.31 → 7.38 (last 7.38); pCO2 48 → 40 (last 40); "
        "pO2 88 → 95 (last 95); HCO3 24 (unchanged)"
    )


def test_flowsheet_malformed_row_aborts_whole_table():
    bad = (
        "Ventilator\t10/02 0200\t10/02 0600\n"
        "Mode\tAC/VC\tPSV\n"
        "FiO2\t60%\tpatient desaturated on room air during suctioning\n"
    )
    out, n, details = tables.run(bad, {"flowsheet_tables": ON})
    assert n == 0 and out == bad and details.get("skipped") == 1


# ---------------------------------------------------------------------------
# 3. Chronological note ordering (stage ``note_order``)
# ---------------------------------------------------------------------------

REVERSE_NOTES = """Admission Date: 09/30/2026
Patient: [REDACTED_NAME]

Progress Notes by Jane Doe, MD on 10/03/2026 09:00
Assessment: improving on HD#4.

Progress Notes by Jane Doe, MD on 10/02/2026 16:30
Assessment: EVD weaned.

Progress Notes by Jane Doe, MD on 10/02/2026 08:00
Assessment: stable morning exam.

Progress Notes by Jane Doe, MD on 10/01/2026 08:30
Assessment: admitted with SAH.
"""


def test_note_order_oldest_first_preserves_preamble_and_intra_day_times():
    out, n, details = note_order.run(REVERSE_NOTES, {"note_order": ON})
    assert n == 4 and details["reordered"] is True
    assert out.startswith("Admission Date: 09/30/2026\nPatient: [REDACTED_NAME]\n\n")
    idx_oct1 = out.index("10/01/2026 08:30")
    idx_oct2_am = out.index("10/02/2026 08:00")
    idx_oct2_pm = out.index("10/02/2026 16:30")
    idx_oct3 = out.index("10/03/2026 09:00")
    assert idx_oct1 < idx_oct2_am < idx_oct2_pm < idx_oct3

    # Already oldest_first -> no-op
    out2, n2, details2 = note_order.run(out, {"note_order": ON})
    assert n2 == 0 and out2 == out and details2["reordered"] is False

    # Flip back to newest_first
    back, n3, _ = note_order.run(out, {"note_order": {**ON, "direction": "newest_first"}})
    assert n3 == 4
    assert back.index("10/03/2026") < back.index("10/02/2026 16:30") < back.index("10/01/2026")


def test_note_order_skips_when_any_note_header_lacks_date():
    undated = (
        "Progress Notes by Jane Doe, MD on 10/03/2026\nDay 3.\n\n"
        "Progress Notes by John Smith, MD\nUndated note.\n\n"
        "Progress Notes by Jane Doe, MD on 10/01/2026\nDay 1.\n"
    )
    out, n, details = note_order.run(undated, {"note_order": ON})
    assert n == 0 and out == undated and details.get("skipped") == "undated_note"


# ---------------------------------------------------------------------------
# 4. Pipeline integration, anchors, validator, and fact_check
# ---------------------------------------------------------------------------

def test_new_compactors_anchor_order_and_fact_check():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    order = [s.id for s in Pipeline(cfg).stages]
    assert order.index("mar_summary") == order.index("neuro_summary") + 1
    assert order.index("flowsheet_tables") == order.index("mar_summary") + 1
    assert order.index("note_order") == order.index("duplicate_notes") + 1

    # Saved stage_order missing the new stages places them at their anchors
    legacy = [s for s in BUILTIN_STAGE_IDS if s not in ("mar_summary", "flowsheet_tables", "note_order")]
    cfg["stage_order"] = legacy
    resolved = [s.id for s in Pipeline(cfg).stages]
    assert resolved.index("mar_summary") == resolved.index("neuro_summary") + 1
    assert resolved.index("flowsheet_tables") == resolved.index("mar_summary") + 1
    assert resolved.index("note_order") == resolved.index("duplicate_notes") + 1

    # Full pipeline run with all 3 enabled passes fact_check with status == "ok"
    cfg["mar_summary"] = {"enabled": True}
    cfg["flowsheet_tables"] = {"enabled": True}
    cfg["note_order"] = {"enabled": True}
    combined = f"{REVERSE_NOTES}\n{MAR_BLOCK}\n\n{VENT_GRID}"
    res = clean_text(combined, cfg, wrap=False)
    assert "Fentanyl IV: 100 mcg total over 3 doses" in res.text
    assert "(10/02 0200 → 10/02 1000, 3 readings): Mode AC/VC → AC/VC → PSV (last PSV)" in res.text
    assert res.text.index("10/01/2026") < res.text.index("10/03/2026")
    assert res.fact_check.status == "ok"


@pytest.mark.parametrize("bad_cfg,expected", [
    ({"mar_summary": {"enabled": True, "min_doses": 1}}, "mar_summary.min_doses"),
    ({"mar_summary": {"enabled": True, "include_scheduled": "yes"}}, "mar_summary.include_scheduled"),
    ({"flowsheet_tables": {"enabled": True, "style": "grid"}}, "flowsheet_tables.style"),
    ({"note_order": {"enabled": True, "direction": "random"}}, "note_order.direction"),
])
def test_validator_rejects_invalid_next_gen_compactor_options(bad_cfg, expected):
    cfg = load_default_config()
    cfg.update(bad_cfg)
    errors, _ = validate_config(cfg)
    assert any(expected in e for e in errors), f"Expected {expected} in {errors}"


async def test_pipeline_page_lists_next_gen_compactors(user):
    await user.open("/pipeline")
    await user.should_see("MAR & PRN dose summary")
    await user.should_see("Flowsheet table de-tabularizer")
    await user.should_see("Chronological note ordering")
