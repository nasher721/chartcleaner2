"""Small UI for managing the rules learned from highlighted chart text.

The editor deliberately keeps the on-disk representation backwards compatible:
``learned_rules`` remains a list of ``[regex, replacement]`` pairs.  New rules
are made by :mod:`chartcleaner.highlight_rules`; legacy regular expressions are
shown and edited as regular expressions so opening this page never changes a
user's existing rule.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

from .engine import validate_config
from .highlight_rules import describe_rule, make_rule


ConfigLoader = Callable[[], dict]
ConfigSaver = Callable[[dict], Any]


def _pairs(config: dict) -> list[list[str]]:
    """Return valid-looking pairs while leaving validation to the engine."""
    pairs = config.get("learned_rules", [])
    return [list(pair) for pair in pairs if isinstance(pair, (list, tuple)) and len(pair) == 2]


def _label(pair: list[str]) -> tuple[str, str, bool]:
    """Return (source, replacement, generated-literal) for display."""
    detail = describe_rule(pair)
    if detail is None:
        return pair[0], pair[1], False
    return str(detail["text"]), str(detail["replacement"]), True


def _ensure_enabled(config: dict) -> bool:
    """Enable learned rules when a config explicitly carries stage options."""
    options = config.setdefault("stage_options", {})
    current = options.setdefault("learned_rules", {})
    changed = current.get("enabled") is not True
    current["enabled"] = True
    return changed


def _save(config: dict, save_current: ConfigSaver) -> tuple[bool, str | None]:
    errors, _ = validate_config(config)
    if errors:
        return False, errors[0]
    save_current(config)
    return True, None


def _current_index(pairs: list[list[str]], original_index: int, target: list[str]) -> int | None:
    """Find a dialog's target after another page may have changed the list."""
    if original_index < len(pairs) and pairs[original_index] == target:
        return original_index
    try:
        return pairs.index(target)
    except ValueError:
        return None


