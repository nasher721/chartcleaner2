"""Clean page: the safety strip and header, stale lines on the Insights tab, and
the copy guard that asks before copying a result whose fact check raised an alert."""

from __future__ import annotations

import asyncio

import pytest
from nicegui.testing import User

import app as cc_app
from app_pages import clean as clean_mod
from app_pages import common
from chartcleaner import fact_check
from chartcleaner.appstate import CLEAN_STATE
from tests.test_phase8_ui import _route, page  # noqa: F401 (fixture)
from tests.test_safety_stale import CHART

pytestmark = pytest.mark.nicegui_main_file("")


async def _clean(user: User, route: str) -> None:
    CLEAN_STATE.update(input=CHART, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, route, cc_app.clean_page)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)


async def test_safety_strip_and_insight_blocks(user: User, page):
    await _clean(user, "/ss-insights")
    await user.should_see(marker="safety-strip")
    await user.should_see(marker="insight-safety")
    await user.should_see(marker="insight-stale")
    assert CLEAN_STATE["safety"].changed
    assert [s.text for s in CLEAN_STATE["stale"].lines] == ["Plan to extubate tmrw.", "MRI pending."]
    assert CLEAN_STATE["fact_ack"] is False
    # the default rules drop "Code status:" lines; the header says so instead of "not stated"
    code = CLEAN_STATE["safety"].get("Code status")
    assert code.status == "removed by cleaning"
    assert "Code status: DNR/DNI after family meeting" in code.removed


def _force_alert(monkeypatch):
    monkeypatch.setattr(fact_check.FactReport, "status", property(lambda self: "alert"))


async def test_copy_guard_asks_once_then_copies(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda text, note="": copied.append(text))
    monkeypatch.setitem(common.PREFS, "copy_guard", True)
    _force_alert(monkeypatch)
    await _clean(user, "/ss-guard")
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    await user.should_see(marker="copy-guard")
    assert not copied
    user.find(marker="copy-anyway").click()
    assert copied and CLEAN_STATE["fact_ack"] is True
    user.find(marker="copy-as").click()
    user.find(marker="copy-safety").click()
    await user.should_see(marker="copy-guard")  # the earlier dialog, still in the page
    assert copied[-1].startswith("Safety header")


async def test_copy_guard_off_copies_straight_away(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda text, note="": copied.append(text))
    monkeypatch.setitem(common.PREFS, "copy_guard", False)
    _force_alert(monkeypatch)
    await _clean(user, "/ss-guard-off")
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    assert copied
    await user.should_not_see(marker="copy-guard")
