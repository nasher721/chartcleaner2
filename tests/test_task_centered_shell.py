import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app  # noqa: F401  (registers the routes for the harness)
from app_pages import navigation
from app_pages.workflow_ui import (
    empty_state,
    overflow_menu,
    summary_counts,
    workflow_section,
    workflow_state,
)

pytestmark = pytest.mark.nicegui_main_file("")


def test_navigation_groups_cover_every_existing_page_once():
    flattened = [item for _name, items in navigation.NAV_GROUPS for item in items]
    assert [item[0] for item in flattened] == [
        "/", "/batch", "/census", "/rules", "/pipeline", "/scripts",
        "/stats", "/settings", "/doctor",
    ]
    assert navigation.group_for("pipeline") == "Customize"
    assert navigation.group_for("clean") == "Work"
    assert navigation.group_for("missing") == ""


async def test_summary_counts_uses_stable_labels_and_values(user: User):
    elements = {}

    @ui.page("/task-summary-counts")
    def fixture_page():
        with ui.row() as elements["row"]:
            summary_counts([("Processed", 4), ("Needs review", 2), ("Errors", 1)])

    await user.open("/task-summary-counts")
    text = " ".join(getattr(el, "text", "") for el in elements["row"].descendants())
    assert "Processed" in text and "4" in text
    assert "Needs review" in text and "2" in text


async def test_empty_state_has_explicit_status_role(user: User):
    elements = {}

    @ui.page("/task-empty-state")
    def fixture_page():
        with ui.column() as elements["column"]:
            empty_state("No beds", "Clean a bed-tagged chart first.", icon="bed")

    await user.open("/task-empty-state")
    region = elements["column"].default_slot.children[0]
    assert region.props.get("role") == "status"
    assert region.props.get("aria-label") == "No beds"


async def test_error_state_uses_alert_without_changing_release_semantics(user: User):
    elements = {}

    @ui.page("/task-error-state")
    def fixture_page():
        with ui.column() as elements["column"]:
            workflow_state("Export failed", "Try the download again.", kind="error")

    await user.open("/task-error-state")
    region = elements["column"].default_slot.children[0]
    assert region.props.get("role") == "alert"


async def test_workflow_section_yields_its_content_container(user: User):
    elements = {}

    @ui.page("/task-workflow-section")
    def fixture_page():
        with ui.column() as elements["column"]:
            with workflow_section("Review output", "Check the cleaned chart.") as elements["section"]:
                ui.label("Chart body")

    await user.open("/task-workflow-section")
    section = elements["section"]
    assert elements["column"].default_slot.children[0] is section
    assert "cc-workflow-section" in section._classes
    text = " ".join(getattr(el, "text", "") for el in section.descendants())
    assert "Review output" in text
    assert "Check the cleaned chart." in text
    assert "Chart body" in text


async def test_overflow_menu_marks_trigger_and_actions(user: User):
    elements = {}

    @ui.page("/task-overflow-menu")
    def fixture_page():
        with ui.column() as elements["column"]:
            overflow_menu(
                [("Download", "download", lambda: None, "download-chart")],
                marker="chart-actions",
            )

    await user.open("/task-overflow-menu")
    trigger = elements["column"].default_slot.children[0]
    assert "chart-actions" in trigger._markers
    assert trigger.props.get("aria-label") == "More actions"
    action = next(el for el in trigger.descendants() if "download-chart" in el._markers)
    assert any(getattr(el, "text", "") == "Download" for el in action.descendants())


async def test_loading_state_uses_a_spinner_and_status_role(user: User):
    elements = {}

    @ui.page("/task-loading-state")
    def fixture_page():
        with ui.column() as elements["column"]:
            workflow_state("Cleaning", "Processing the chart.", kind="loading")

    await user.open("/task-loading-state")
    region = elements["column"].default_slot.children[0]
    assert region.props.get("role") == "status"
    assert any(el.tag == "q-spinner-dots" for el in region.descendants())
