from contextlib import contextmanager

from nicegui import ui


@contextmanager
def workflow_section(title: str, description: str = ""):
    with ui.column().classes("w-full gap-2 cc-workflow-section") as holder:
        with ui.column().classes("gap-0"):
            ui.label(title).classes("cc-section")
            if description:
                ui.label(description).classes("cc-muted")
        yield holder


def summary_counts(items: list[tuple[str, object]]) -> None:
    with ui.row().classes("w-full gap-2 flex-wrap cc-summary-counts"):
        for label, value in items:
            with ui.element("div").classes("cc-summary-count"):
                ui.label(str(value)).classes("cc-summary-count-value")
                ui.label(label).classes("cc-summary-count-label")


def overflow_menu(actions, *, marker: str, label: str = "More actions") -> None:
    with ui.button(icon="more_horiz").props(
            f"flat round aria-label='{label}'").mark(marker):
        with ui.menu():
            for action in actions:
                text, icon, callback, action_marker = action
                ui.menu_item(text, on_click=callback).props(f"icon={icon}").mark(action_marker)


def workflow_state(title: str, body: str, kind: str = "info") -> None:
    with ui.column().classes("w-full items-center gap-2 py-12 cc-workflow-state") as region:
        role = "alert" if kind == "error" else "status"
        region.props(f"role={role} aria-label='{title}'")
        if kind == "loading":
            ui.spinner("dots", size="md")
        else:
            icon = {"success": "check_circle", "error": "error", "empty": "info"}.get(kind, "info")
            ui.icon(icon).classes("text-2xl")
        ui.label(title).classes("cc-section")
        ui.label(body).classes("cc-page-sub text-center")


def empty_state(title: str, body: str, icon: str = "info") -> None:
    # Keep the custom icon for existing callers while sharing the same state layout.
    with ui.column().classes("w-full items-center gap-2 py-12 cc-workflow-state cc-empty-state") as region:
        region.props(f"role=status aria-label='{title}'")
        ui.icon(icon).classes("text-2xl")
        ui.label(title).classes("cc-section")
        ui.label(body).classes("cc-page-sub text-center")
