"""Task-centered Review workspace composed from existing review evidence."""

from collections.abc import Callable
from dataclasses import dataclass

from nicegui import ui

from app_pages.review_model import build_finding_rows, default_review_view, filter_findings


def collapse_context_ranges(line_count: int, changed_lines: set[int], context: int = 3) -> list[tuple[int, int]]:
    visible = set(changed_lines)
    for line in changed_lines:
        visible.update(range(max(1, line - context), min(line_count, line + context) + 1))
    ranges = []
    start = None
    for line in range(1, line_count + 2):
        if line not in visible and line <= line_count:
            start = line if start is None else start
            continue
        if start is not None:
            if line - start >= 2:
                ranges.append((start, line - 1))
            start = None
    return ranges


@dataclass(frozen=True)
class ReviewSections:
    diff: Callable[[], None]
    removed: Callable[[], None]
    abbreviations: Callable[[], None]
    diagnostics: Callable[[], None]


def render_review_workspace(*, result, audit, source, output, decision,
                            sections: ReviewSections, locate, change_count: int = 0) -> None:
    rows = build_finding_rows(result, audit, source=source, output=output)
    if decision.status == "blocked":
        with ui.row().classes("w-full items-center gap-2 cc-tint cc-tint--danger p-3") \
                .mark("blocking-banner"):
            ui.icon("report")
            ui.label("Release blocked — resolve the blocking findings before copying.")

    finding_count = sum(row.count for row in rows)
    with ui.tabs().props("dense align=left") as tabs:
        findings_tab = ui.tab(f"Findings ({finding_count})", icon="rule").mark("review-findings-tab")
        changes_tab = ui.tab("Changes", icon="difference").mark("review-changes-tab")
        diagnostics_tab = ui.tab("Diagnostics", icon="account_tree").mark("review-diagnostics-tab")
    initial = findings_tab if default_review_view(rows) == "findings" else changes_tab
    with ui.tab_panels(tabs, value=initial).props("keep-alive").classes("w-full"):
        with ui.tab_panel(findings_tab):
            initial_filter = "review" if filter_findings(rows, "review") else "all"
            filter_control = ui.toggle(
                {"all": "All", "blocking": "Blocking", "review": "Needs review", "info": "Informational"},
                value=initial_filter).props("dense no-caps").mark("finding-filter")
            listing = ui.column().classes("w-full gap-2")

            def draw_findings() -> None:
                listing.clear()
                visible = filter_findings(rows, filter_control.value)
                with listing:
                    if not visible:
                        message = ("No issues detected by these checks—review before sharing."
                                   if not rows else "No findings match this filter.")
                        ui.label(message).classes("cc-muted")
                    for row in visible:
                        with ui.card().classes("w-full gap-1").mark("finding-row"):
                            ui.badge({"blocked": "Blocked", "review": "Review", "info": "Info"}[row.severity],
                                     color={"blocked": "red", "review": "orange", "info": "grey"}[row.severity])
                            ui.label(row.title).classes("cc-section")
                            ui.label(f"{row.detail} · {row.count} occurrence(s)").classes("cc-muted")
                            for occurrence in row.occurrences:
                                ui.button("Locate", icon="my_location",
                                          on_click=lambda _e, item=occurrence: locate(item)) \
                                    .props("flat dense no-caps").mark("locate-finding")
            filter_control.on_value_change(lambda _e: draw_findings())
            draw_findings()
        with ui.tab_panel(changes_tab):
            with ui.tabs().props("dense align=left") as change_tabs:
                changed = ui.tab("Changed")
                removed = ui.tab("Removed")
                abbreviated = ui.tab("Abbreviations")
            with ui.tab_panels(change_tabs, value=changed).props("keep-alive").classes("w-full"):
                with ui.tab_panel(changed):
                    def navigate_changes(direction: int) -> None:
                        ui.run_javascript(f"""
(() => {{
  const nav = document.querySelector('.cc-diff-navigation');
  const rows = Array.from(document.querySelectorAll('.cc-diff-change[data-diff-line]'));
  const label = nav?.querySelector('.cc-change-position');
  if (!nav || !label) return;
  if (!rows.length) {{ label.textContent = '0 / 0'; return; }}
  const current = Number(nav.dataset.changeIndex || 0);
  const index = (current + {direction} + rows.length) % rows.length;
  nav.dataset.changeIndex = String(index);
  rows[index].scrollIntoView({{block: 'center', behavior: 'instant'}});
  rows[index].setAttribute('tabindex', '-1');
  rows[index].focus({{preventScroll: true}});
  label.textContent = `${{index + 1}} / ${{rows.length}}`;
}})();
""")

                    with ui.row().classes("w-full items-center gap-2 cc-diff-navigation") as navigation:
                        navigation.props(f"data-change-index={0 if change_count else -1}")
                        ui.button("Previous", icon="chevron_left", on_click=lambda: navigate_changes(-1)) \
                            .props("flat dense no-caps").classes("cc-change-previous").mark("change-previous")
                        ui.button("Next", icon="chevron_right", on_click=lambda: navigate_changes(1)) \
                            .props("flat dense no-caps").classes("cc-change-next").mark("change-next")
                        ui.label(f"{1 if change_count else 0} / {change_count}") \
                            .classes("cc-muted text-xs cc-change-position") \
                            .props("aria-live=polite aria-label='Change position'").mark("change-position")
                    sections.diff()
                with ui.tab_panel(removed):
                    sections.removed()
                with ui.tab_panel(abbreviated):
                    sections.abbreviations()
        with ui.tab_panel(diagnostics_tab):
            sections.diagnostics()
