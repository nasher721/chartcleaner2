"""Phase 8 UI: to-do and ICU bundle blocks on the Insights tab, and the
Settings → Local AI "Compare models" table."""

from __future__ import annotations

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import common
from chartcleaner import model_compare, store
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
from chartcleaner.engine import load_default_config, save_config

pytestmark = pytest.mark.nicegui_main_file("")

CHART = """Date of Service: 09/15/2026
Intubated 9/13, on the vent, PEEP 5.
Foley in place since 9/11.
Urine cx pending.

Assessment & Plan:
1. SAH, Hunt-Hess 3.
   - Nimodipine 60 mg q4h.
   - Repeat CTH in AM.
2. Prophylaxis: heparin 5000 units SQ q8h.
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


async def _route(user: User, route: str, page_fn) -> None:
    @ui.page(route)
    async def fixture_page():
        await page_fn() if asyncio.iscoroutinefunction(page_fn) else page_fn()

    await user.open(route)


async def test_insights_show_todo_and_bundle(user: User, page):
    CLEAN_STATE.update(input=CHART, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, "/p8-insights", cc_app.clean_page)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    await user.should_see(marker="insight-pending")
    await user.should_see(marker="insight-bundle")
    await user.should_see("Urine cx pending.")
    await user.should_see("Repeat CTH in AM.")
    rep = CLEAN_STATE["bundle"]
    assert rep.ventilated
    assert "GI prophylaxis" in [i.name for i in rep.gaps]
    assert [i.text for i in CLEAN_STATE["pending"].items][0] == "Urine cx pending."


async def test_settings_compare_models(user: User, page, monkeypatch):
    calls = []

    class Fake:
        def is_available(self):
            return True

        def list_models(self):
            return ["good", "bad"]

        def generate(self, prompt, model="", system=None, **kw):
            return "Na 131." if model == "good" else "Na 120. Meropenem 2 g."

    real = model_compare.compare

    def fake_compare(cfg, models=None, charts=None, **kw):
        calls.append(kw.get("preset"))
        return real(cfg, models, [("Chart", "Na 131. GCS 14.")], client=Fake(), **kw)

    monkeypatch.setattr(model_compare, "compare", fake_compare)
    await _route(user, "/p8-settings", cc_app.settings_page)
    await user.should_see(marker="compare-models")
    user.find(marker="compare-models").click()
    await user.should_see(marker="compare-row-0", retries=100)
    await user.should_see(marker="compare-row-1")
    await user.should_see("best")
    assert calls == ["clinical"]
