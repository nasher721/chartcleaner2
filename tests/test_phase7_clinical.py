"""Phase 7 group 3: problem-oriented view, devices, scores, overnight events,
antibiotics and cultures — plus their service / API / MCP / CLI exposure."""

from __future__ import annotations

import json
import sys
from datetime import date

import pytest

from chartcleaner import chart_dates, devices, micro, overnight, problems, service
from chartcleaner.trends import build as build_trends
from chartcleaner.trends import scores_in

CHART = """Date of Service: 09/14/2026
Assessment & Plan:
1. SAH, Hunt-Hess 3, Fisher 3.
   - Nimodipine 60 mg q4h.
GCS 13. RASS -1. NIHSS 4.
Na 136.

Date of Service: 09/15/2026
Overnight events:
- Febrile to 38.6, blood cultures sent.
- Nicardipine increased for SBP 170s.

Subjective: feels ok.
0230 - rapid response for desaturation, placed on BiPAP
1400 - family meeting
EVD placed 09/12/2026, draining at 10 cm H2O.
R IJ CVC inserted on 9/10.
L radial arterial line placed 9/14.
Foley in place since 9/11.
Patient extubated 9/14.
GCS 14. RASS 0. NIHSS 2.
Na 131, transcranial Dopplers with mild vasospasm.
ID: Cefepime started 9/12 for VAP. Vancomycin day 3. Metronidazole stopped 9/14.
Blood cultures 9/12: no growth to date.
Sputum culture 9/12 growing MSSA.
Urine cx pending.

Assessment & Plan:
1. SAH, Hunt-Hess 3, Fisher 3, post-coiling day 5.
   - Nimodipine 60 mg q4h.
   - TCDs daily.
2. Hyponatremia
   - Salt tabs 2 g TID.
3. VAP
   - Cefepime, follow sputum culture.
"""


# --- dates -------------------------------------------------------------------

def test_dates_take_the_chart_year_and_reference():
    ref = chart_dates.reference_date(CHART)
    assert ref == date(2026, 9, 15)
    assert chart_dates.parse_date("9/12", ref) == date(2026, 9, 12)
    assert chart_dates.parse_date("12/30", date(2026, 1, 2)) == date(2025, 12, 30)
    assert chart_dates.parse_date("Sep 3", ref) == date(2026, 9, 3)
    assert chart_dates.day_number(date(2026, 9, 12), ref) == 4


# --- devices -------------------------------------------------------------------

def test_devices_with_days_sites_and_removal():
    rep = devices.build(CHART)
    by = {d.name: d for d in rep.devices}
    assert by["EVD"].day == 4 and by["EVD"].placed == date(2026, 9, 12)
    assert by["Central line"].site == "R IJ" and by["Central line"].day == 6
    assert by["Arterial line"].site == "L radial" and by["Arterial line"].day == 2
    assert by["Foley"].day == 5 and by["Foley"].needs_review
    assert by["Endotracheal tube"].removed
    assert "still needed?" in rep.to_text()
    assert rep.to_dict()["devices"][0]["name"] == "EVD"


def test_device_day_written_in_the_chart():
    rep = devices.build("Note 09/15/2026\nPICC line day 9, flushing well.\n")
    assert rep.devices[0].name == "PICC" and rep.devices[0].day == 9


def test_no_devices():
    assert devices.build("Patient walking in hall.").devices == []


# --- antibiotics and cultures --------------------------------------------------

def test_antibiotic_days_and_cultures():
    rep = micro.build(CHART)
    by = {a.name: a for a in rep.antibiotics}
    assert by["cefepime"].day == 4 and by["cefepime"].start == date(2026, 9, 12)
    assert by["vancomycin"].day == 3 and not by["vancomycin"].stopped
    assert by["metronidazole"].stopped
    cultures = {c.specimen: c for c in rep.cultures}
    assert cultures["Blood"].result == "no growth to date"
    assert cultures["Sputum"].organism == "MSSA"
    assert cultures["Urine"].result == "pending"
    assert "Antimicrobials" in rep.to_text()


def test_brand_names_map_to_generics():
    rep = micro.build("Note 09/15/2026\nZosyn (9/13 - ) continues. Bactrim DS BID.\n")
    names = [a.name for a in rep.antibiotics]
    assert names == ["piperacillin-tazobactam", "trimethoprim-sulfamethoxazole"]
    assert rep.antibiotics[0].day == 3


# --- overnight -----------------------------------------------------------------

def test_overnight_section_and_timed_lines():
    rep = overnight.build(CHART)
    texts = [e.text for e in rep.events]
    assert texts[:2] == ["Febrile to 38.6, blood cultures sent.", "Nicardipine increased for SBP 170s."]
    assert "rapid response for desaturation, placed on BiPAP" in texts
    assert "family meeting" not in texts       # 14:00 is not overnight
    assert rep.to_text().startswith("Overnight events")