def render_learned_editor(load_current: ConfigLoader, save_current: ConfigSaver):
    """Render the learned-rule editor and return its root NiceGUI element.

    ``load_current`` is called for every mutation.  This matters when another
    page changes settings while the editor is open: the editor never writes a
    stale snapshot over those changes.  ``save_current`` owns atomic writing
    and backup creation.
    """
    with ui.column().classes("w-full max-w-5xl gap-3") as root:
        ui.label("Learned rules").classes("text-2xl font-semibold")
        ui.label(
            "Rules learned from highlighted text run on every future Clean. "
            "New rules use literal matching; existing regex rules stay unchanged."
        ).classes("text-sm opacity-70")

        with ui.row().classes("w-full items-end gap-2"):
            search = ui.input("Search rules", placeholder="text or replacement").props(
                "outlined dense clearable"
            ).classes("grow")
            filter_select = ui.select(
                {"all": "All", "remove": "Removals", "replace": "Replacements"},
                value="all",
                label="Show",
            ).props("outlined dense")
            add_button = ui.button("Add rule", icon="add").props("unelevated color=primary")

        list_box = ui.column().classes("w-full gap-2")

    def notify_saved(enabled: bool) -> None:
        message = "Rule saved."
        if enabled:
            message += " Learned rules were enabled in Pipeline settings."
        ui.notify(message, type="positive")

    def edit_dialog(index: int | None = None) -> None:
        config = load_current()
        pairs = _pairs(config)
        existing = pairs[index] if index is not None and index < len(pairs) else None
        detail = describe_rule(existing) if existing is not None else None
        legacy = existing is not None and detail is None

        with ui.context.client.content, ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
            ui.label("Edit learned rule" if existing is not None else "Add learned rule").classes(
                "text-lg font-semibold"
            )
            if legacy:
                ui.label("Legacy regex rule — its pattern will be preserved as regex.").classes(
                    "text-xs text-orange-700"
                )
                source = ui.textarea("Regular expression", value=existing[0]).props(
                    "outlined input-style='min-height: 80px'"
                ).classes("w-full font-mono")
            else:
                source = ui.textarea(
                    "Text to match", value=(detail["text"] if detail else "")
                ).props("outlined input-style='min-height: 80px'").classes("w-full")
                with ui.row().classes("gap-4"):
                    case_sensitive = ui.checkbox(
                        "Case-sensitive", value=bool(detail and detail.get("case_sensitive"))
                    )
                    whole_words = ui.checkbox(
                        "Whole words", value=bool(detail is None or detail.get("whole_words", True))
                    )

            replacement = ui.input(
                "Replacement (leave empty to remove)",
                value=(detail["replacement"] if detail else (existing[1] if existing else "")),
            ).props("outlined dense").classes("w-full")
            if legacy:
                ui.label("Replacement is literal text; the legacy pattern remains regex.").classes(
                    "text-xs opacity-60"
                )

            def commit() -> None:
                text = str(source.value or "")
                repl = str(replacement.value or "")
                if not text.strip():
                    ui.notify("Enter text or a regular expression first.", type="warning")
                    return
                if legacy:
                    pair = [text, repl]
                else:
                    pair = list(
                        make_rule(
                            text,
                            repl,
                            case_sensitive=bool(case_sensitive.value),
                            whole_words=bool(whole_words.value),
                        )
                    )
                fresh = load_current()
                current = _pairs(fresh)
                duplicate = any(
                    candidate == pair and (index is None or position != index)
                    for position, candidate in enumerate(current)
                )
                if duplicate:
                    ui.notify(
                        "That exact rule already exists; no unrelated rule was changed.",
                        type="warning",
                    )
                    return
                if index is None:
                    current.append(pair)
                else:
                    target_index = _current_index(current, index, existing or [])
                    if target_index is None:
                        ui.notify("The rule list changed; reopen this dialog and try again.", type="warning")
                        return
                    current[target_index] = pair
                fresh["learned_rules"] = current
                enabled = _ensure_enabled(fresh)
                try:
                    ok, error = _save(fresh, save_current)
                except Exception as exc:  # keep UI responsive on save failures
                    ui.notify(f"Could not save rule: {exc}", type="negative")
                    return
                if not ok:
                    ui.notify(f"Cannot save rule: {error}", type="negative")
                    return
                dialog.close()
                notify_saved(enabled)
                render_list()

            with ui.row().classes("justify-end gap-2"):
                ui.button("Save", icon="save", on_click=commit).props("unelevated color=primary")
                ui.button("Cancel", on_click=dialog.close).props("flat")
        dialog.open()

    def delete_dialog(index: int) -> None:
        config = load_current()
        pairs = _pairs(config)
        if index >= len(pairs):
            return
        source, repl, _ = _label(pairs[index])
        with ui.context.client.content, ui.dialog() as dialog, ui.card().classes("w-full max-w-md gap-3"):
            ui.label("Delete this learned rule?").classes("text-lg font-semibold")
            ui.label(source).classes("font-mono text-sm break-all")
            ui.label("This removes the saved rule immediately.").classes("text-xs opacity-60")

            def confirm() -> None:
                fresh = load_current()
                current = _pairs(fresh)
                target_index = _current_index(current, index, pairs[index])
                if target_index is None:
                    dialog.close()
                    ui.notify("The rule list changed; reopen this dialog and try again.", type="warning")
                    return
                del current[target_index]
                fresh["learned_rules"] = current
                try:
                    ok, error = _save(fresh, save_current)
                except Exception as exc:
                    ui.notify(f"Could not delete rule: {exc}", type="negative")
                    return
                if not ok:
                    ui.notify(f"Cannot delete rule: {error}", type="negative")
                    return
                dialog.close()
                ui.notify("Rule deleted.", type="positive")
                render_list()

            with ui.row().classes("justify-end gap-2"):
                ui.button("Delete", icon="delete", on_click=confirm).props("unelevated color=negative")
                ui.button("Cancel", on_click=dialog.close).props("flat")
        dialog.open()

    def render_list() -> None:
        list_box.clear()
        config = load_current()
        pairs = _pairs(config)
        query = str(search.value or "").strip().casefold()
        kind = filter_select.value or "all"
        shown: list[tuple[int, list[str]]] = []
        for index, pair in enumerate(pairs):
            source, repl, _ = _label(pair)
            mode = "replace" if pair[1] else "remove"
            if kind != "all" and mode != kind:
                continue
            if query and query not in f"{source} {repl}".casefold():
                continue
            shown.append((index, pair))
        with list_box:
            if not pairs:
                ui.label("No learned rules yet. Add one here or highlight text on Clean.").classes(
                    "rounded border border-dashed p-5 text-sm opacity-70"
                )
                return
            if not shown:
                ui.label("No rules match this search or filter.").classes(
                    "rounded border border-dashed p-5 text-sm opacity-70"
                )
                return
            for index, pair in shown:
                source, repl, generated = _label(pair)
                mode = "Replacement" if pair[1] else "Removal"
                with ui.card().classes("w-full gap-1 p-3"):
                    with ui.row().classes("w-full items-start justify-between gap-3"):
                        with ui.column().classes("min-w-0 gap-0"):
                            ui.label(mode).classes(
                                "text-xs font-semibold uppercase tracking-wide "
                                + ("text-blue-700" if pair[1] else "text-orange-700")
                            )
                            ui.label(source).classes("font-mono text-sm break-all")
                            ui.label(f"→ {repl}" if pair[1] else "Removed").classes(
                                "text-sm opacity-70 break-all"
                            )
                            ui.label("Literal rule" if generated else "Legacy regex rule").classes(
                                "text-xs opacity-50"
                            )
                        with ui.row().classes("shrink-0"):
                            ui.button(icon="edit", on_click=lambda i=index: edit_dialog(i)).props(
                                "flat round dense aria-label='Edit rule'"
                            ).tooltip("Edit rule")
                            ui.button(icon="delete", on_click=lambda i=index: delete_dialog(i)).props(
                                "flat round dense color=negative aria-label='Delete rule'"
                            ).tooltip("Delete rule")

    search.on_value_change(lambda _: render_list())
    filter_select.on_value_change(lambda _: render_list())
    add_button.on_click(lambda _: edit_dialog())
    render_list()
    return root


__all__ = ["render_learned_editor"]
