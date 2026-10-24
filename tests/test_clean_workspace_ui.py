import pytest
from nicegui import ui
from nicegui.testing import User
import app as cc_app
from app_pages.workspace_layout import set_mobile_view
from chartcleaner import fact_check
from chartcleaner.appstate import CLEAN_STATE
from tests.test_phase8_ui import _route, page  # noqa: F401

pytestmark = pytest.mark.nicegui_main_file("")


_SYNC_SLOT = None
_SYNC_SLOT_PARENT = None
_SYNC_SLOT_CLIENT = None


@pytest.fixture(scope="module", autouse=True)
def _restore_nicegui_slot():
    """Preserve an existing sync-UI slot for tests run after this module."""
    from nicegui import context

    global _SYNC_SLOT, _SYNC_SLOT_PARENT, _SYNC_SLOT_CLIENT
    try:
        _SYNC_SLOT = context.slot
        _SYNC_SLOT_PARENT = _SYNC_SLOT.parent
        _SYNC_SLOT_CLIENT = _SYNC_SLOT_PARENT.client
    except RuntimeError:
        _SYNC_SLOT = None
    yield
    if _SYNC_SLOT is not None:
        _SYNC_SLOT.__enter__()


def _reset_input() -> None:
    CLEAN_STATE.update(input="Plan: recheck\n", mode="clean", result=None,
                       result_text="", audit=None, summary=None, qa=[])


async def test_mobile_view_switches_only_presentation_classes(user: User, page):
    layouts = []

    def state_page():
        with ui.element("div") as layout:
            layouts.append(layout)

    await _route(user, "/workspace-state", state_page)
    layout = layouts[0]
    with user.client:
        set_mobile_view(layout, "result")
    classes = " ".join(layout._classes)
    assert "cc-mobile-show-result" in classes
    assert "cc-mobile-show-input" not in classes
    with user.client:
        set_mobile_view(layout, "input")
    classes = " ".join(layout._classes)
    assert "cc-mobile-show-input" in classes


async def test_clean_has_mobile_switch_and_split_separator(user: User, page):
    _reset_input()
    await _route(user, "/workspace-responsive", cc_app.clean_page)
    handle = next(iter(user.find(marker="clean-split-handle").elements))
    assert handle.props.get("role") == "separator"
    assert handle.props.get("aria-orientation") == "vertical"
    assert handle.props.get("tabindex") == "0"
    switch = next(iter(user.find(marker="mobile-workspace-switch").elements))
    with user.client:
        switch.set_value("result")
    layout = next(iter(user.find(marker="clean-workspace").elements))
    assert "cc-mobile-show-result" in layout._classes


async def test_advanced_input_controls_are_disclosed(user: User, page):
    _reset_input()
    await _route(user, "/workspace-input", cc_app.clean_page)
    await user.should_see(marker="clean-task-flow")
    flow = next(iter(user.find(marker="clean-task-flow").elements))
    assert flow.props["aria-label"] == "1 Input → 2 Review → 3 Export"
    assert flow.props["data-current-step"] == "1"
    await user.should_see(marker="run-clean")
    await user.should_see(marker="clean-mode")
    await user.should_see(marker="clean-destination")
    await user.should_not_see(marker="selection-mode")
    user.find(marker="advanced-input-tools").click()
    await user.should_see(marker="selection-mode")
    user.find(marker="input-overflow").click()
    await user.should_see("Sample")
    await user.should_see("Clear")


async def test_selection_overflow_keeps_advanced_actions(user: User, page):
    text = "Plan: recheck\n"
    _reset_input()
    await _route(user, "/workspace-selection", cc_app.clean_page)
    user.find(marker="highlight-source").trigger(
        "mouseup", {"start": 0, "end": 4, "text": "Plan", "value": text})
    await user.should_see(marker="selection-bar")
    for marker in ("sel-remove", "sel-line", "sel-replace", "sel-abbreviate", "sel-build"):
        await user.should_see(marker=marker)
    await user.should_not_see(marker="sel-keep")
    await user.should_not_see(marker="sel-collect")
    await user.should_not_see(marker="sel-more")
    user.find(marker="selection-overflow").click()
    await user.should_see(marker="sel-keep")
    await user.should_see(marker="sel-collect")
    await user.should_see(marker="sel-more")
    await user.should_see("Whole words only")
    await user.should_see("Match case")
    await user.should_see("Close")


async def test_result_has_one_release_aware_primary_action(user: User, page):
    _reset_input()
    await _route(user, "/workspace-output", cc_app.clean_page)
    user.find(marker="run-clean").click()
    await user.should_see(marker="result-actions", retries=200)
    await user.should_see(marker="copy-as")
    await user.should_see(marker="destination-summary")
    await user.should_see(marker="release-status")
    await user.should_not_see(marker="review-primary")
    assert CLEAN_STATE["result"] is not None
    flow = next(iter(user.find(marker="clean-task-flow").elements))
    assert flow.props["data-current-step"] == "3"
    user.find(marker="result-overflow").click()
    await user.should_see("Save")
    await user.should_see(marker="save-txt")


async def test_not_ready_result_promotes_review_over_copy(user: User, page, monkeypatch):
    from app_pages import clean as clean_mod

    copied = []
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda text, note="": copied.append(text))
    monkeypatch.setattr(fact_check.FactReport, "status", property(lambda self: "alert"))
    _reset_input()
    await _route(user, "/workspace-review-first", cc_app.clean_page)
    user.find(marker="run-clean").click()
    await user.should_see(marker="review-primary", retries=200)
    copy_button = next(iter(user.find(marker="copy-as").elements))
    assert copy_button.props["outline"] is True
    assert not copy_button.props.get("unelevated")
    flow = next(iter(user.find(marker="clean-task-flow").elements))
    assert flow.props["data-current-step"] == "2"
    user.find(marker="review-primary").click()
    tabs = next(iter(user.find(ui.tabs).elements))
    assert tabs.value.props["name"] == "Review"

    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    await user.should_see(marker="copy-anyway")
    assert not copied
    user.find(marker="copy-anyway").click()
    assert copied
    await user.should_not_see(marker="review-primary")
    copy_button = next(iter(user.find(marker="copy-as").elements))
    assert copy_button.props["unelevated"] is True
    assert not copy_button.props.get("outline")
    flow = next(iter(user.find(marker="clean-task-flow").elements))
    assert flow.props["data-current-step"] == "3"
