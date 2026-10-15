"""Insights must survive abbreviated output (Phase 12 T3).

Every insight reads the *cleaned* text, so its patterns must also match the
abbreviated forms (ALLG, Hep gtt, tmrw, BCx, POC Glu …). For each insight
module, the report on abbreviated output must contain everything the report
on unabbreviated output has — no insight lost to abbreviation. Verbatim
wording may differ (quotes are verbatim by design); the comparisons below
are semantic, with the verbatim delta documented per module.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from chartcleaner import (
    bundle,
    conflicts,
    devices,
    micro,
    overnight,
    pending,
    problems,
    safety,
    stale,
)
from chartcleaner.engine import Pipeline, load_default_config

ROOT = Path(__file__).resolve().parent.parent

SYNTHETIC = """Date of Service: 09/15/2026
Code status: full code
Allergies: Penicillin (rash)
Plan:
- Extubate tomorrow if SBT passes.
- Repeat CT in AM.
- Blood cultures pending.
- Will obtain MRI tomorrow.
- Consult nephrology to see tomorrow.
- If sodium less than 130 start salt tabs.
- Follow up chest xray.
- Consider dialysis.
- NPO.
- Tolerating regular diet.
- Afebrile. Temp 101.2 F.
- Heparin gtt per nomogram.
- POC Glucose 108.
- Acetaminophen 650 mg q6h.
"""


@pytest.fixture
def pair():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    off_cfg = deepcopy(cfg)
    off_cfg.setdefault("stage_options", {})["medical_abbreviations"] = {"enabled": False}
    chart = (ROOT / "sample_chart.txt").read_text()
    on = Pipeline(cfg).run(chart, wrap=False).text
    off = Pipeline(off_cfg).run(chart, wrap=False).text
    assert on != off  # the fixture must actually exercise abbreviation
    return on, off


@pytest.fixture
def synth_pair():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    off_cfg = deepcopy(cfg)
    off_cfg.setdefault("stage_options", {})["medical_abbreviations"] = {"enabled": False}
    on = Pipeline(cfg).run(SYNTHETIC, wrap=False).text
    off = Pipeline(off_cfg).run(SYNTHETIC, wrap=False).text
    return on, off


def test_safety_statuses_survive_abbreviation(pair, synth_pair):
    for on, off in (pair, synth_pair):
        want = {i.name: i.status for i in safety.build(off).items}
        got = {i.name: i.status for i in safety.build(on).items}
        assert got == want  # verbatim lines differ (ALLG/Hep gtt); statuses must not


def test_stale_findings_survive_abbreviation(pair):
    on, off = pair
    # (notes-in-a-row, since): the verbatim line/word may be abbreviated.
    want = sorted((s.notes, s.since) for s in stale.build(off).lines)
    got = sorted((s.notes, s.since) for s in stale.build(on).lines)
    assert got == want


def test_conflict_kinds_survive_abbreviation(pair, synth_pair):
    for on, off in (pair, synth_pair):
        want = sorted((c.kind, c.why) for c in conflicts.build(off).conflicts)
        got = sorted((c.kind, c.why) for c in conflicts.build(on).conflicts)
        assert got == want


def test_bundle_statuses_survive_abbreviation(pair, synth_pair):
    for on, off in (pair, synth_pair):
        want = {i.name: i.status for i in bundle.build(off).items}
        got = {i.name: i.status for i in bundle.build(on).items}
        assert got == want  # e.g. VTE via "Hep gtt", Glucose via "POC Glu"


def test_pending_groups_survive_abbreviation(pair, synth_pair):
    for on, off in (pair, synth_pair):
        want = sorted(i.group for i in pending.build(off).items)
        got = sorted(i.group for i in pending.build(on).items)
        assert got == want  # e.g. "tmrw" still Planned, "Rpt" still Follow up


def test_micro_findings_survive_abbreviation(synth_pair):
    on, off = synth_pair
    want = sorted((c.specimen, c.result, c.organism) for c in micro.build(off).cultures)
    got = sorted((c.specimen, c.result, c.organism) for c in micro.build(on).cultures)
    assert got == want  # "BCx pending" reads as a Blood culture
    want_abx = sorted((a.name, a.day, a.stopped) for a in micro.build(off).antibiotics)
    got_abx = sorted((a.name, a.day, a.stopped) for a in micro.build(on).antibiotics)
    assert got_abx == want_abx


def test_problem_structure_survives_abbreviation(pair):
    on, off = pair
    # Titles quote the chart verbatim ("reg" vs "regimen", "PPx" vs
    # "Prophylaxis") so only the problem count is asserted exactly; no
    # problem may be lost and evidence must not shrink.
    rep_on, rep_off = problems.build(on), problems.build(off)
    assert len(rep_on.problems) == len(rep_off.problems)
    for p_on, p_off in zip(rep_on.problems, rep_off.problems):
        assert len(p_on.evidence) >= len(p_off.evidence)


def test_devices_and_overnight_unaffected_by_abbreviation(pair, synth_pair):
    for on, off in (pair, synth_pair):
        want = [(d.name, d.day) for d in devices.build(off).devices]
        got = [(d.name, d.day) for d in devices.build(on).devices]
        assert got == want
        assert overnight.build(on).to_text() == overnight.build(off).to_text()
