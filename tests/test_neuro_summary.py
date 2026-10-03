"""Neuro ICU condensers (stage neuro_summary, chartcleaner/compactors/neuro.py)."""

from __future__ import annotations

from chartcleaner.compactors import neuro
from chartcleaner.engine import BUILTIN_STAGE_IDS, Pipeline, clean_text, load_default_config, validate_config

ON = {"enabled": True}


def run(text: str, **opts) -> str:
    return neuro.run(text, {"neuro_summary": {**ON, **opts}})[0]


CHECKS = """Neuro checks
10/02 0800  10/02 1200  10/02 1600  10/02 2000
GCS: 14 15 15 13
GCS Motor: 6 6 6 5
RASS: 0 -1 0 -2
Pupils: 3 brisk  3 brisk  3 sluggish  3 sluggish
CAM-ICU: neg neg pos pos
Plan: repeat CT."""

EVD = """EVD at 10 cm H2O, open
0800 EVD output 10 mL
0900 EVD output 12 mL
1000 EVD output 8 mL
1100 EVD output 15 mL
ICP: 8 12 15 22"""

SODIUM = """Sodium checks:
0400 141
1000 143
1600 145
Na goal 145-155."""

DRIP = """0600 niCARdipine 5 mg/hr Rate Change
0700 niCARdipine 7.5 mg/hr Rate Change
0800 niCARdipine 10 mg/hr Rate Change
0930 niCARdipine 7.5 mg/hr Rate Verify"""


def test_neuro_checks_flowsheet():
    assert run(CHECKS).splitlines() == [
        "Neuro checks (4 readings): GCS 13–15 (last 13); GCS-M 5–6 (last 5); RASS -2–0 (last -2); "
        "Pupils 3 brisk → 3 sluggish (last 3 sluggish); CAM-ICU neg → pos (last pos)",
        "Plan: repeat CT.",
    ]


def test_unchanged_pupils_and_single_readings():
    assert run("Pupils: 3 brisk  3 brisk  3 brisk") == "Neuro checks (3 readings): Pupils 3 brisk (unchanged)"
    assert run("GCS 15 this morning.\nGCS: 15") == "GCS 15 this morning.\nGCS: 15"


def test_evd_block_with_icp_threshold():
    assert run(EVD) == ("EVD: 10 cm H2O, open; output 45 mL 0800–1100 over 4 readings (10, 12, 8, 15); "
                        "ICP 8–22 (last 22; 1 reading >20)")
    assert run(EVD, icp_threshold=12).endswith("ICP 8–22 (last 22; 2 readings >12)")
    assert run("EVD output: 10 12 8") == "EVD: output 30 mL over 3 readings (10, 12, 8)"


def test_serial_sodium():
    assert run(SODIUM) == "Na checks 0400–1600: 141 → 143 → 145 (3 checks, +4)\nNa goal 145-155."
    # bare numbers without a sodium header are not sodium
    assert run("0400 141\n1000 143\n1600 145") == "0400 141\n1000 143\n1600 145"
    assert run("Na 150 at 0200\nNa 148 at 0800\nNa 146 at 1400").startswith(
        "Na checks 0200–1400: 150 → 148 → 146 (3 checks, -4)")


def test_drip_titration():
    assert run(DRIP) == "Nicardipine drip 0600–0930: 5–10 mg/hr over 4 entries (start 5, last 7.5 mg/hr)"
    stopped = DRIP + "\n1000 niCARdipine 0 mg/hr Stopped"
    assert run(stopped).endswith("(start 5, last 0 mg/hr; stopped)")


def test_mixed_or_unparsed_blocks_stay():
    two = "0600 nicardipine 5 mg/hr\n0700 norepinephrine 4 mcg/min\n0800 nicardipine 7 mg/hr"
    assert run(two) == two
    odd = "GCS: 14 15 confused\nRASS: 0 -1"
    assert run(odd) == "GCS: 14 15 confused\nRASS: 0 -1"


def test_parts_can_be_switched_off():
    text = SODIUM + "\n" + DRIP
    out = run(text, sodium=False)
    assert out.startswith("Sodium checks:") and "Nicardipine drip" in out
    assert neuro.run(text, {"neuro_summary": {"enabled": False}})[0] == text


def test_stage_is_registered_off_by_default_and_anchored():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    order = [s.id for s in Pipeline(cfg).stages]
    assert order.index("neuro_summary") == order.index("vitals_summary") + 1
    assert clean_text(DRIP, cfg, wrap=False).text.count("niCARdipine") == 4  # off by default
    old = [s for s in BUILTIN_STAGE_IDS if s != "neuro_summary"]
    cfg["stage_order"] = old
    order = [s.id for s in Pipeline(cfg).stages]
    assert order.index("neuro_summary") == order.index("vitals_summary") + 1


def test_in_pipeline_with_fact_check():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["neuro_summary"] = {"enabled": True}
    result = clean_text(EVD, cfg, wrap=False)
    assert result.text.startswith("EVD: 10 cm H2O")
    assert result.fact_check.status == "ok"  # summaries count as by design


def test_validator():
    cfg = load_default_config()
    cfg["neuro_summary"] = {"enabled": True, "icp_threshold": "high", "evd": 1}
    errors, _ = validate_config(cfg)
    assert any("icp_threshold" in e for e in errors) and any("neuro_summary.evd" in e for e in errors)


async def test_pipeline_page_lists_the_stage(user):
    await user.open("/pipeline")
    await user.should_see("Neuro ICU summaries")
