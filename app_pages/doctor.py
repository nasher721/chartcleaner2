"""The Doctor page (/doctor): is everything Chart Cleaner needs in place?"""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers
from chartcleaner import doctor

_ICONS = {"ok": ("check_circle", "green"), "warn": ("warning", "orange"),
          "fail": ("error", "red"), "info": ("info", "grey")}


def doctor_page():
    state = {"running": False, "deep": False}

    async def run_checks(deep: bool = False) -> None:
        if state["running"]:
            return
        state["running"] = True
        spinner.set_visibility(True)
        try:
            checks = await run.io_bound(doctor.run_checks, deep=deep,
                                        ai=not os.environ.get("NICEGUI_USER_SIMULATION"))
            draw(checks)
        finally:
            state["running"] = False
            spinner.set_visibility(False)

    async def fix(check) -> None:
        ok, msg = await run.io_bound(doctor.apply_fix, check.fix)
        ui.notify(msg, type="positive" if ok else "negative", multi_line=True)
        await run_checks()

    def draw(checks) -> None:
        summary.set_text(doctor.summary(checks))
        rows.clear()
        with rows:
            for c in checks:
                icon, color = _ICONS.get(c.status, _ICONS["info"])
                with ui.row().classes("w-full items-center gap-3 no-wrap border-b pb-1") \
                        .mark(f"doctor-{c.id}"):
                    ui.icon(icon, color=color).classes("text-xl")
                    ui.label(c.label).classes("font-medium w-60")
                    ui.label(c.detail).classes("text-sm flex-grow break-all")
                    if c.fix:
                        ui.button(c.fix_label or "Fix", icon="build",
                                  on_click=lambda c=c: fix(c)).props("outline dense") \
                            .mark(f"doctor-fix-{c.fix}")

    with shell("Doctor — is everything in place?", "/doctor"):
        ui.label("Checks Python, packages, the spaCy model, local AI, encryption, your rules, the "
                 "API token, launchers, the data folder and update signing. Problems that can be "
                 "repaired from here get a button.").classes("opacity-70")
        with ui.row().classes("w-full items-center gap-2"):
            summary = ui.label("Checking…").classes("text-lg font-semibold").mark("doctor-summary")
            spinner = ui.spinner("dots", size="md")
            ui.space()
            ui.button("Check again", icon="refresh", on_click=lambda: run_checks()).props("flat")
            ui.button("Deep check (load the NLP engine)", icon="psychology",
                      on_click=lambda: run_checks(deep=True)).props("flat").mark("doctor-deep")
        rows = ui.column().classes("w-full gap-1")
        ui.timer(0.1, run_checks, once=True)
