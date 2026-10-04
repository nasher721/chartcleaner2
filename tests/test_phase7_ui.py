"""Phase 7 UI: insights blocks, note templates, daily note, editable result,
AI fact marks and citations, patient lists, Doctor, Settings and Statistics cards."""

from __future__ import annotations

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import clean as clean_mod
from app_pages import common
from chartcleaner import edit_log, recent_charts, store
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
from chartcleaner.citations import cite
from chartcleaner.engine import load_default_config, save_config
from chartcleaner.fact_check import verify_output
from chartcleaner.local_llm import GroundingResult
from chartcleaner.summarizer import SummaryResult

pytestmark = pytest.mark.nicegui_main_file("")

CHART = """Date of Service: 09/15/2026
Overnight events:
- Febrile to 38.6, blood cultures sent.

EVD placed 09/12/2026, draining at 10 cm H2O.
Foley in place since 9/11.
Cefepime started 9/12 for VAP.
Blood cultures 9/12: no growth to date.
GCS 14. Na 131.

Assessment & Plan:
1. SAH, Hunt-Hess 3.
   - Nimodipine 60 mg q4h.
2. Hyponatremia
   - Salt tabs 2 g TID.
"""


@pytest.fixture()
def page(monkeypatch, tmp_path):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "append_run", lambda _r: None)
    monkeypatch.setitem(common.PREFS, "auto_clean", False)
    monkeypatch.setitem(common.PREFS, "onboarded", True)
    monkeypatch.setattr(cc_app.LocalLlmClient, "is_available", lambda self: False)
    monkeypatch.setattr(cc_app.LocalLlmClient, "list_models", lambda self: [])
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    yield path
    CLEAN_STATE.clear()
    CLEAN_STATE.update(before)
    AUTO_LAST.clear()
    AUTO_LAST.update(auto_before)


async def _open(user: User, route: str, text: str) -> None:
    CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")

    @ui.page(route)
    async def fixture_page():
        await cc_app.clean_page()

    await user.open(route)


async def _route(user: User, route: str, page_fn) -> None:
    @ui.page(route)
    async def fixture_page():
        result = page_fn()
        if asyncio.iscoroutine(result):
            await result

    await user.open(route)


async def _clean(user: User) -> None:
    result = CLEAN_STATE.get("result")
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None and CLEAN_STATE.get("result") is not result:
            break
        await asyncio.sleep(0.02)
    assert CLEAN_STATE["result"] is not None


async def test_insights_show_devices_micro_overnight_and_problems(user: User, page):
    await _open(user, "/p7-insights", CHART)
    await _clean(user)
    for marker in ("insight-overnight", "insight-devices", "insight-micro", "insight-problems"):
        await user.should_see(marker=marker)
    await user.should_see("Lines, drains & airway")
    await user.should_see("By problem (2)")
    assert CLEAN_STATE["devices"].devices[0].name == "EVD"
    assert [a.name for a in CLEAN_STATE["micro"].antibiotics] == ["cefepime"]


async def test_note_template_copy_and_bed_tag(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda text, note="": copied.append(text))
    await _open(user, "/p7-note", CHART)
    with user.client:
        next(iter(user.find(marker="bed-tag").elements)).set_value("G20-1")
    await _clean(user)
    assert recent_charts.load()[0]["tag"] == "G20-1"
    user.find(marker="copy-as").click()
    user.find(marker="copy-note-0").click()
    assert copied and "[N]" in copied[-1]


async def test_daily_note_dialog_compares_with_previous(user: User, page):
    previous = CHART.replace("09/15/2026", "09/14/2026").replace("Na 131", "Na 136")
    recent_charts.remember(previous, "test", {}, tag="G20-1")
    await _open(user, "/p7-daily", CHART)
    await _clean(user)
    user.find(marker="daily-note").click()
    await user.should_see(marker="daily-note-dialog")
    user.find(marker="daily-note-build").click()
    for _ in range(200):
        value = next(iter(user.find(marker="daily-note-output").elements)).value
        if value:
            break
        await asyncio.sleep(0.02)
    assert value.startswith("Daily update") and "Na: 136 → 131" in value


