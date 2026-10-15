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
                ).classes("text-sm opacity-70")
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
                            ui.label("⚠ regex risk").classes("text-xs text-orange-600")
                        ui.button("Remove rule", icon="delete",
                                  on_click=lambda e, r=r: remove_health_rule(r, holder)) \
                            .props("flat dense")

    def remove_health_rule(row: dict, holder: "ui.column") -> None:
        cfg = load_current()
        key = row["key"]
        lst = cfg.get(key)
        if isinstance(lst, list) and 0 <= row["index"] < len(lst):
            lst.pop(row["index"])
            save_config_with_backup(cfg)
            ui.notify(f"Removed rule from {key}.", type="positive")
        render_rule_health(holder)

    with shell("My text rules", "/rules", measure=True):
        ui.label("Manage what you remove, replace and abbreviate. Changes save immediately.") \
            .classes("cc-page-sub")
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
                if name == "Remove & replace":
                    health_holder = ui.column().classes("w-full")
                    render_rule_health(health_holder)

        tabs.on_value_change(lambda e: render_tab(e.value))
        render_tab("Remove & replace")
        ui.button("Full settings backup", icon="backup",
                  on_click=lambda: ui.navigate.to("/settings")).props("flat")
