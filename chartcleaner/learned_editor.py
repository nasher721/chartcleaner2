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

from . import rule_catalog, rule_examples, rule_meta
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


def _default_runs() -> list[dict]:
    try:
        from . import store
        return store.load_runs()
    except Exception:
        return []


def render_learned_editor(load_current: ConfigLoader, save_current: ConfigSaver, *,
                          load_runs: Callable[[], list[dict]] | None = None,
                          open_builder: Callable[[dict, Callable], Any] | None = None):
    """Render the learned-rule editor and return its root NiceGUI element.

    ``load_current`` is called for every mutation.  This matters when another
    page changes settings while the editor is open: the editor never writes a
    stale snapshot over those changes.  ``save_current`` owns atomic writing
    and backup creation. ``load_runs`` (run history, for hits / last use) and
    ``open_builder(row, on_save)`` (edit a shaped rule in the rule builder) are
    optional.
    """
    load_runs = load_runs or _default_runs
    with ui.column().classes("w-full max-w-5xl gap-3") as root:
        ui.label("Learned rules").classes("text-2xl font-semibold")
        ui.label(
            "Rules learned from highlighted text run on every future Clean. "
            "Pause a rule to keep it without applying it; every change can be undone "
            "under Recent changes."
        ).classes("text-sm opacity-70")

        with ui.row().classes("w-full items-end gap-2"):
            search = ui.input("Search rules", placeholder="text, replacement, note or tag").props(
                "outlined dense clearable"
            ).classes("grow")
            filter_select = ui.select(
                {"all": "All", "remove": "Removals", "replace": "Replacements"},
                value="all",
                label="Show",
            ).props("outlined dense")
            sort_select = ui.select(rule_catalog.SORTS, value="order", label="Sort") \
                .props("outlined dense").mark("rules-sort")
            source_select = ui.select({"all": "Any source", **rule_meta.SOURCE_LABELS}, value="all",
                                      label="Source").props("outlined dense").mark("rules-source")
            status_select = ui.select({"all": "Any status", "paused": "Paused",
                                       "never matched": "Never matched", "very broad": "Very broad",
                                       "slow": "Slow", "ok": "Working"},
                                      value="all", label="Status").props("outlined dense") \
                .mark("rules-status")
            add_button = ui.button("Add rule", icon="add").props("unelevated color=primary")
        tools = ui.row().classes("w-full items-center gap-2")

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
                    fresh = rule_meta.replace(fresh, current[target_index][0], pair, shape=None)
                    current = _pairs(fresh)
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
                fresh = rule_meta.prune(fresh)
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

    def save_or_warn(fresh: dict, message: str) -> bool:
        try:
            ok, error = _save(fresh, save_current)
        except Exception as exc:
            ui.notify(f"Could not save: {exc}", type="negative")
            return False
        if not ok:
            ui.notify(f"Cannot save: {error}", type="negative")
            return False
        ui.notify(message, type="positive")
        render_list()
        return True

    def toggle(row: dict) -> None:
        fresh = load_current()
        if row["pattern"] not in [p[0] for p in _pairs(fresh)]:
            ui.notify("The rule list changed; nothing was changed.", type="warning")
            return
        enable = not rule_meta.is_enabled(fresh, row["pattern"])
        fresh = rule_meta.set_enabled(fresh, [row["pattern"]], enable)
        save_or_warn(fresh, "Rule resumed." if enable else "Rule paused — kept, but not applied.")

    def set_stage(enabled: bool) -> None:
        fresh = load_current()
        fresh.setdefault("stage_options", {}).setdefault("learned_rules", {})["enabled"] = enabled
        save_or_warn(fresh, "Learned rules are on." if enabled else
                     "All learned rules paused — turn them back on here any time.")

    def details_dialog(row: dict) -> None:
        """Note, tags and lines this rule must never change."""
        with ui.context.client.content, ui.dialog() as dialog, \
                ui.card().classes("w-full max-w-xl gap-3").mark("rule-details"):
            ui.label("Rule details").classes("text-lg font-semibold")
            ui.label(row["label"]).classes("text-sm")
            note = ui.input("Note", value=row["note"]).props("outlined dense").classes("w-full")
            tags = ui.input("Tags (comma-separated)", value=", ".join(row["tags"])) \
                .props("outlined dense").classes("w-full")
            ui.label("Lines this rule must never change — checked by Settings → Check my "
                     "learned rules after every rule change.").classes("text-xs opacity-70")
            keeps = [ex for ex in rule_examples.for_rule(row["pattern"]) if ex.get("kind") == "keep"]
            keep_box = ui.column().classes("w-full gap-1")

            def render_keeps() -> None:
                keep_box.clear()
                with keep_box:
                    for ex in keeps:
                        with ui.row().classes("w-full items-center gap-2 no-wrap"):
                            ui.label(ex["text"][:160]).classes("text-xs font-mono flex-grow break-all")
                            ui.button(icon="close", on_click=lambda t=ex["text"]: drop_keep(t)) \
                                .props("flat dense round aria-label='Remove example'")

            def drop_keep(text: str) -> None:
                rule_examples.forget(row["pattern"], text)
                keeps[:] = [ex for ex in keeps if ex["text"] != text]
                render_keeps()

            keep_in = ui.input("Add a line it must keep").props("outlined dense").classes("w-full") \
                .mark("keep-example-input")

            def add_keep() -> None:
                text = (keep_in.value or "").strip()
                if not text:
                    return
                import re
                try:
                    if re.sub(row["pattern"], row["replacement"], text, flags=re.IGNORECASE) != text:
                        ui.notify("This rule already changes that line — edit the rule first.",
                                  type="warning")
                        return
                except re.error:
                    pass
                keeps.append(rule_examples.record_keep(row["pair"], text))
                keep_in.set_value("")
                render_keeps()

            keep_in.on("keydown.enter", add_keep)
            render_keeps()

            def commit() -> None:
                fresh = load_current()
                if row["pattern"] not in [p[0] for p in _pairs(fresh)]:
                    ui.notify("The rule list changed; reopen this rule.", type="warning")
                    return
                tag_list = [t.strip() for t in (tags.value or "").split(",") if t.strip()]
                fresh = rule_meta.update(fresh, row["pattern"], note=(note.value or "").strip() or None,
                                         tags=tag_list or None)
                if save_or_warn(fresh, "Rule details saved."):
                    dialog.close()

            with ui.row().classes("justify-end gap-2"):
                ui.button("Add line", icon="add", on_click=add_keep).props("flat")
                ui.button("Save", icon="save", on_click=commit).props("unelevated color=primary") \
                    .mark("rule-details-save")
                ui.button("Cancel", on_click=dialog.close).props("flat")
        dialog.open()

    def edit_row(row: dict) -> None:
        shaped = isinstance(rule_meta.entry(load_current(), row["pattern"]).get("shape"), dict)
        if shaped and open_builder is not None:
            def on_save(shape, scope, note) -> bool:
                from .rule_shapes import build
                fresh = load_current()
                try:
                    fresh = rule_meta.replace(fresh, row["pattern"], build(shape), shape=shape,
                                              scope=scope, note=note)
                except ValueError as exc:
                    ui.notify(str(exc), type="warning")
                    return False
                return save_or_warn(fresh, "Rule updated.")
            open_builder(row, on_save)
        else:
            edit_dialog(row["index"])

    def archive_unused() -> None:
        fresh, dead = rule_catalog.archive_unused(load_current(), load_runs())
        if not dead:
            ui.notify("Every rule has matched recently — nothing to clean up.", type="info")
            return
        save_or_warn(fresh, f"Paused {len(dead)} rule(s) that never matched (marked archived; "
                     "resume any of them here).")

    def merge_dialog() -> None:
        suggestions = rule_catalog.merge_suggestions(load_current())
        with ui.context.client.content, ui.dialog() as dialog, \
                ui.card().classes("w-full max-w-2xl gap-3").mark("merge-dialog"):
            ui.label("Merge look-alike rules").classes("text-lg font-semibold")
            if not suggestions:
                ui.label("No look-alike rules to merge.").classes("text-sm opacity-70")
            for sug in suggestions:
                with ui.card().classes("w-full gap-1 p-3"):
                    ui.label(sug["label"]).classes("text-sm font-medium")
                    for text in sug["texts"][:5]:
                        ui.label(f"replaces “{text[:120]}”").classes("text-xs font-mono opacity-70")
                    ui.button(f"Merge {len(sug['patterns'])} rules into one", icon="call_merge",
                              on_click=lambda sg=sug: (save_or_warn(
                                  rule_catalog.merge(load_current(), sg), "Rules merged."),
                                  dialog.close())).props("flat").mark("merge-apply")
            ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    def render_tools(config: dict, items: list[dict]) -> None:
        tools.clear()
        stage_on = ((config.get("stage_options") or {}).get("learned_rules") or {}).get("enabled") is not False
        unused = sum(1 for r in items if r["enabled"] and r["status"] == "never matched")
        merges = len(rule_catalog.merge_suggestions(config))
        with tools:
            ui.switch("Apply learned rules", value=stage_on,
                      on_change=lambda e: set_stage(bool(e.value)) if e.value != stage_on else None) \
                .mark("rules-stage-switch").tooltip("Pause every learned rule at once")
            paused = sum(1 for r in items if not r["enabled"])
            if paused:
                ui.label(f"{paused} paused").classes("text-xs opacity-70")
            ui.space()
            ui.button(f"Clean up unused ({unused})", icon="inventory", on_click=archive_unused) \
                .props("flat dense no-caps").mark("rules-archive-unused") \
                .tooltip("Pause the rules that never matched in your recent cleans")
            ui.button(f"Merge look-alikes ({merges})", icon="call_merge", on_click=merge_dialog) \
                .props("flat dense no-caps").mark("rules-merge")

    def render_list() -> None:
        list_box.clear()
        config = load_current()
        try:
            runs = load_runs()
        except Exception:
            runs = []
        items = rule_catalog.rows(config, runs)
        render_tools(config, items)
        shown = rule_catalog.filter_rows(items, query=str(search.value or ""),
                                         kind=filter_select.value or "all",
                                         source=source_select.value or "all",
                                         status=status_select.value or "all",
                                         sort=sort_select.value or "order")
        with list_box:
            if not items:
                with ui.column().classes("w-full items-start gap-3 py-6"):
                    ui.element("div").classes("cc-dotgrid")
                    ui.label("No learned rules yet. Add one here or highlight text on Clean.").classes(
                        "cc-page-sub"
                    )
                return
            if not shown:
                with ui.column().classes("w-full items-start gap-3 py-6"):
                    ui.element("div").classes("cc-dotgrid")
                    ui.label("No rules match this search or filter.").classes("cc-page-sub")
                return
            for row in shown:
                source, repl, generated = _label(row["pair"])
                mode = "Replacement" if row["replacement"] else "Removal"
                with ui.card().classes("w-full gap-1 p-3" + ("" if row["enabled"] else " opacity-60")) \
                        .mark("rule-row"):
                    with ui.row().classes("w-full items-start justify-between gap-3"):
                        with ui.column().classes("min-w-0 gap-0"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(mode).classes(
                                    "text-xs font-semibold uppercase tracking-wide "
                                    + ("text-blue-700" if row["replacement"] else "text-orange-700")
                                )
                                if not row["enabled"]:
                                    ui.badge("Archived" if row["archived"] else "Paused",
                                             color="grey").props("outline")
                                if row["scope"]:
                                    scope = row["scope"]
                                    ui.badge(("only in " if scope["mode"] == "only" else "not in ")
                                             + ", ".join(scope["sections"])[:40]).props("outline")
                                if row["runs"]:
                                    ui.badge(f"{row['hits']} hit(s)"
                                             + (f" · last {row['last_hit'][:10]}" if row["last_hit"] else ""),
                                             color="teal" if row["hits"] else "orange").props("outline")
                                for tag in row["tags"]:
                                    ui.badge(tag, color="purple").props("outline")
                            ui.label(row["label"]).classes("text-sm break-all")
                            ui.label(source).classes("font-mono text-xs opacity-70 break-all")
                            if row["note"]:
                                ui.label(row["note"]).classes("text-xs italic opacity-70")
                            ui.label(("Literal rule" if generated else
                                      "Shaped rule" if row["shape_kind"] != "regex" else "Legacy regex rule")
                                     + (f" · {row['source_label']}" if row["source_label"] else "")
                                     + (f" · added {row['added'][:10]}" if row["added"] else "")
                                     ).classes("text-xs opacity-50")
                        with ui.row().classes("shrink-0"):
                            ui.button(icon="play_circle" if not row["enabled"] else "pause_circle",
                                      on_click=lambda r=row: toggle(r)).props(
                                "flat round dense aria-label='Pause or resume rule'"
                            ).tooltip("Resume rule" if not row["enabled"] else "Pause rule").mark("rule-toggle")
                            ui.button(icon="sticky_note_2", on_click=lambda r=row: details_dialog(r)).props(
                                "flat round dense aria-label='Note, tags and examples'"
                            ).tooltip("Note, tags, lines it must keep").mark("rule-details-open")
                            ui.button(icon="edit", on_click=lambda r=row: edit_row(r)).props(
                                "flat round dense aria-label='Edit rule'"
                            ).tooltip("Edit rule").mark("rule-edit")
                            ui.button(icon="delete", on_click=lambda i=row["index"]: delete_dialog(i)).props(
                                "flat round dense color=negative aria-label='Delete rule'"
                            ).tooltip("Delete rule")

    for control in (search, filter_select, sort_select, source_select, status_select):
        control.on_value_change(lambda _: render_list())
    add_button.on_click(lambda _: edit_dialog())
    render_list()
    return root


__all__ = ["render_learned_editor"]