def test_overnight_without_events():
    assert overnight.build("Progress Note\nStable.\n").empty


def test_handoff_prompt_starts_with_overnight_events():
    from chartcleaner.summarizer import summarize

    class Client:
        prompt = ""

        def is_available(self):
            return True

        def list_models(self):
            return ["m"]

        def generate(self, prompt, model="", system=None):
            Client.prompt = prompt
            return "ok"

    summarize(CHART, {"local_llm": {"prompt_preset": "handoff"}}, client=Client())
    assert "Overnight events\n- Febrile to 38.6" in Client.prompt
    summarize(CHART, {"local_llm": {"prompt_preset": "brief"}}, client=Client())
    assert "Overnight events (copied" not in Client.prompt


# --- problems ------------------------------------------------------------------

def test_problems_group_chart_lines_by_problem():
    rep = problems.build(CHART)
    titles = [p.title for p in rep.problems]
    assert titles == ["SAH", "Hyponatremia", "VAP"]
    sah, hypo, vap = rep.problems
    assert "- TCDs daily." in sah.plan
    sah_lines = [e.line for e in sah.evidence]
    assert any("vasospasm" in line for line in sah_lines)
    assert any("Na 131" in e.line for e in hypo.evidence)
    assert any("Sputum culture" in e.line for e in vap.evidence)
    # evidence is verbatim chart text and carries the note it came from
    for p in rep.problems:
        for e in p.evidence:
            assert e.line in CHART.replace("\n", " ") or e.line in CHART
    assert {e.note for e in sah.evidence} <= {"09/14/2026", "09/15/2026"}


def test_problem_from_an_earlier_note_is_kept_and_labelled():
    chart = ("Date of Service: 09/14/2026\nAssessment & Plan:\n1. Seizure\n   - Levetiracetam 500 mg BID.\n\n"
             "Date of Service: 09/15/2026\nAssessment & Plan:\n1. Hyponatremia\n   - Salt tabs.\n")
    rep = problems.build(chart)
    assert [p.title for p in rep.problems] == ["Hyponatremia", "Seizure"]
    assert rep.problems[1].note == "09/14/2026" and rep.problems[0].note == ""


def test_no_assessment_means_no_problems():
    assert problems.build("Patient stable.").empty


# --- scores --------------------------------------------------------------------

def test_scores_are_read_and_trended():
    assert scores_in("GCS 14, RASS -2, CAM-ICU positive, mRS 3, modified Fisher 4") == {
        "GCS": "14", "RASS": "-2", "CAM-ICU": "positive", "mRS": "3", "mFisher": "4"}
    rep = build_trends(CHART)
    by = {t.name: t for t in rep.scores}
    assert by["GCS"].values == ["13", "14"] and by["GCS"].direction() == "↑"
    assert by["RASS"].values == ["-1", "0"]
    assert by["NIHSS"].direction() == "↓"
    assert "Scores (" in rep.to_text()
    assert rep.to_dict()["scores"][0]["name"] == "GCS"


# --- service, API, MCP, CLI ----------------------------------------------------

def test_service_insights():
    out = service.insights(CHART)
    assert set(out) == set(service.INSIGHTS)
    assert out["devices"]["devices"][0]["name"] == "EVD"
    text = service.insights_text(CHART, ["overnight", "devices"])
    assert text.startswith("Overnight events") and "Lines / drains / airway" in text
    with pytest.raises(ValueError):
        service.insights(CHART, ["nope"])


def test_format_output_puts_insights_on_top():
    out, _ = service.format_output(CHART, insights=True)
    assert out.startswith("Overnight events") and out.endswith(CHART)


def test_api_insights(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from chartcleaner import api, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    app = FastAPI()
    api.register(app)
    client = TestClient(app, client=("127.0.0.1", 50000))
    client.headers["Authorization"] = f"Bearer {api.get_token()}"
    res = client.post("/api/v1/insights", json={"text": CHART, "which": ["micro"]})
    assert res.status_code == 200 and list(res.json()) == ["micro"]
    assert client.post("/api/v1/insights", json={"text": CHART, "which": "micro"}).status_code == 400


def test_mcp_insights_tool():
    from chartcleaner import mcp_server
    assert mcp_server.chart_insights in mcp_server.TOOLS
    out = mcp_server.chart_insights(CHART, "problems, devices")
    assert list(out) == ["problems", "devices"]


def test_cli_insights(monkeypatch, capsys, tmp_path):
    import medical_cleaner
    from chartcleaner import store
    from chartcleaner.engine import load_default_config, save_config
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    monkeypatch.setattr(medical_cleaner, "CONFIG_PATH", path, raising=False)
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(CHART))
    monkeypatch.setattr(sys, "argv", ["clean-chart", "--stdin", "--stdout", "--insights", "--no-wrap"])
    medical_cleaner.main()
    out = capsys.readouterr().out
    assert out.startswith("Overnight events") and "Antimicrobials" in out
