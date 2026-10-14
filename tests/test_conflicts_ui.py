"""Clean page: possible contradictions as a chip above the output and a block on Insights."""

from __future__ import annotations

import asyncio

import pytest
from nicegui.testing import User

import app as cc_app
from chartcleaner.appstate import CLEAN_STATE
from tests.test_conflicts import CHART
from tests.test_phase8_ui import _route, page  # noqa: F401 (fixture)

pytestmark = pytest.mark.nicegui_main_file("")


async def test_conflicts_chip_and_insight_block(user: User, page):
    CLEAN_STATE.update(input=CHART, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, "/conflicts-ui", cc_app.clean_page)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    report = CLEAN_STATE["conflicts"]
    assert report is not None and "side" in [c.kind for c in report.conflicts]
    await user.should_see(marker="conflicts-chip")
    await user.should_see(marker="insight-conflicts")
