"""The Census page (/census): every bed at a glance, for pre-rounds."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers
from chartcleaner import census, recent_charts
from chartcleaner.appstate import CLEAN_STATE


def census_page():
    state: dict = {"running": False, "texts": {}, "report": None}

    async def refresh() -> None:
        if state["running"]:
            return
        state["running"] = True
        spinner.set_visibility(True)
        try:
            records = await run.io_bound(recent_charts.load)
            state["texts"] = {}
            for rec in records:  # newest first
                tag = recent_charts.clean_tag(rec.get("tag"))
                if tag and tag.casefold() not in state["texts"]:
                    state["texts"][tag.casefold()] = rec["text"]
            cfg = load_config(common.CONFIG_PATH)
            report = await run.io_bound(census.build, cfg, records=records)
            state["report"] = report
            draw(report)
        finally:
            state["running"] = False
            spinner.set_visibility(False)

    def open_in_clean(tag: str) -> None:
        text = state["texts"].get(tag.casefold())
        if text is None:
            return
        CLEAN_STATE.update(input=text, tag=tag, result=None, review_html=None, result_text="")
        ui.navigate.to("/")

    def copy_all() -> None:
        report = state.get("report")
        if report is None or report.empty:
            ui.notify("Nothing to copy yet.")
            return
        copy_to_clipboard(report.to_text(), "Census copied")

    def copy_markdown() -> None:
        report = state.get("report")
        if report is None or report.empty:
            ui.notify("Nothing to copy yet.")
            return
        copy_to_clipboard(to_markdown(report.to_text(), source="Census"),
                          "Census Markdown copied")

    def download(fmt: str) -> None:
        report = state.get("report")
        if report is None or report.empty:
            ui.notify("Nothing to download yet.")
            return
        try:
            name = f"census_{time.strftime('%Y%m%d_%H%M%S')}.{fmt}"
            if fmt == "md":
                content = to_markdown(report.to_text(), source="Census")
            else:
                content = report.to_text()
            path = store.EXPORTS_DIR / name
            store.ensure_dir(store.EXPORTS_DIR)
            path.write_text(content, encoding="utf-8")
            download_file(f"/exports/{name}", name)
        except Exception as ex:
            report_error("Census download failed", ex)

    def draw(report) -> None:
        cards.clear()
        n = len(report.beds)
        look = len([b for b in report.beds if b.attention])
        summary.set_text(f"{n} bed{'s' if n != 1 else ''}" + (f" · {look} with something to look at"
                                                              if look else "") if n else "No beds yet")
        with cards:
            if report.empty:
                with ui.card().classes("w-full cc-panel").mark("census-empty"):
                    ui.label("No bed-tagged charts yet.").classes("cc-section")
                    if not recent_charts.settings(common.PREFS)["enabled"]:
                        ui.label("Keeping recent charts is switched off (Settings → Recent charts); "
                                 "the census is built from them.").classes("text-sm")
                    ui.label("On the Clean page, type a bed tag (e.g. G20-1) next to the chart before "
                             "cleaning. The newest chart for each tag shows up here.").classes("text-sm")
                return
            with ui.element("div").classes(
                    "w-full grid gap-3 md:grid-cols-2 2xl:grid-cols-3 cc-stagger"):
                for bed in report.beds:
                    bed_card(bed)

    def bed_card(bed) -> None:
        with ui.card().classes("w-full gap-1").mark(f"census-bed-{bed.tag}"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label(bed.tag).classes("cc-section whitespace-nowrap")
                if bed.updated:
                    ui.label(f"cleaned {bed.updated}").classes("text-xs opacity-60 whitespace-nowrap")
                ui.space()
                ui.button("Open", icon="open_in_new", on_click=lambda t=bed.tag: open_in_clean(t)) \
                    .props("flat dense").mark(f"census-open-{bed.tag}")
                ui.button(icon="content_copy", on_click=lambda b=bed: copy_to_clipboard(b.to_text())) \
                    .props("flat dense").tooltip("Copy this bed")
            if bed.error:
                status_tag("could not read", "danger", tooltip=bed.error)
                return
            with ui.row().classes("w-full gap-2 flex-wrap"):
                for label, name, value in (("Allergies", "Allergies", bed.allergies),
                                           ("Code", "Code status", bed.code_status)):
                    removed = f"{name}: removed by a cleaning rule" in bed.safety_flags
                    shown = "removed by a rule" if removed and not value else (value or "not stated")
                    status = "danger" if removed else ("ok" if value else "warn")
                    status_tag(f"{label}: {shown}"[:60], status, tooltip=value or None)
            for flag in bed.safety_flags:
                if not flag.endswith("removed by a cleaning rule"):
                    ui.label(f"! {flag}").classes("text-xs text-red-600 break-words")
            for why in bed.conflicts:
                ui.label(f"! contradiction: {why}").classes("text-xs text-red-600 break-words")
            if bed.antibiotics:
                ui.label("Abx: " + "; ".join(bed.antibiotics)).classes("text-xs cc-mono")
            if bed.devices_review:
                ui.label("Review device: " + "; ".join(bed.devices_review)).classes("text-xs cc-mono")
            with ui.row().classes("gap-2 flex-wrap"):
                if bed.bundle_gaps:
                    status_tag(f"{len(bed.bundle_gaps)} bundle items not mentioned", "warn",
                               tooltip=", ".join(bed.bundle_gaps))
                if bed.stale:
                    status_tag(f"{bed.stale} possibly stale", "warn")
            if bed.todo:
                with ui.column().classes("w-full gap-0 mt-1"):
                    for item in bed.todo:
                        ui.checkbox(item).props("dense").classes("text-xs")
                    if bed.todo_total > len(bed.todo):
                        ui.label(f"… {bed.todo_total - len(bed.todo)} more").classes("text-xs opacity-60")
            if bed.delta_total:
                since = f" since {bed.delta_since}" if bed.delta_since else ""
                ui.label(f"~ {bed.delta_total} new/changed line{'s' if bed.delta_total != 1 else ''}"
                         f"{since}").classes("text-xs font-semibold")
                for line in bed.delta:
                    ui.label(f"+ {line}").classes("text-xs cc-mono break-words")
                if bed.delta_total > len(bed.delta):
                    ui.label(f"… {bed.delta_total - len(bed.delta)} more").classes("text-xs opacity-60")

    with shell("Census — every bed at a glance", "/census"):
        ui.label("The newest chart for each bed tag, cleaned with your saved rules: allergies, code "
                 "status, possible contradictions, antibiotics, devices to review, ICU bundle gaps and "
                 "the to-do list. Charts stay encrypted on this computer and follow your retention "
                 "setting. Ticks are not saved.").classes("cc-page-sub")
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            summary = ui.label("Loading…").classes("text-lg").mark("census-summary")
            spinner = ui.spinner("dots", size="md")
            ui.space()
            ui.button("Refresh", icon="refresh", on_click=lambda: refresh()).props("flat") \
                .mark("census-refresh")
            ui.button("Copy census", icon="content_copy", on_click=copy_all).props("flat") \
                .mark("census-copy")
            ui.button("Copy Markdown", icon="markdown", on_click=copy_markdown).props("flat") \
                .mark("census-copy-md")
            ui.button("Download .txt", icon="download",
                      on_click=lambda: download("txt")).props("flat").mark("census-dl-txt")
            ui.button("Download .md", icon="download",
                      on_click=lambda: download("md")).props("flat").mark("census-dl-md")
        cards = ui.column().classes("w-full gap-3")
    ui.timer(0.05, refresh, once=True)
