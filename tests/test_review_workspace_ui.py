"""Unified Review workspace: findings, changes, diagnostics and safe fallback."""

import asyncio
import difflib
import json
import re
from types import SimpleNamespace as NS

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user import UserInteraction

import app as cc_app
from app_pages import clean as clean_mod
from app_pages import common, review_workspace
from app_pages.review_model import FindingRow
from app_pages.review_workspace import collapse_context_ranges
from chartcleaner.appstate import CLEAN_STATE
from chartcleaner.engine import Pipeline, load_config
from tests.test_phase8_ui import _route, page  # noqa: F401
from tests.test_validation_review_ui import BLOCK_CFG, BLOCK_CHART, _write_cfg

pytestmark = pytest.mark.nicegui_main_file("")


async def _clean(user, route, text=BLOCK_CHART):
    CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="",
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


def test_context_ranges_cover_only_long_unchanged_runs():
    # Changed diff rows 5 and 12 keep rows 2–15 visible; only rows 16–20 collapse.
    ranges = collapse_context_ranges(20, {5, 12}, context=3)
    assert ranges == [(16, 20)]
    assert all(not (start <= n <= end) for start, end in ranges for n in (5, 12))
    assert collapse_context_ranges(6, {3}, context=3) == []


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
    user.find(marker="review-findings-tab").click()
    finding_filter = next(iter(user.find(marker="finding-filter").elements))
    finding_filter.set_value("info")
    await user.should_see(marker="blocking-banner")


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


async def test_late_review_failure_replaces_partial_changes(user: User, page, monkeypatch, caplog):
    _write_cfg(**BLOCK_CFG)
    before = "\n".join(f"line {n}" for n in range(1, 31)) + "\n"
    after = before.replace("line 5", "line 5 changed").replace("line 20", "line 20 changed")

    def fail_after_changes(**kwargs):
        kwargs["sections"].diff()
        raise RuntimeError("late render failed")

    monkeypatch.setattr(review_workspace, "render_review_workspace", fail_after_changes)
    await _clean(user, "/workspace-late-fallback", before)
    result = CLEAN_STATE["result"]
    result.text = after
    CLEAN_STATE["result_text"] = after
    CLEAN_STATE["review_html"] = None
    # Re-render through the page's existing edit action rather than rebuilding the pipeline.
    user.find(marker="edit-result").click()
    out = next(iter(user.find(marker="result-output").elements))
    out.set_value(after + "\n")
    out.set_value(after)
    user.find(marker="edit-result").click()
    user.find(marker="review-tab").click()
    await user.should_see(marker="review-fallback")
    diffs = list(user.find(marker="review-diff").elements)
    assert len(diffs) == 1
    ids = re.findall(r"\bid=['\"]([^'\"]+)", diffs[0].content)
    assert len(ids) == len(set(ids))
    caplog.clear()


async def test_badge_uses_same_finding_projection_as_workspace(user: User, page):
    _write_cfg(boilerplate=[r"^Plan: aspirin 81 mg daily\.\n"])
    await _clean(user, "/workspace-shared-count")
    badge = next(iter(user.find(marker="review-count").elements))
    findings_tab = next(iter(user.find(marker="review-findings-tab").elements))
    label = findings_tab.props.get("label")
    assert badge.text in label
    assert label == f"Findings ({badge.text})"
    assert badge.text != "0"


async def test_retained_original_locate_uses_shipped_text_and_labels_candidate(
        user: User, page, monkeypatch):
    _write_cfg(literal_replacements=[["Na 141 mmol/L", "Na"]])
    scripts = []
    monkeypatch.setattr(clean_mod.ui, "run_javascript", lambda script: scripts.append(script))
    await _clean(user, "/workspace-retained-locate")
    proposals = [p for p in CLEAN_STATE["result"].ledger.proposals
                 if p.decision == "retain_original"]
    assert proposals and proposals[0].before == "Na 141 mmol/L"
    assert proposals[0].after == "Na"
    user.find(marker="review-tab").click()
    user.find(marker="review-findings-tab").click()
    row = next(row for row in user.find(marker="finding-row").elements
               if any(getattr(e, "text", "") == "Edit kept as original"
                      for e in row.descendants()))
    locate = next(e for e in row.descendants() if "locate-finding" in e._markers)
    UserInteraction(user, {locate}, None).click()
    await user.should_see(marker="finding-location")
    await user.should_see("Rejected proposed edit")
    await user.should_see("Output")
    user.find("Locate in panes").click()
    script = scripts[-1]
    targets = json.loads(script.split(" of ", 1)[1].split(") {", 1)[0])
    assert dict(targets)[".cc-out textarea"] == proposals[0].before
    assert dict(targets)[".cc-out textarea"] != proposals[0].after


