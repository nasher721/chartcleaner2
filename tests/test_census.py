"""Census: one card per bed tag from the encrypted recent charts — the module,
service, CLI and the /census page."""

from __future__ import annotations

import asyncio
import sys

import pytest

from chartcleaner import census, micro, recent_charts, service
from chartcleaner.engine import load_default_config
from tests.test_conflicts import CHART as CONFLICT_CHART

QUIET = """Date of Service: 10/12/2026
Allergies: NKDA
Code status: Full code
Plan:
- Follow up blood cx.
"""


def _cfg() -> dict:
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    return cfg


def _as_is(text: str, cfg: dict) -> tuple[str, list[str]]:
    return text, []


def test_newest_chart_per_tag_and_busiest_bed_first():
    records = [  # newest first, as recent_charts.load() returns them
        {"tag": "B2", "ts": "2026-10-12T07:30:00", "text": QUIET},
        {"tag": "B1", "ts": "2026-10-12T07:00:00", "text": CONFLICT_CHART},
        {"tag": "b2", "ts": "2026-10-11T07:00:00", "text": CONFLICT_CHART},  # older, same bed
        {"ts": "2026-10-12T06:00:00", "text": CONFLICT_CHART},               # untagged: skipped
    ]
    report = census.build(_cfg(), records=records, clean=_as_is)
    assert [b.tag for b in report.beds] == ["B1", "B2"]
    busy, quiet = report.beds
    assert busy.conflicts and busy.conflicts[0].startswith("side — weakness")
    assert busy.allergies == "Penicillin (hives)" and busy.code_status == "DNR/DNI"  # the latest line
    assert any(f.startswith("Code status: changed") for f in busy.safety_flags)
    assert "piperacillin-tazobactam" in " ".join(busy.antibiotics)
    assert not any("penicillin" in a for a in busy.antibiotics)  # the allergy is not an antibiotic
    assert quiet.updated == "10/12 07:30" and quiet.conflicts == [] and quiet.allergies == "NKDA"
    assert quiet.todo == ["Follow up blood cx."] and quiet.todo_total == 1
    text = report.to_text()
    assert text.index("B1") < text.index("B2") and "! contradiction: side" in text
    assert report.to_dict()["beds"][0]["attention"] == busy.attention


def test_one_bed_failing_never_hides_the_others():
    def boom(text, cfg):
        if "Penicillin" in text:
            raise RuntimeError("bad chart")
        return text, []
    report = census.build(_cfg(), clean=boom,
                          records=[{"tag": "B1", "text": CONFLICT_CHART}, {"tag": "B2", "text": QUIET}])
    bad = next(b for b in report.beds if b.tag == "B1")
    assert bad.error == "RuntimeError: bad chart" and "could not read" in bad.to_text()
    assert next(b for b in report.beds if b.tag == "B2").allergies == "NKDA"


def test_removed_code_status_is_flagged():
    report = census.build(_cfg(), records=[{"tag": "B1", "text": QUIET}],
                          clean=lambda t, c: (t.replace("Code status: Full code\n", ""),
                                              ["Code status: Full code"]))
    assert report.beds[0].safety_flags == ["Code status: removed by a cleaning rule"]


def test_no_tagged_charts():
    assert census.build(_cfg(), records=[{"text": QUIET}]).empty
    assert census.build(_cfg(), records=[]).to_text() == ""


def test_micro_skips_allergy_lines():
    assert [a.name for a in micro.build("ALLG: Penicillin (hives)\nVancomycin day 3").antibiotics] \
        == ["vancomycin"]
    assert [a.name for a in micro.build("Allergies\nCeftriaxone (rash)\n\nCefepime 2 g IV q8h").antibiotics] \
        == ["cefepime"]


def test_service_reads_stored_charts_with_the_real_pipeline():
    recent_charts.remember(QUIET, "test", {}, tag="G20-1")
    data = service.census(config=_cfg())
    assert [b["tag"] for b in data["beds"]] == ["G20-1"]
    assert data["beds"][0]["allergies"] == "NKDA"


def test_cli_census(monkeypatch, capsys, tmp_path):
    import medical_cleaner
    from chartcleaner import store
    from chartcleaner.engine import save_config
    path = tmp_path / "config.json"
    save_config(_cfg(), path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    monkeypatch.setattr(sys, "argv", ["clean-chart", "--census"])
    medical_cleaner.main()
    assert "No bed-tagged charts yet" in capsys.readouterr().out
    recent_charts.remember(QUIET, "test", {}, tag="G20-1")
    medical_cleaner.main()
    assert capsys.readouterr().out.startswith("G20-1")
