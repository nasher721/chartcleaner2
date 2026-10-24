"""Accessibility layer: shell semantics, keyboard-operable controls, diff tables.

The design system renders much of its chrome as styled ``div``s, which read
fine on screen but say nothing to assistive technology. These tests pin the
semantics added on top: landmarks, a skip link, live-region/status labels,
keyboard activation for the pills, and captioned diff tables.
"""

from __future__ import annotations

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app  # noqa: F401  (registers the routes for the harness)
from app_pages import a11y, common
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE

pytestmark = pytest.mark.nicegui_main_file("")


@pytest.fixture()
def clean_state():
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    yield
    CLEAN_STATE.clear()
    CLEAN_STATE.update(before)
    AUTO_LAST.clear()
    AUTO_LAST.update(auto_before)


def _props(el) -> str:
    """A stable string view of an element's props and classes for assertions."""
    return " ".join(f"{k}={v}" for k, v in (el._props or {}).items()) + " " + " ".join(el._classes or [])


def _first_container():
    return ui.column()


# ---------------------------------------------------------------------------
# the module's element semantics
# ---------------------------------------------------------------------------

def test_sr_only_exposes_a_hidden_label():
    with ui.row() as row:
        a11y.sr_only("Chart Cleaner home")
    wrapper = row.default_slot.children[0]
    assert "cc-sr-only" in (wrapper._classes or [])
    label = wrapper.default_slot.children[0]
    assert "Chart Cleaner home" in str(label._text)


def test_skip_link_points_at_the_main_landmark():
    with ui.row() as row:
        a11y.skip_link()
    link = row.default_slot.children[0]
    assert "cc-skip" in (link._classes or [])
    assert link._props.get("href") == "#cc-main"


def test_landmark_sets_role_and_id_for_main():
    with ui.column() as col:
        with a11y.landmark("main", label="Clean a chart"):
            ui.label("body")
    region = col.default_slot.children[0]
    assert region._props.get("role") == "main"
    assert region._props.get("id") == "cc-main"
    assert region._props.get("aria-label") == "Clean a chart"
    assert region._props.get("tabindex") == "-1"


def test_landmark_nav_does_not_claim_the_main_id():
    with ui.column() as col:
        with a11y.landmark("navigation", label="Primary"):
            ui.label("nav")
    region = col.default_slot.children[0]
    assert region._props.get("role") == "navigation"
    assert "id" not in region._props


def test_status_region_is_a_polite_live_region():
    with ui.column() as col:
        with a11y.status_region(label="Cleaning status"):
            ui.label("idle")
    region = col.default_slot.children[0]
    assert region._props.get("role") == "status"
    assert region._props.get("aria-live") == "polite"
    assert region._props.get("aria-label") == "Cleaning status"


def test_activate_on_key_makes_an_element_focusable_and_handles_keys():
    with ui.element("div") as el:
        ui.label("Preset")
    a11y.activate_on_key(el, label="Open the pipeline")
    assert el._props.get("tabindex") == "0"
    assert el._props.get("role") == "button"
    assert el._props.get("aria-label") == "Open the pipeline"
    assert "cc-activatable" in (el._classes or [])
    registered = {listener.type for listener in el._event_listeners.values()}
    assert {"keydown.enter", "keydown.space"} <= registered


def test_status_tag_with_click_is_keyboard_operable():
    from app_pages.design_system import status_tag

    with ui.row() as row:
        status_tag("watcher on", "on", tooltip="Clipboard watcher", on_click=lambda: None)
    tag = row.default_slot.children[0]
    assert tag._props.get("role") == "button"
    assert tag._props.get("tabindex") == "0"
    assert tag._props.get("aria-label") == "Clipboard watcher"
    registered = {listener.type for listener in tag._event_listeners.values()}
    assert {"keydown.enter", "keydown.space"} <= registered


def test_status_tag_without_click_is_not_a_control():
    from app_pages.design_system import status_tag

    with ui.row() as row:
        status_tag("encrypted", "info")
    tag = row.default_slot.children[0]
    assert "role" not in tag._props


# ---------------------------------------------------------------------------
# diff tables carry a caption and column headers
# ---------------------------------------------------------------------------

