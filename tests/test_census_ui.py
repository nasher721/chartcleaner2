"""The /census page: empty state, bed cards, and Open → Clean page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nicegui.testing import User

import app as cc_app
from app_pages import census as census_ui
from app_pages import common
from chartcleaner import recent_charts
from chartcleaner.appstate import CLEAN_STATE
from chartcleaner.census import BedCard, CensusReport
from chartcleaner.engine import load_default_config, save_config
from tests.test_conflicts import CHART
from tests.test_phase8_ui import _route, page  # noqa: F401 (fixture)

pytestmark = pytest.mark.nicegui_main_file("")


@pytest.fixture
def quiet_config(tmp_path, monkeypatch):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)


async def test_census_empty_state(user: User, page, quiet_config):
    await _route(user, "/census-empty", cc_app.census_page)
    await user.should_see(marker="census-empty", retries=100)


async def test_census_cards_and_open(user: User, page, quiet_config):
    recent_charts.remember(CHART, "test", {}, tag="G20-1")
    await _route(user, "/census-beds", cc_app.census_page)
    await user.should_see(marker="census-bed-G20-1", retries=200)
    await user.should_see("G20-1")
    user.find(marker="census-open-G20-1").click()
    assert CLEAN_STATE["input"] == CHART and CLEAN_STATE["tag"] == "G20-1"


def test_visible_beds_filters_only_beds_without_attention():
    quiet = SimpleNamespace(tag="G20-2", attention=0)
    flagged = SimpleNamespace(tag="G20-1", attention=2)
    beds = [quiet, flagged]

    assert census_ui.visible_beds(beds, "attention") == [flagged]
    assert census_ui.visible_beds(beds, "all") == beds
    assert census_ui.visible_beds(beds, "all") is not beds


async def test_census_attention_filter_preserves_safety_and_open(user: User, page, quiet_config):
    recent_charts.remember(CHART, "test", {}, tag="G20-1")
    await _route(user, "/census-filter", cc_app.census_page)
    await user.should_see(marker="census-bed-G20-1", retries=200)
    switch = next(iter(user.find(marker="census-filter").elements))
    with user.client:
        switch.set_value("attention")
    await user.should_see(marker="census-bed-G20-1")
    user.find(marker="census-export").click()
    await user.should_see(marker="census-copy")
    await user.should_see(marker="census-dl-md")
    user.find(marker="census-open-G20-1").click()
    assert CLEAN_STATE["tag"] == "G20-1"


async def test_census_filter_hides_only_quiet_bed_and_keeps_total_summary(
        user: User, page, quiet_config, monkeypatch):
    recent_charts.remember(CHART, "test", {}, tag="G20-1")
    recent_charts.remember("Stable. Continue care.", "test", {}, tag="G20-2")
    report = CensusReport([
        BedCard("G20-1", safety_flags=["Code status: changed"]),
        BedCard("G20-2", allergies="NKDA", code_status="Full code"),
    ])
    monkeypatch.setattr(census_ui.census, "build", lambda _cfg, *, records: report)
    await _route(user, "/census-filter-totals", cc_app.census_page)
    await user.should_see(marker="census-bed-G20-2", retries=200)

    switch = next(iter(user.find(marker="census-filter").elements))
    with user.client:
        switch.set_value("attention")

    await user.should_see(marker="census-bed-G20-1")
    await user.should_not_see(marker="census-bed-G20-2")
    summary = next(iter(user.find(marker="census-summary").elements))
    assert summary.text == "2 beds · 1 with something to look at"


async def test_census_attention_filter_uses_shared_empty_state(
        user: User, page, quiet_config, monkeypatch):
    recent_charts.remember("Stable. Continue care.", "test", {}, tag="G20-2")
    report = CensusReport([BedCard("G20-2", allergies="NKDA", code_status="Full code")])
    monkeypatch.setattr(census_ui.census, "build", lambda _cfg, *, records: report)
    await _route(user, "/census-filter-empty", cc_app.census_page)
    await user.should_see(marker="census-bed-G20-2", retries=200)

    switch = next(iter(user.find(marker="census-filter").elements))
    with user.client:
        switch.set_value("attention")

    await user.should_see("No beds need attention")
    await user.should_not_see(marker="census-bed-G20-2")
    summary = next(iter(user.find(marker="census-summary").elements))
    assert summary.text == "1 bed"


async def test_census_details_collapse_secondary_content_not_safety(
        user: User, page, quiet_config, monkeypatch):
    recent_charts.remember(CHART, "test", {}, tag="G20-1")
    report = CensusReport([BedCard(
        "G20-1",
        allergies="Penicillin (hives)",
        code_status="DNR/DNI",
        safety_flags=["Allergies: changed"],
        conflicts=["side — weakness changed sides"],
        antibiotics=["Pip-tazo 4.5 g IV q8h (day 2)"],
        devices_review=["Central line day 8"],
        bundle_gaps=["DVT prophylaxis", "Head of bed"],
        stale=2,
        todo=["Repeat K in the morning"],
        todo_total=1,
        delta=["K 5.9. KCl 20 mEq PO BID."],
        delta_total=1,
        delta_since="10/11/2026",
    )])
    monkeypatch.setattr(census_ui.census, "build", lambda _cfg, *, records: report)
    await _route(user, "/census-disclosure", cc_app.census_page)
    await user.should_see(marker="census-bed-G20-1", retries=200)
    await user.should_see("! Allergies: changed")
    await user.should_see("! contradiction: side — weakness changed sides")

    details = next(iter(user.find(marker="census-details-G20-1").elements))
    assert details.value is False
    detail_text = " ".join(getattr(el, "text", "") for el in details.descendants())
    assert "2 bundle items not mentioned" in detail_text
    assert "2 possibly stale" in detail_text
    assert "Repeat K in the morning" in detail_text
    assert "1 new/changed line since 10/11/2026" in detail_text
    assert "Allergies: changed" not in detail_text
    assert "contradiction" not in detail_text
    assert "Pip-tazo" not in detail_text
    assert "Central line" not in detail_text