async def test_informational_findings_default_to_all_and_empty_filter_has_message(
        user: User, page, monkeypatch):
    await _render_rows(user, "/workspace-info", [_finding("info")], monkeypatch)
    user.find(marker="review-findings-tab").click()
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


async def test_change_navigation_has_position_and_controls(user: User, page):
    _write_cfg(**BLOCK_CFG)
    await _clean(user, "/workspace-diff-nav")
    user.find(marker="review-changes-tab").click()
    await user.should_see(marker="change-previous")
    await user.should_see(marker="change-next")
    position = next(iter(user.find(marker="change-position").elements)).text
    assert "/" in position


async def test_zero_change_navigation_stays_at_zero(user: User, page, monkeypatch):
    scripts = []
    monkeypatch.setattr(clean_mod.ui, "run_javascript", lambda script: scripts.append(script))
    await _render_rows(user, "/workspace-zero-changes", [], monkeypatch)
    user.find(marker="review-changes-tab").click()
    position = next(iter(user.find(marker="change-position").elements))
    assert position.text == "0 / 0"
    user.find(marker="change-previous").click()
    user.find(marker="change-next").click()
    assert len(scripts) == 2
    assert all("if (!rows.length)" in script and "0 / 0" in script for script in scripts)
    assert position.text == "0 / 0"


async def test_clean_page_oversized_diff_uses_cached_metadata(user: User, page):
    _write_cfg()
    result = Pipeline(load_config(common.CONFIG_PATH)).run(BLOCK_CHART)
    result.text = "x" * 600_001 + "\n"
    html = common.review_diff_html(BLOCK_CHART, result.text, None, [])
    CLEAN_STATE.update(input=BLOCK_CHART, result=result, result_text=result.text,
                       mode="clean", result_mode="clean", audit=None, summary=None, qa=[],
                       review_html=(result, BLOCK_CHART, result.text, html, 0))
    await _route(user, "/clean-oversized-diff", cc_app.clean_page)
    user.find(marker="review-changes-tab").click()
    await user.should_see("Diff too large to display")
    position = next(iter(user.find(marker="change-position").elements))
    assert position.text == "0 / 0"


async def test_oversized_diff_has_no_navigable_rows(user: User, page, monkeypatch):
    before = "x" * 600_001 + "\nPlan: aspirin 81 mg daily.\n"
    after = "x" * 600_001 + "\n"
    monkeypatch.setattr(review_workspace, "build_finding_rows", lambda *a, **kw: [])
    sections = review_workspace.ReviewSections(
        diff=lambda: ui.html(common.review_diff_html(before, after, None, [])),
        removed=lambda: None, abbreviations=lambda: None, diagnostics=lambda: None)

    def fixture_page():
        review_workspace.render_review_workspace(
            result=NS(), audit=None, source=before, output=after,
            decision=NS(status="ready"), sections=sections, locate=lambda _item: None)

    await _route(user, "/workspace-oversized-diff", fixture_page)
    await user.should_see("Diff too large to display")
    position = next(iter(user.find(marker="change-position").elements)).text
    assert position == "0 / 0"


async def test_workspace_does_not_recompute_diff_on_ui_thread(
        user: User, page, monkeypatch):
    def unexpected_diff(*_args, **_kwargs):
        raise AssertionError("Review UI recomputed the complete diff")

    monkeypatch.setattr(difflib, "SequenceMatcher", unexpected_diff)
    await _render_rows(user, "/workspace-no-recount", [], monkeypatch)
    position = next(iter(user.find(marker="change-position").elements)).text
    assert position == "0 / 0"