def test_diff_html_is_a_captioned_table():
    html = common.diff_html("keep me\ndrop me\n", "keep me\n")
    assert "<caption" in html
    assert "scope='col'" in html
    assert "Before" in html and "After" in html
    assert html.count("<tbody>") == 1


def test_review_diff_html_is_a_captioned_table_and_marks_spans():
    groups = [{"kind": "removal", "before": "Printed by EPIC", "after": "",
               "befores": ["Printed by EPIC"]}]
    html = common.review_diff_html("Printed by EPIC\nreal line\n", "real line\n", None, groups)
    assert "<caption" in html
    assert "scope='col'" in html
    # The clickable span stays keyboard reachable.
    assert "tabindex='0'" in html
    assert "role='button'" in html


def test_diff_html_large_input_is_refused():
    big = "x\n" * 400_000
    assert "too large" in common.diff_html(big, big)


# ---------------------------------------------------------------------------
# the shared shell, rendered through a real page
# ---------------------------------------------------------------------------

async def test_shell_renders_skip_link_and_main_landmark(user: User, clean_state):
    @ui.page("/a11y-shell")
    async def fixture_page():
        with common.shell("Accessibility shell", "clean"):
            ui.label("page body")

    await user.open("/a11y-shell")
    elements = list(user.find().elements)
    links = [e for e in elements if getattr(e, "_props", {}).get("href") == "#cc-main"]
    assert links, "the skip link must be on every page"
    mains = [e for e in elements if getattr(e, "_props", {}).get("role") == "main"]
    assert mains, "the main landmark must be present"
    assert mains[0]._props.get("id") == "cc-main"


async def test_shell_navigation_marks_the_active_page(user: User, clean_state):
    @ui.page("/a11y-nav")
    async def fixture_page():
        with common.shell("Accessibility nav", "settings"):
            ui.label("settings body")

    await user.open("/a11y-nav")
    active = [e for e in user.find().elements
              if getattr(e, "_props", {}).get("aria-current") == "page"]
    assert len(active) == 1, "exactly one visible navigation item must carry aria-current"


async def test_shell_groups_navigation_and_keeps_all_drawer_routes(user: User, clean_state):
    @ui.page("/a11y-grouped-nav")
    async def fixture_page():
        with common.shell("Accessibility grouped nav", "pipeline"):
            ui.label("pipeline body")

    await user.open("/a11y-grouped-nav")
    elements = list(user.find().elements)
    direct = [e for e in elements
              if "cc-navlink" in e._classes and "cc-navgroup" not in e._classes]
    assert {e._props.get("href") for e in direct} == {"/", "/batch", "/census"}
    group_text = " ".join(getattr(e, "text", "") for e in elements
                          if "cc-navgroup" in e._classes)
    assert "Customize" in group_text and "System" in group_text
    drawer_routes = [e for e in elements if "cc-nav-item" in e._classes]
    assert len(drawer_routes) == 9


async def test_status_strip_groups_local_privacy_details(user: User, clean_state):
    @ui.page("/a11y-status-strip")
    async def fixture_page():
        with common.shell("Accessibility status", "clean"):
            ui.label("page body")

    await user.open("/a11y-status-strip")
    strip = next(e for e in user.find().elements if "status-strip" in e._markers)
    text = " ".join(getattr(e, "text", "") for e in strip.descendants())
    assert "Current rules" in text
    assert "Local AI" in text
    assert "Local & private" in text
    assert "Clipboard monitoring off" in text
    assert "Stored data encrypted" in text
    assert "release" not in text.casefold()


def test_workflow_css_uses_the_shared_design_tokens():
    for selector in (".cc-summary-count", ".cc-workflow-section", ".cc-empty-state",
                     ".cc-navgroup--active"):
        start = common.CSS.index(selector)
        rule = common.CSS[start:common.CSS.index("}", start)]
        assert "var(--nd-" in rule


async def test_pages_declare_their_language(user: User, clean_state):
    """<html lang> is what lets a screen reader pick the right pronunciation."""
    @ui.page("/a11y-lang", language="en")
    async def fixture_page():
        with common.shell("Accessibility lang", "clean"):
            ui.label("body")

    response = await user.http_client.get("/a11y-lang")
    assert 'lang="en"' in response.text
