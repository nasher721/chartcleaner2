"""The /census page: empty state, bed cards, and Open → Clean page."""

from __future__ import annotations

import pytest
from nicegui.testing import User

import app as cc_app
from app_pages import common
from chartcleaner import recent_charts
from chartcleaner.appstate import CLEAN_STATE
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
