"""The Rules page (/rules): learned rules, abbreviations, sharing."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers
from app_pages.rule_builder_ui import open_rule_builder
from chartcleaner import rule_history, rule_meta

# Rule journal sources shown on Recent changes.
HISTORY_SOURCES = {"selection": "Clean page", "builder": "Rule builder", "batch": "Collected selections",
                   "editor": "My text rules", "quick_add": "Quick add", "import": "Import",
                   "restore": "Backup restore", "preset": "Preset", "rule_pack": "Rule pack",
                   "undo": "Undo", "cli": "Command line", "api": "Integration", "app": "App"}


def render_history(load_current, holder) -> None:
    """Rules → Recent changes: the rule journal, each entry undoable on its own."""
    holder.clear()
    with holder:
        ui.label("Every change to your rules, newest first — from any page, the command line or "
                 "an import. Undo reverses just that change and keeps everything since.") \
            .classes("cc-muted")
        items = rule_history.entries(100)
        if not items:
            ui.label("No rule changes recorded yet.").classes("cc-page-sub")
            return
        for item in items:
            with ui.card().classes("w-full gap-1 p-3" + (" opacity-60" if item.get("reverted") else "")) \
                    .mark("history-row"):
                with ui.row().classes("w-full items-start justify-between gap-3 no-wrap"):
                    with ui.column().classes("min-w-0 gap-0"):
                        ui.label(item["summary"]).classes("text-sm break-all")
                        when = item["ts"].replace("T", " ")[:16]
                        ui.label(f"{when} · {HISTORY_SOURCES.get(item['source'], item['source'])}"
                                 + (" · undone" if item.get("reverted") else "")) \
                            .classes("text-xs opacity-60")
                    if not item.get("reverted"):
                        ui.button("Undo", icon="undo",
                                  on_click=lambda e, it=item: undo_entry(load_current, it, holder)) \
                            .props("flat dense no-caps").mark("history-undo")


def undo_entry(load_current, item: dict, holder) -> None:
    try:
        cfg = rule_history.revert(load_current(), item)
        undone = save_config_with_backup(cfg, source="undo")
        rule_history.mark_reverted(item["id"], by=(undone or {}).get("id"))
    except Exception as ex:
        ui.notify(f"Could not undo: {ex}", type="negative")
        return
    ui.notify("Undone. " + ("" if undone else "(It was already undone.)"), type="positive")
    render_history(load_current, holder)


def _abbreviation_preview_text() -> str:
    """Chart used for abbreviation previews: the Clean page input, else the sample."""
    text = CLEAN_STATE.get("input") or ""
    if text.strip():
        return text
    try:
        return (BASE_DIR / "sample_chart.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


def _report_line(label: str, report) -> str:
    return f"{label}: {report.summary('rule')}"


def render_quick_add(load_current, save_current) -> None:
    """Plain-text bulk entry: one phrase to remove / one ``term = abbreviation`` per line."""
    from chartcleaner import quick_rules

    chart = _abbreviation_preview_text()

    def save(updated: dict, message: str) -> bool:
        try:
            save_current(updated)  # validates first, keeps a backup
        except Exception as exc:
            ui.notify(f"Could not save: {exc}", type="negative")
            return False
        ui.notify(message, type="positive", multi_line=True)
        return True

    ui.label("No regex needed: type or paste one rule per line, check the preview, save. "
             "Rules apply to every future clean (and the Clean page's Remove text / "
             "Abbreviate selection modes save the same rules).").classes("cc-muted")
    with ui.element("div").classes("grid grid-cols-1 lg:grid-cols-2 gap-4 w-full items-start"):
        # ---- Remove text ----
        with ui.card().classes("w-full gap-2").mark("quick-remove"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("backspace").classes("text-red-700")
                ui.label("Remove text").classes("cc-section")
            remove_box = ui.textarea(
                "Text to remove — one phrase per line",
                placeholder="Electronically signed by\nThis note was created using voice recognition\n"
                            "Please see MAR for details") \
                .props("outlined autogrow input-style='min-height: 140px'") \
                .classes("w-full cc-mono").mark("quick-remove-input")
            with ui.row().classes("items-center gap-4"):
                whole = ui.checkbox("Whole words only", value=True)
                case = ui.checkbox("Match case", value=False)
            remove_preview = ui.column().classes("w-full gap-0")

            def refresh_remove() -> None:
                phrases = quick_rules.parse_removals(remove_box.value or "")
                remove_preview.clear()
                with remove_preview:
                    if not phrases:
                        return
                    rows = quick_rules.preview_removals(chart, phrases, whole_words=bool(whole.value),
                                                        case_sensitive=bool(case.value))
                    total = sum(r["hits"] for r in rows)
                    ui.label(f"{len(phrases)} phrase(s) · {total} match(es) in the current chart") \
                        .classes("text-xs cc-muted")
                    for r in rows[:30]:
                        with ui.row().classes("items-center gap-2 no-wrap w-full"):
                            ui.label(str(r["hits"])).classes(
                                "text-xs cc-mono w-8 text-right "
                                + ("text-green-700" if r["hits"] else "opacity-50"))
                            ui.label(r["text"]).classes("text-xs cc-mono truncate")

            def save_removals() -> None:
                phrases = quick_rules.parse_removals(remove_box.value or "")
                if not phrases:
                    ui.notify("Type at least one phrase to remove.", type="info")
                    return
                updated, report = quick_rules.add_removals(
                    load_current(), phrases, whole_words=bool(whole.value),
                    case_sensitive=bool(case.value))
                if not report.changed:
                    ui.notify(report.summary("removal"), type="info")
                    return
                if save(updated, report.summary("removal")):
                    remove_box.set_value("")

            for control in (remove_box, whole, case):
                control.on_value_change(lambda _: refresh_remove())
            ui.button("Save removals", icon="save", on_click=save_removals) \
                .props("unelevated no-caps").mark("quick-remove-save")

        # ---- Add abbreviations ----
        with ui.card().classes("w-full gap-2").mark("quick-abbreviate"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("short_text").classes("text-blue-700")
                ui.label("Add abbreviations").classes("cc-section")
            abbr_box = ui.textarea(
                "One per line: full term = abbreviation",
                placeholder="subarachnoid hemorrhage = SAH\nexternal ventricular drain = EVD\n"
                            "as needed = PRN") \
                .props("outlined autogrow input-style='min-height: 140px'") \
                .classes("w-full cc-mono").mark("quick-abbreviate-input")
            ui.label("Also accepts =>, -> or a tab (paste two columns from a spreadsheet). "
                     "Do Not Use abbreviations are skipped.").classes("text-xs opacity-60")
            abbr_preview = ui.column().classes("w-full gap-0")

            def refresh_abbr() -> None:
                pairs, errors = quick_rules.parse_abbreviations(abbr_box.value or "")
                abbr_preview.clear()
                with abbr_preview:
                    for error in errors[:5]:
                        ui.label(error).classes("text-xs text-orange-700")
                    rows = quick_rules.preview_abbreviations(chart, pairs, load_current())
                    for r in rows[:30]:
                        color = {"ok": "text-green-700", "warn": "text-orange-700",
                                 "blocked": "text-red-700"}[r["status"]]
                        icon = {"ok": "check", "warn": "warning", "blocked": "block"}[r["status"]]
                        with ui.row().classes("items-center gap-2 no-wrap w-full"):
                            ui.icon(icon).classes(f"{color} text-sm")
                            ui.label(f"{r['term']} → {r['abbreviation']}").classes("text-xs cc-mono")
                            ui.label(f"{r['hits']} in chart").classes("text-xs opacity-60")
                        if r["message"]:
                            ui.label(r["message"]).classes(f"text-xs {color} pl-6")

            def save_abbreviations() -> None:
                pairs, errors = quick_rules.parse_abbreviations(abbr_box.value or "")
                if errors:
                    ui.notify(errors[0], type="warning")
                    return
                if not pairs:
                    ui.notify("Type at least one “term = abbreviation”.", type="info")
                    return
                updated, report = quick_rules.add_abbreviations(load_current(), pairs)
                if not report.changed:
                    ui.notify(report.summary("abbreviation"), type="warning" if report.blocked else "info")
                    return
                if save(updated, report.summary("abbreviation")):
                    kept = [f"{t} = {a}" for t, a in pairs if t in report.blocked]
                    abbr_box.set_value("\n".join(kept))

            abbr_box.on_value_change(lambda _: refresh_abbr())
            ui.button("Save abbreviations", icon="save", on_click=save_abbreviations) \
                .props("unelevated no-caps").mark("quick-abbreviate-save")

    # ---- plain-text export / import ----
    with ui.card().classes("w-full gap-2").mark("quick-text-io"):
        ui.label("Your rules as plain text").classes("cc-section")
        ui.label("Readable lists of what you remove, replace and abbreviate — edit them in any text "
                 "editor, email them, and import them back here. (Advanced regex rules travel in the "
                 "JSON export on the Share & import tab.)").classes("cc-muted")
        with ui.row().classes("gap-2 flex-wrap items-center"):
            def download_text() -> None:
                ui.download.content(quick_rules.export_text(load_current()).encode("utf-8"),
                                    "chart-cleaner-rules.txt")

            async def copy_text() -> None:
                text = quick_rules.export_text(load_current())
                await ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text)})")
                ui.notify("Rules copied as plain text.", type="positive")

            ui.button("Download .txt", icon="download", on_click=download_text) \
                .props("outline no-caps").mark("quick-export-txt")
            ui.button("Copy", icon="content_copy", on_click=copy_text).props("flat no-caps")

            async def import_text(event) -> None:
                try:
                    data = await event.file.read() if hasattr(event, "file") else event.content.read()
                    updated, reports, errors = quick_rules.apply_text(
                        load_current(), data.decode("utf-8-sig"))
                except (UnicodeDecodeError, ValueError) as exc:
                    ui.notify(f"Could not read the file: {exc}", type="negative")
                    return
                if errors:
                    ui.notify(f"Nothing imported — {errors[0]}", type="warning")
                    return
                if not any(r.changed for r in reports.values()):
                    ui.notify("Every rule in the file is already saved.", type="info")
                    return
                save(updated, "\n".join((_report_line("Remove", reports["remove"]),
                                         _report_line("Replace", reports["replace"]),
                                         _report_line("Abbreviations", reports["abbreviations"]))))

            ui.upload(label="Import .txt", on_upload=import_text, auto_upload=True) \
                .props("accept=.txt,text/plain flat bordered dense") \
                .classes("w-[240px]").mark("quick-import-txt")


def text_rules_page():
    from chartcleaner.abbreviation_editor import render as render_abbreviations
    from chartcleaner.learned_editor import render_learned_editor
    from chartcleaner.rule_sharing import render_rule_sharing
    from chartcleaner import rule_health, store

    def load_current() -> dict:
        return load_config(common.CONFIG_PATH)

    def render_rule_health(holder: "ui.column") -> None:
        """Surface dead / over-broad / slow regex rules from run history."""
        holder.clear()
        with holder:
            cfg = load_current()
            try:
                runs = store.load_runs()
            except Exception:
                runs = []
            rows = rule_health.report(cfg, runs)
            if not rows:
                return
            flagged = [r for r in rows if r["status"] in ("never matched", "very broad", "slow")]
            ui.separator()
            with ui.expansion(
                f"Rule health ({len(flagged)} need attention of {len(rows)} rules)",
                icon="health_and_safety", value=bool(flagged),
            ).classes("w-full"):
                ui.label(
                    "From your last cleaned charts. 'Never matched' = dead weight; "
                    "'very broad' = deletes a large share of lines (verify it isn't "
                    "removing real content); 'slow' = took long to run. Remove a rule "
                    "here, or scope it on the Pipeline page."
                ).classes("cc-muted")
                cols = [
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    {"name": "stage", "label": "Stage", "field": "stage", "align": "left"},
                    {"name": "rule", "label": "Rule", "field": "rule", "align": "left"},
                    {"name": "hits", "label": "Hits", "field": "hits"},
                    {"name": "runs", "label": "Runs", "field": "runs"},
                    {"name": "last", "label": "Last", "field": "last", "align": "left"},
                ]
                table_rows = [{
                    "status": r["status"],
                    "stage": r["stage"],
                    "rule": (r["pattern"][:90] + ("…" if len(r["pattern"]) > 90 else "")),
                    "hits": r["hits"],
                    "runs": r["runs"],
                    "last": (r["last_hit"][:10] if r["last_hit"] else "—"),
                } for r in rows]
                ui.table(columns=cols, rows=table_rows, row_key="rule").classes("w-full") \
                    .props("flat dense wrap-cells")
                for r in flagged:
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        ui.label(f"[{r['status']}] {r['stage']}: {r['pattern'][:70]}") \
                            .classes("text-xs cc-mono flex-grow whitespace-pre-wrap break-all")
                        if r["risks"]:
                            with ui.row().classes("items-center gap-2 no-wrap"):
                                ui.element("span").classes("cc-dot cc-dot--warn")
                                ui.label("regex risk").classes("text-xs text-orange-600")
                        ui.button("Remove rule", icon="delete",
                                  on_click=lambda e, r=r: remove_health_rule(r, holder)) \
                            .props("flat dense")

    def remove_health_rule(row: dict, holder: "ui.column") -> None:
        cfg = load_current()
        key = row["key"]
        lst = cfg.get(key)
        lst = lst if isinstance(lst, list) else []

        def pattern_at(i: int):
            entry = lst[i]
            return entry[0] if isinstance(entry, (list, tuple)) and entry else entry

        # The table can be stale (the rule editor above saves on its own), so
        # find the rule by pattern instead of trusting the rendered index.
        index = row["index"]
        if not (0 <= index < len(lst) and pattern_at(index) == row["pattern"]):
            index = next((i for i in range(len(lst)) if pattern_at(i) == row["pattern"]), -1)
        if index < 0:
            ui.notify("That rule is no longer in your rules.", type="warning")
        else:
            lst.pop(index)
            if key == "learned_rules":
                cfg = rule_meta.prune(cfg)
            save_config_with_backup(cfg, source="editor")
            ui.notify(f"Removed rule from {key}.", type="positive")
        render_rule_health(holder)

    with shell("My text rules", "/rules", measure=True):
        ui.label("Manage what you remove, replace and abbreviate. Changes save immediately.") \
            .classes("cc-page-sub")
        with ui.tabs().classes("w-full") as tabs:
            quick = ui.tab("Quick add", icon="bolt")
            learned = ui.tab("Remove & replace", icon="find_replace")
            abbreviations = ui.tab("Abbreviations", icon="short_text")
            sharing = ui.tab("Share & import", icon="import_export")
            history = ui.tab("Recent changes", icon="history")
        with ui.tab_panels(tabs, value=quick).classes("w-full") \
                .props("transition-prev=fade transition-next=fade"):
            with ui.tab_panel(quick):
                quick_holder = ui.column().classes("w-full")
            with ui.tab_panel(learned):
                learned_holder = ui.column().classes("w-full")
            with ui.tab_panel(abbreviations):
                abbreviation_holder = ui.column().classes("w-full")
            with ui.tab_panel(sharing):
                sharing_holder = ui.column().classes("w-full")
            with ui.tab_panel(history):
                history_holder = ui.column().classes("w-full")

        def open_builder(row: dict, on_save) -> None:
            info = rule_meta.entry(load_current(), row["pattern"])
            open_rule_builder(row["text"], chart=CLEAN_STATE.get("input") or "", on_save=on_save,
                              initial=info.get("shape"), initial_scope=info.get("scope"),
                              initial_note=info.get("note", ""))

        def save_from_editor(cfg: dict):
            return save_config_with_backup(cfg, source="editor")

        def render_tab(name: str) -> None:
            holder, renderer = {
                "Quick add": (quick_holder, lambda load, _save: render_quick_add(
                    load, lambda cfg: save_config_with_backup(cfg, source="quick_add"))),
                "Remove & replace": (learned_holder, lambda load, save: render_learned_editor(
                    load, save_from_editor, load_runs=store.load_runs, open_builder=open_builder)),
                "Abbreviations": (abbreviation_holder, lambda load, save:
                    render_abbreviations(load, save_from_editor, sample_text=_abbreviation_preview_text)),
                "Share & import": (sharing_holder, lambda load, save:
                    render_rule_sharing(load, lambda cfg: save_config_with_backup(cfg, source="import"),
                                        lambda: open_folder(store.EXPORTS_DIR))),
                "Recent changes": (history_holder, lambda load, _save: render_history(load, history_holder)),
            }[name]
            holder.clear()
            with holder:
                renderer(load_current, save_config_with_backup)
                if name == "Remove & replace":
                    health_holder = ui.column().classes("w-full")
                    render_rule_health(health_holder)

        tabs.on_value_change(lambda e: render_tab(e.value))
        render_tab("Quick add")
        ui.button("Full settings backup", icon="backup",
                  on_click=lambda: ui.navigate.to("/settings")).props("flat")
