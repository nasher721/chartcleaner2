"""The Rules page (/rules): learned rules, abbreviations, sharing."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers


def _abbreviation_preview_text() -> str:
    """Chart used for abbreviation previews: the Clean page input, else the sample."""
    text = CLEAN_STATE.get("input") or ""
    if text.strip():
        return text
    try:
        return (BASE_DIR / "sample_chart.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


def text_rules_page():
    from chartcleaner.abbreviation_editor import render as render_abbreviations
    from chartcleaner.learned_editor import render_learned_editor
    from chartcleaner.rule_sharing import render_rule_sharing

    def load_current() -> dict:
        return load_config(common.CONFIG_PATH)

    with shell("My text rules", "/rules"):
        ui.label("Manage what you remove, replace and abbreviate. Changes save immediately.") \
            .classes("text-sm opacity-70")
        with ui.tabs().classes("w-full") as tabs:
            learned = ui.tab("Remove & replace", icon="find_replace")
            abbreviations = ui.tab("Abbreviations", icon="short_text")
            sharing = ui.tab("Share & import", icon="import_export")
        with ui.tab_panels(tabs, value=learned).classes("w-full"):
            with ui.tab_panel(learned):
                learned_holder = ui.column().classes("w-full")
            with ui.tab_panel(abbreviations):
                abbreviation_holder = ui.column().classes("w-full")
            with ui.tab_panel(sharing):
                sharing_holder = ui.column().classes("w-full")

        def render_tab(name: str) -> None:
            holder, renderer = {
                "Remove & replace": (learned_holder, render_learned_editor),
                "Abbreviations": (abbreviation_holder, lambda load, save:
                    render_abbreviations(load, save, sample_text=_abbreviation_preview_text)),
                "Share & import": (sharing_holder, lambda load, save:
                    render_rule_sharing(load, save, lambda: open_folder(store.EXPORTS_DIR))),
            }[name]
            holder.clear()
            with holder:
                renderer(load_current, save_config_with_backup)

        tabs.on_value_change(lambda e: render_tab(e.value))
        render_tab("Remove & replace")
        ui.button("Full settings backup", icon="backup",
                  on_click=lambda: ui.navigate.to("/settings")).props("flat")