async def test_editing_the_result_is_kept_and_learned(user: User, page):
    text = CHART + "Routed to inbox 4471 by system\n"
    await _open(user, "/p7-edit", text)
    await _clean(user)
    user.find(marker="edit-result").click()
    out = next(iter(user.find(marker="result-output").elements))
    edited = "\n".join(ln for ln in out.value.split("\n") if "Routed to inbox" not in ln)
    with user.client:
        out.set_value(edited)
    user.find(marker="result-output").trigger("blur")
    assert CLEAN_STATE["result_text"] == edited
    assert any("inbox" in e["sample"] for e in edit_log.entries().values())


async def test_ai_summary_marks_unsupported_values_and_cites(user: User, page, monkeypatch):
    await _open(user, "/p7-ai", CHART)
    await _clean(user)
    chart_text = CLEAN_STATE["result_text"]
    text = "Nimodipine 60 mg q4h.\nNimodipine 90 mg q4h."

    def fake(chart, cfg, **kw):
        return SummaryResult(text=text, grounding=GroundingResult(50.0, False, [], 0), model="fake",
                             preset="clinical", duration_ms=1, facts=verify_output(chart, text),
                             citations=cite(text, chart))

    monkeypatch.setattr(clean_mod, "summarize", fake)
    user.find("Summarize").click()
    await user.should_see(marker="ai-facts-unsupported", retries=50)
    await user.should_see(marker="ai-summary-citations")
    assert CLEAN_STATE["summary"].facts.unsupported[0].display == "90 mg"
    assert chart_text  # citations index the cleaned chart shown in the output


async def test_fact_panel_lists_meaning_changes(user: User, page):
    from chartcleaner.engine import load_config
    cfg = load_config(page)
    cfg["literal_replacements"] = [[r"\bdenies ", ""]]
    save_config(cfg, page)
    await _open(user, "/p7-meaning", "Patient denies chest pain at rest today.\n")
    await _clean(user)
    assert CLEAN_STATE["result"].fact_check.meaning
    await user.should_see(marker="meaning-change")


async def test_batch_patient_list(user: User, page):
    await _route(user, "/p7-batch", cc_app.batch_page)
    with user.client:
        next(iter(user.find(marker="patient-list-input").elements)).set_value(
            "G20-1 65M SAH, EVD day 4.\nG20-2 72F ICH, SBP goal <140.\n")
    await user.should_see("2 patient(s): G20-1, G20-2")
    user.find(marker="patient-list-run").click()
    await user.should_see("Processed 2 item(s)", retries=100)


async def test_doctor_page(user: User, page):
    await _route(user, "/p7-doctor", cc_app.doctor_page)
    await user.should_see(marker="doctor-summary")
    await user.should_see(marker="doctor-python", retries=100)
    await user.should_see(marker="doctor-config")


async def test_settings_cards(user: User, page):
    await _route(user, "/p7-settings", cc_app.settings_page)
    await user.should_see(marker="local-ai-card")
    await user.should_see(marker="note-templates")
    await user.should_see(marker="check-ai")


async def test_stats_stage_noise(user: User, page, monkeypatch):
    runs = [{"ts": "2026-10-01T09:00:00", "source": "t", "chars_before": 100, "chars_after": 60,
             "stages": [{"label": "Boilerplate blocks", "chars_before": 100, "chars_after": 60}]},
            {"ts": "2026-10-02T09:00:00", "source": "t", "chars_before": 80, "chars_after": 70,
             "stages": [{"label": "Boilerplate blocks", "chars_before": 80, "chars_after": 70}]}]
    monkeypatch.setattr(store, "load_runs", lambda: runs)
    await _route(user, "/p7-stats", cc_app.stats_page)
    await user.should_see(marker="stage-noise")
    await user.should_see("Noise removed by each stage over time")
