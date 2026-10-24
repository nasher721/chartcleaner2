"""Unified Review workspace: findings, changes, diagnostics and safe fallback."""

import asyncio
from types import SimpleNamespace as NS

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import review_workspace
from app_pages.review_model import FindingRow
from chartcleaner.appstate import CLEAN_STATE
from tests.test_phase8_ui import _route, page  # noqa: F401
from tests.test_validation_review_ui import BLOCK_CFG, BLOCK_CHART, _write_cfg

pytestmark = pytest.mark.nicegui_main_file("")


async def _clean(user, route):
    CLEAN_STATE.update(input=BLOCK_CHART, mode="clean", result=None, result_text="",
                       audit=None, summary=None, qa=[], release_ack=None)
    await _route(user, route, cc_app.clean_page)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("clean did not finish")


def _finding(severity: str) -> FindingRow:
    occurrence = {"before": "source", "after": "output"}
    return FindingRow("test", severity, f"{severity} finding", "detail", "", None,
                      (occurrence,))


def _sections():
    return review_workspace.ReviewSections(diff=lambda: None, removed=lambda: None,
                                           abbreviations=lambda: None,
                                           diagnostics=lambda: None)


async def _render_rows(user: User, route: str, rows: list[FindingRow], monkeypatch) -> None:
    monkeypatch.setattr(review_workspace, "build_finding_rows", lambda *a, **kw: rows)

    def fixture_page():
        review_workspace.render_review_workspace(
            result=NS(), audit=None, source="source", output="output",
            decision=NS(status="ready"), sections=_sections(), locate=lambda _item: None)

    await _route(user, route, fixture_page)


async def test_blocking_banner_survives_view_and_filter_changes(user: User, page):
    _write_cfg(**BLOCK_CFG)
    await _clean(user, "/workspace-blocked")
    user.find(marker="review-tab").click()
    await user.should_see(marker="blocking-banner")
    user.find(marker="review-changes-tab").click()
    await user.should_see(marker="blocking-banner")
    user.find(marker="review-diagnostics-tab").click()
    await user.should_see(marker="blocking-banner")
    await user.should_see(marker="validation-panel")


async def test_review_failure_keeps_release_and_plain_diff(user: User, page, monkeypatch, caplog):
    _write_cfg(**BLOCK_CFG)
    monkeypatch.setattr(review_workspace, "render_review_workspace",
                        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("render failed")))
    await _clean(user, "/workspace-fallback")
    user.find(marker="review-tab").click()
    await user.should_see(marker="review-fallback")
    await user.should_see(marker="release-status")
    await user.should_see(marker="review-diff")
    status = next(iter(user.find(marker="release-status").elements)).text
    assert "ready" not in status.lower()
    caplog.clear()  # the forced rendering failure is intentionally logged


async def test_informational_findings_default_to_all_and_empty_filter_has_message(
        user: User, page, monkeypatch):
    await _render_rows(user, "/workspace-info", [_finding("info")], monkeypatch)
    control = next(iter(user.find(marker="finding-filter").elements))
    assert control.value == "all"
    await user.should_see(marker="finding-row")
    await user.should_see("info finding")
    control.set_value("review")
    await user.should_see("No findings match this filter.")


async def test_actionable_findings_default_to_needs_review(user: User, page, monkeypatch):
    await _render_rows(user, "/workspace-review",
                       [_finding("info"), _finding("blocked")], monkeypatch)
    control = next(iter(user.find(marker="finding-filter").elements))
    assert control.value == "review"
    await user.should_see("blocked finding")
