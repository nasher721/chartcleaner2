"""NiceGUI editor for bundled and user medical abbreviation rules."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

from .abbreviation_safety import check, is_blocked, needs_override, report
from .abbreviations import SOURCE_PATH, normalize_settings, preview

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


def show_issues(container, issues) -> None:
    """Render safety issues (block = red, warn = amber) into ``container``."""
    from nicegui import ui

    container.clear()
    with container:
        for issue in issues:
            color = "text-negative" if issue.level == "block" else "text-warning"
            icon = "block" if issue.level == "block" else "warning"
            with ui.row().classes("items-start gap-1 no-wrap"):
                ui.icon(icon).classes(color)
                text = issue.message + (f" Use “{issue.use_instead}” instead." if issue.use_instead else "")
                ui.label(text).classes(f"text-sm {color}")


def save_with_safety(term: str, replacement: str, cfg: dict, save: Callable[[bool], None]) -> None:
    """Save directly, or ask for an explicit override when a rule is Do Not Use.

    ``save(acknowledged)`` is called with True only after the user ticks the
    override box.
    """
    from nicegui import ui

    issues = check(term, replacement, cfg)
    if not needs_override(issues):
        save(False)
        for issue in issues:
            ui.notify(issue.message, type="warning")
        return
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-2"):
        ui.label(f"“{term}” → “{replacement}” is a Do Not Use abbreviation").classes("text-lg font-semibold")
        show_issues(ui.column().classes("gap-1"), issues)
        ui.label("Blocked abbreviations are never applied unless you allow them here. "
                 "Your choice is saved with the rule.").classes("text-sm opacity-70")
        understood = ui.checkbox("I understand the risk — use this abbreviation anyway")

        def confirm() -> None:
            dialog.close()
            save(True)

        with ui.row():
            allow = ui.button("Save anyway", on_click=confirm).props("unelevated color=negative")
            allow.bind_enabled_from(understood, "value")
            ui.button("Cancel", on_click=dialog.close).props("flat")
    dialog.open()


def _section_options() -> dict[str, str]:
    from .section_parser import SECTION_TAXONOMY
    return {key: key.replace("_", " ").capitalize() for key in SECTION_TAXONOMY}


def _scope_controls(load_current: Callable[[], dict], update) -> None:
    """Where abbreviations apply: everywhere, only in, or everywhere except sections."""
    from nicegui import ui

    group = normalize_settings((load_current() or {}).get("abbreviations"))
    scope = group.get("scope") or {"mode": "all", "sections": []}
    options = _section_options()
    for name in scope["sections"]:
        options.setdefault(name, name)

    def save() -> None:
        mode, sections = mode_sel.value, list(sections_sel.value or [])
        sections_sel.set_visibility(mode != "all")

        def mutate(g: dict) -> None:
            g["scope"] = {"mode": mode, "sections": sections}
        if mode == "all" or sections:
            update(mutate)

    with ui.row().classes("w-full items-center gap-2"):
        ui.label("Where to abbreviate").classes("font-medium")
        mode_sel = ui.select({"all": "Everywhere", "only": "Only in these sections",
                              "except": "Everywhere except these sections"},
                             value=scope["mode"], on_change=lambda _: save()).classes("min-w-[260px]")
        sections_sel = ui.select(options, multiple=True, value=scope["sections"], label="Sections",
                                 on_change=lambda _: save()).props("use-chips").classes("min-w-[320px] flex-grow")
        sections_sel.set_visibility(scope["mode"] != "all")


def _suggestions_dialog(load_current: Callable[[], dict], update,
                        sample_text: Callable[[], str] | None) -> None:
    from nicegui import ui

    from .phrase_miner import mine

    chart = sample_text() if sample_text else ""
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl gap-2"):
        ui.label("Suggested abbreviations").classes("text-lg font-semibold")
        ui.label("Phrases that repeat in the current chart (or the sample chart), ranked by how much "
                 "text an abbreviation would save. Copied-forward lines count once; names are skipped.") \
            .classes("text-sm opacity-70")
        body = ui.column().classes("w-full gap-1")

        def draw() -> None:
            body.clear()
            found = mine(chart, load_current()) if chart.strip() else []
            with body:
                if not found:
                    ui.label("No repeated phrases found. Paste a longer chart on the Clean page "
                             "and try again.").classes("text-sm")
                for item in found:
                    with ui.row().classes("w-full items-center gap-2 border-b pb-1"):
                        ui.label(f"{item['phrase']}  ×{item['count']}").classes("flex-grow")
                        short = ui.input("Abbreviation", value=item["suggestion"]).props("dense") \
                            .classes("w-28")
                        ui.button("Add", icon="add",
                                  on_click=lambda i=item, c=short: accept(i["phrase"], c.value)).props("flat dense")
                        ui.button("Dismiss", on_click=lambda i=item: dismiss(i["phrase"])) \
                            .props("flat dense color=grey")

        def accept(phrase: str, replacement: str) -> None:
            replacement = (replacement or "").strip()
            if not replacement:
                ui.notify("Enter an abbreviation first.", type="warning")
                return

            def save(acknowledged: bool) -> None:
                def mutate(group: dict) -> None:
                    entry = {"term": phrase, "replacement": replacement, "enabled": True}
                    if acknowledged:
                        entry["acknowledged"] = True
                    group["custom"] = [c for c in group["custom"] if c["term"].casefold() != phrase]
                    group["custom"].append(entry)
                update(mutate)
                draw()

            save_with_safety(phrase, replacement, load_current(), save)

        def dismiss(phrase: str) -> None:
            def mutate(group: dict) -> None:
                group["rejected_suggestions"] = list(group.get("rejected_suggestions", [])) + [phrase]
            update(mutate)
            draw()

        draw()
        ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _packs_dialog(load_current: Callable[[], dict], save_current: Callable[[dict], None],
                  refresh: Callable[[], None]) -> None:
    from nicegui import ui

    from . import abbreviation_packs as packs

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl gap-2"):
        ui.label("Specialty abbreviation packs").classes("text-lg font-semibold")
        ui.label("A pack adds abbreviations that are not in the bundled dictionary. It never "
                 "overwrites your own entries, and Remove takes out only what the pack added.") \
            .classes("text-sm opacity-70")
        body = ui.column().classes("w-full gap-2")

        def act(name: str, install: bool) -> None:
            config = load_current()
            if install:
                config, added, skipped = packs.install(config, name)
                note = f"Added {added} abbreviation(s) from {name}."
                if skipped:
                    note += f" Kept your own entry for {len(skipped)} term(s)."
            else:
                config, removed = packs.uninstall(config, name)
                note = f"Removed {removed} abbreviation(s) from {name}."
            try:
                save_current(config)
            except Exception as error:
                ui.notify(f"Could not save: {error}", type="negative")
                return
            ui.notify(note, type="positive")
            draw()
            refresh()

        def draw() -> None:
            have = packs.installed(load_current())
            body.clear()
            with body:
                for pack in packs.list_packs():
                    with ui.row().classes("w-full items-center border-b pb-2 gap-2"):
                        with ui.column().classes("flex-grow gap-0"):
                            ui.label(pack["name"]).classes("font-medium")
                            ui.label(f"{pack['description']} {pack['count']} abbreviations.") \
                                .classes("text-xs opacity-70")
                        if have.get(pack["name"]):
                            ui.badge(f"{have[pack['name']]} installed", color="positive")
                            ui.button("Remove", on_click=lambda n=pack["name"]: act(n, False)) \
                                .props("flat color=negative")
                        else:
                            ui.button("Install", icon="add",
                                      on_click=lambda n=pack["name"]: act(n, True)).props("flat")

        draw()
        ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _csv_dialog(load_current: Callable[[], dict], save_current: Callable[[dict], None],
                refresh: Callable[[], None]) -> None:
    from nicegui import ui

    from . import abbreviation_packs as packs

    state: dict = {"rows": []}
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl gap-2"):
        ui.label("Import or export your abbreviations").classes("text-lg font-semibold")
        ui.label("Exports your own and pack abbreviations as CSV (opens in Excel or Numbers). "
                 "Import accepts the same columns: Abbreviation, Expanded version, and "
                 "optionally Enabled and Pack.").classes("text-sm opacity-70")

        def export() -> None:
            ui.download.content(packs.export_csv(load_current()).encode("utf-8"),
                                "my-abbreviations.csv")

        ui.button("Export my abbreviations (.csv)", icon="download", on_click=export).props("outline")
        summary = ui.label("").classes("text-sm")
        table_box = ui.column().classes("w-full")

        async def on_upload(event) -> None:
            if hasattr(event, "file"):
                data = await event.file.read()
            else:
                data = event.content.read()
            try:
                rows = packs.preview_import(data.decode("utf-8-sig"), load_current())
            except (UnicodeDecodeError, ValueError) as error:
                ui.notify(str(error), type="negative")
                return
            state["rows"] = rows
            counts: dict[str, int] = {}
            for row in rows:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
            summary.set_text("Preview: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
                             + ". Blocked (Do Not Use) and invalid rows are skipped.")
            table_box.clear()
            with table_box:
                ui.table(columns=[
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    {"name": "term", "label": "Term", "field": "term", "align": "left"},
                    {"name": "replacement", "label": "Abbreviation", "field": "replacement", "align": "left"},
                ], rows=[r for r in rows if r["status"] != "same"][:200], row_key="term") \
                    .classes("w-full").props("dense flat")
            apply_btn.set_enabled(any(r["status"] in ("new", "changed") for r in rows))

        ui.upload(label="Import .csv", on_upload=on_upload, auto_upload=True).props("accept=.csv") \
            .classes("w-full")

        def apply() -> None:
            config, applied = packs.apply_import(load_current(), state["rows"])
            try:
                save_current(config)
            except Exception as error:
                ui.notify(f"Could not save: {error}", type="negative")
                return
            ui.notify(f"Imported {applied} abbreviation(s).", type="positive")
            refresh()
            dialog.close()

        with ui.row():
            apply_btn = ui.button("Apply import", icon="done", on_click=apply).props("unelevated")
            apply_btn.disable()
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _safety_report(load_current: Callable[[], dict]) -> None:
    from nicegui import ui

    rows = report(load_current())
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl gap-2"):
        ui.label("Abbreviation safety report").classes("text-lg font-semibold")
        ui.label("Rules in your dictionary that are on The Joint Commission Do Not Use list or "
                 "ISMP's error-prone list. Blocked rules are not applied; allow one from the "
                 "dictionary list if you really need it.").classes("text-sm opacity-70")
        if not rows:
            ui.label("No Do Not Use abbreviations found.").classes("text-positive")
        else:
            ui.table(columns=[
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
                {"name": "term", "label": "Term", "field": "term", "align": "left"},
                {"name": "abbreviation", "label": "Abbreviation", "field": "abbreviation", "align": "left"},
                {"name": "use", "label": "Use instead", "field": "use_instead", "align": "left"},
                {"name": "source", "label": "Source", "field": "source", "align": "left"},
            ], rows=rows, row_key="term").classes("w-full").props("dense flat")
        ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def _export_dialog(load_current: Callable[[], dict]) -> None:
    from nicegui import ui

    from .expander_export import entries, export_filename, render as render_export

    labels = {"plist": "macOS Text Replacements (.plist)", "espanso": "Espanso (.yml)",
              "textexpander": "TextExpander (.csv)", "ahk": "AutoHotkey v2 for Windows (.ahk)"}
    help_text = {
        "plist": "Drag the file into System Settings → Keyboard → Text Replacements.",
        "espanso": "Put the file in Espanso's match folder (espanso path shows it).",
        "textexpander": "In TextExpander choose File → Import Snippets and pick the file.",
        "ahk": "Double-click the file with AutoHotkey v2 installed (add it to Startup to keep it on).",
    }
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-2"):
        ui.label("Export for a text expander").classes("text-lg font-semibold")
        ui.label("Type the prefix plus an abbreviation anywhere (for example ;sah) and the "
                 "expander types the full term. Do Not Use abbreviations are left out.") \
            .classes("text-sm opacity-70")
        fmt = ui.select(labels, value="plist", label="Format").classes("w-full")
        prefix = ui.input("Prefix typed before each abbreviation", value=";").classes("w-full")
        bundled = ui.switch("Include the bundled dictionary (not only my abbreviations)", value=True)
        how = ui.label(help_text["plist"]).classes("text-sm")
        fmt.on_value_change(lambda e: how.set_text(help_text[e.value]))

        def download() -> None:
            pairs = entries(load_current(), prefix=prefix.value or "",
                            include_bundled=bool(bundled.value))
            if not pairs:
                ui.notify("Nothing to export.", type="warning")
                return
            ui.download.content(render_export(fmt.value, pairs), export_filename(fmt.value))
            ui.notify(f"Exported {len(pairs)} abbreviation(s).", type="positive")

        with ui.row():
            ui.button("Download", icon="download", on_click=download).props("unelevated")
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def render(load_current: Callable[[], dict], save_current: Callable[[dict], None],
           sample_text: Callable[[], str] | None = None):
    """Render a searchable paginated editor using fresh config reads on writes.

    ``sample_text`` returns the chart used for live previews of a new rule.
    """
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
            term_input = ui.input("New expanded term", on_change=lambda _: refresh_preview()).classes("flex-grow")
            replacement_input = ui.input("Replacement", on_change=lambda _: refresh_preview()).classes("flex-grow")
            ui.button("Add custom", icon="add", on_click=lambda: add_custom(term_input, replacement_input))
        preview_box = ui.column().classes("w-full gap-1")
        _scope_controls(load_current, update)
        with ui.row().classes("items-center gap-2"):
            ui.button("Safety report", icon="health_and_safety",
                      on_click=lambda: _safety_report(load_current)).props("flat")
            ui.button("Export for text expander", icon="keyboard",
                      on_click=lambda: _export_dialog(load_current)).props("flat")
            ui.button("Suggest from this chart", icon="auto_awesome",
                      on_click=lambda: _suggestions_dialog(load_current, update, sample_text)).props("flat")
            ui.button("Specialty packs", icon="inventory_2",
                      on_click=lambda: _packs_dialog(load_current, save_current, refresh)).props("flat")
            ui.button("Import / export CSV", icon="table_view",
                      on_click=lambda: _csv_dialog(load_current, save_current, refresh)).props("flat")
        listing = ui.column().classes("w-full gap-2")
        with ui.row().classes("items-center"):
            previous = ui.button("Previous", on_click=lambda: turn(-1)).props("flat")
            page_label = ui.label()
            next_page = ui.button("Next", on_click=lambda: turn(1)).props("flat")

    def change_search(value: str) -> None:
        state["query"] = (value or "").strip().casefold()
        state["page"] = 0
        refresh()

    def refresh_preview() -> None:
        term = (term_input.value or "").strip()
        replacement = (replacement_input.value or "").strip()
        preview_box.clear()
        if not term or not replacement:
            return
        config = load_current()
        with preview_box:
            issues = ui.column().classes("gap-1")
            show_issues(issues, check(term, replacement, config))
            chart = sample_text() if sample_text else ""
            if not chart.strip():
                return
            try:
                result = preview(chart, config, term, replacement)
            except Exception:
                return
            ui.label(f"Would change {result['count']} place(s) in the current chart.") \
                .classes("text-sm font-medium")
            for sample in result["samples"]:
                ui.label(f"{sample['before']}  →  {sample['after']}").classes("text-xs opacity-80 cc-mono")

    def add_custom(term_control, replacement_control) -> None:
        term = (term_control.value or "").strip()
        replacement = (replacement_control.value or "").strip()
        if not term or not replacement:
            ui.notify("Both term and replacement are required", type="warning")
            return

        def save(acknowledged: bool) -> None:
            def mutate(group: dict) -> None:
                group["custom"] = [x for x in group["custom"] if x["term"].casefold() != term.casefold()]
                entry = {"term": term, "replacement": replacement, "enabled": True}
                if acknowledged:
                    entry["acknowledged"] = True
                group["custom"].append(entry)
            update(mutate)
            term_control.value = replacement_control.value = ""
            preview_box.clear()

        save_with_safety(term, replacement, load_current(), save)

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
                _row(item, update, load_current)

    refresh()
    return listing


def _row(item: dict, update: Callable[[Callable[[dict], None]], None],
         load_current: Callable[[], dict]) -> None:
    from nicegui import ui

    blocked = (is_blocked(item["replacement"], item["term"])
               and not (item.get("custom") and item.get("acknowledged")))
    with ui.row().classes("w-full items-center flex-wrap border-b pb-1"):
        ui.label(item["term"]).classes("font-medium min-w-[280px]")
        ui.label("→")
        ui.label(item["replacement"]).classes("min-w-[90px]")
        if blocked:
            ui.badge("Blocked: Do Not Use", color="negative").tooltip(
                " ".join(i.message for i in check(item["term"], item["replacement"]) if i.level == "block"))
        if blocked and not item.get("custom"):
            ui.button("Allow anyway", icon="lock_open",
                      on_click=lambda value=item: _allow(update, load_current, value)).props("flat")
        elif item.get("custom"):
            enabled = item.get("enabled", not item.get("disabled", False))
            ui.switch("Enabled", value=enabled, on_change=lambda event, term=item["term"]: _set_custom_enabled(update, term, event.value))
            ui.button("Edit", icon="edit", on_click=lambda value=item: _edit_custom(update, value, load_current)).props("flat")
            ui.button("Remove", icon="delete", on_click=lambda term=item["term"]: _remove_custom(update, term)).props("flat color=negative")
        elif item.get("disabled"):
            ui.button("Edit", icon="edit", on_click=lambda value=item: _edit_custom(update, value, load_current)).props("flat")
            ui.button("Restore bundled", on_click=lambda term=item["term"]: _restore(update, term)).props("flat")
        else:
            ui.button("Edit", icon="edit", on_click=lambda value=item: _edit_custom(update, value, load_current)).props("flat")
            ui.button("Disable", on_click=lambda term=item["term"]: _disable(update, term)).props("flat")


def _set_custom_enabled(update, term: str, enabled: bool) -> None:
    def mutate(group: dict) -> None:
        for item in group["custom"]:
            if item["term"].casefold() == term.casefold(): item["enabled"] = bool(enabled)
    update(mutate)


def _allow(update, load_current: Callable[[], dict], item: dict) -> None:
    def save(acknowledged: bool) -> None:
        def mutate(group: dict) -> None:
            group["custom"] = [x for x in group["custom"] if x["term"].casefold() != item["term"].casefold()]
            group["custom"].append({"term": item["term"], "replacement": item["replacement"],
                                    "enabled": True, "acknowledged": acknowledged})
        update(mutate)

    save_with_safety(item["term"], item["replacement"], load_current(), save)


def _edit_custom(update, item: dict, load_current: Callable[[], dict]) -> None:
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
            def persist(acknowledged: bool) -> None:
                def mutate(group: dict) -> None:
                    if any(x["term"].casefold() == new_term.casefold()
                           and x["term"].casefold() != item["term"].casefold()
                           for x in group["custom"]):
                        raise ValueError("A custom entry with that term already exists")
                    group["custom"] = [x for x in group["custom"] if x["term"].casefold() != item["term"].casefold()]
                    entry = {"term": new_term, "replacement": new_replacement, "enabled": item.get("enabled", True)}
                    if acknowledged:
                        entry["acknowledged"] = True
                    group["custom"].append(entry)
                update(mutate)
                dialog.close()

            unchanged = (new_term == item["term"] and new_replacement == item["replacement"])
            if unchanged and item.get("acknowledged"):
                persist(True)  # already allowed; don't ask again
            else:
                save_with_safety(new_term, new_replacement, load_current(), persist)
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


__all__ = ["render", "save_with_safety", "show_issues", "PAGE_SIZE"]
