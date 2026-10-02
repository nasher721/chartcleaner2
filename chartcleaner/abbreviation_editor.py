"""NiceGUI editor for bundled and user medical abbreviation rules."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

from .abbreviations import SOURCE_PATH, normalize_settings

PAGE_SIZE = 25


def _bundled() -> list[dict[str, str]]:
    with SOURCE_PATH.open(newline="", encoding="utf-8-sig") as source:
        rows = csv.DictReader(source)
        seen: set[str] = set()
        result = []
        for row in rows:
            term = row.get("Expanded version", "").strip()
            replacement = row.get("Abbreviation", "").strip()
            if not term or not replacement or term.casefold() in seen:
                continue
            seen.add(term.casefold())
            result.append({"term": term, "replacement": replacement, "kind": "bundled"})
    return result


def render(load_current: Callable[[], dict], save_current: Callable[[dict], None]):
    """Render a searchable paginated editor using fresh config reads on writes."""
    from nicegui import ui

    state = {"query": "", "page": 0}
    bundled = _bundled()

    def update(mutator: Callable[[dict], None]) -> None:
        config = load_current()
        group = normalize_settings((config.get("abbreviations") or {}) if isinstance(config, dict) else {})
        try:
            mutator(group)
        except ValueError as error:
            ui.notify(str(error), type="warning")
            return
        config["abbreviations"] = normalize_settings(group)
        try:
            save_current(config)
        except Exception as error:
            ui.notify(f"Could not save abbreviation settings: {error}", type="negative")
            return
        refresh()

    with ui.card().classes("w-full"):
        ui.label("Medical abbreviation dictionary").classes("text-lg font-semibold")
        search = ui.input("Search terms or replacements", on_change=lambda event: change_search(event.value)).props("clearable")
        with ui.row().classes("w-full items-center"):
            term_input = ui.input("New expanded term").classes("flex-grow")
            replacement_input = ui.input("Replacement").classes("flex-grow")
            ui.button("Add custom", icon="add", on_click=lambda: add_custom(term_input, replacement_input))
        listing = ui.column().classes("w-full gap-2")
        with ui.row().classes("items-center"):
            previous = ui.button("Previous", on_click=lambda: turn(-1)).props("flat")
            page_label = ui.label()
            next_page = ui.button("Next", on_click=lambda: turn(1)).props("flat")

    def change_search(value: str) -> None:
        state["query"] = (value or "").strip().casefold()
        state["page"] = 0
        refresh()

    def add_custom(term_control, replacement_control) -> None:
        term = (term_control.value or "").strip()
        replacement = (replacement_control.value or "").strip()
        if not term or not replacement:
            ui.notify("Both term and replacement are required", type="warning")
            return
        def mutate(group: dict) -> None:
            group["custom"] = [x for x in group["custom"] if x["term"].casefold() != term.casefold()]
            group["custom"].append({"term": term, "replacement": replacement, "enabled": True})
        update(mutate)
        term_control.value = replacement_control.value = ""

    def turn(delta: int) -> None:
        state["page"] = max(0, state["page"] + delta)
        refresh()

    def refresh() -> None:
        query = state["query"]
        config = load_current()
        group = normalize_settings((config.get("abbreviations") or {}) if isinstance(config, dict) else {})
        disabled = {x.casefold() for x in group["disabled"]}
        custom = {x["term"].casefold(): x for x in group["custom"]}
        entries = []
        for item in bundled:
            override = custom.get(item["term"].casefold())
            entries.append({**item, **(override or {}), "disabled": item["term"].casefold() in disabled, "custom": bool(override)})
        entries.extend({**item, "kind": "custom", "custom": True, "disabled": not item.get("enabled", True)} for item in group["custom"] if item["term"].casefold() not in {x["term"].casefold() for x in bundled})
        if query:
            entries = [x for x in entries if query in x["term"].casefold() or query in x["replacement"].casefold()]
        total_pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        state["page"] = min(state["page"], total_pages - 1)
        page_label.text = f"Page {state['page'] + 1} of {total_pages} ({len(entries)} terms)"
        previous.set_enabled(state["page"] > 0)
        next_page.set_enabled(state["page"] + 1 < total_pages)
        listing.clear()
        with listing:
            for item in entries[state["page"] * PAGE_SIZE:(state["page"] + 1) * PAGE_SIZE]:
                _row(item, update)

    refresh()
    return listing


def _row(item: dict, update: Callable[[Callable[[dict], None]], None]) -> None:
    from nicegui import ui

    with ui.row().classes("w-full items-center flex-wrap border-b pb-1"):
        ui.label(item["term"]).classes("font-medium min-w-[280px]")
        ui.label("→")
        ui.label(item["replacement"]).classes("min-w-[90px]")
        if item.get("custom"):
            enabled = item.get("enabled", not item.get("disabled", False))
            ui.switch("Enabled", value=enabled, on_change=lambda event, term=item["term"]: _set_custom_enabled(update, term, event.value))
            ui.button("Edit", icon="edit", on_click=lambda value=item: _edit_custom(update, value)).props("flat")
            ui.button("Remove", icon="delete", on_click=lambda term=item["term"]: _remove_custom(update, term)).props("flat color=negative")
        elif item.get("disabled"):
            ui.button("Edit", icon="edit", on_click=lambda value=item: _edit_custom(update, value)).props("flat")
            ui.button("Restore bundled", on_click=lambda term=item["term"]: _restore(update, term)).props("flat")
        else:
            ui.button("Edit", icon="edit", on_click=lambda value=item: _edit_custom(update, value)).props("flat")
            ui.button("Disable", on_click=lambda term=item["term"]: _disable(update, term)).props("flat")


def _set_custom_enabled(update, term: str, enabled: bool) -> None:
    def mutate(group: dict) -> None:
        for item in group["custom"]:
            if item["term"].casefold() == term.casefold(): item["enabled"] = bool(enabled)
    update(mutate)


def _edit_custom(update, item: dict) -> None:
    from nicegui import ui

    with ui.context.client.content, ui.dialog() as dialog, ui.card():
        term = ui.input("Expanded term", value=item["term"])
        replacement = ui.input("Replacement", value=item["replacement"])
        def save() -> None:
            new_term = (term.value or "").strip()
            new_replacement = (replacement.value or "").strip()
            if not new_term or not new_replacement:
                ui.notify("Both term and replacement are required", type="warning")
                return
            def mutate(group: dict) -> None:
                if any(x["term"].casefold() == new_term.casefold()
                       and x["term"].casefold() != item["term"].casefold()
                       for x in group["custom"]):
                    raise ValueError("A custom entry with that term already exists")
                group["custom"] = [x for x in group["custom"] if x["term"].casefold() != item["term"].casefold()]
                group["custom"].append({"term": new_term, "replacement": new_replacement, "enabled": item.get("enabled", True)})
            update(mutate)
            dialog.close()
        with ui.row():
            ui.button("Save", on_click=save)
            ui.button("Cancel", on_click=dialog.close).props("flat")
    dialog.open()


def _remove_custom(update, term: str) -> None:
    update(lambda group: group.__setitem__("custom", [x for x in group["custom"] if x["term"].casefold() != term.casefold()]))


def _disable(update, term: str) -> None:
    update(lambda group: group["disabled"].append(term))


def _restore(update, term: str) -> None:
    update(lambda group: group.__setitem__("disabled", [x for x in group["disabled"] if x.casefold() != term.casefold()]))


__all__ = ["render", "PAGE_SIZE"]
