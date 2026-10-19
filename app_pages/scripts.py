"""The Scripts page (/scripts): custom Python cleaning rules."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers


def scripts_page():
    sel = {"name": None}
    list_holder: dict = {}
    editor_holder: dict = {}

    def build_list() -> None:
        metas = list_custom_rules(common.CUSTOM_DIR)
        if not metas:
            ui.label("No scripts yet.").classes("text-xs opacity-60")
        for m in metas:
            color = "primary" if m["name"] == sel["name"] else "grey-7"
            ui.button(m["label"], icon="description",
                      on_click=lambda n=m["name"]: select_file(n)) \
                .props(f"flat no-caps align=left color={color}").classes("w-full text-xs")
            if m.get("error"):
                ui.badge("load error", color="negative").classes("ml-4")
            if m.get("placeholder"):
                ui.badge("placeholder", color="blue-grey").props("outline").classes("ml-4")
            if not m.get("trusted") and not m.get("error") and not m.get("placeholder"):
                ui.badge("untrusted", color="orange").props("outline").classes("ml-4")

    def build_editor() -> None:
        if not sel["name"]:
            ui.label("Select a script, or create a new one.").classes("opacity-60")
            return
        path = common.CUSTOM_DIR / f"{sel['name']}.py"
        if not path.exists():
            ui.label("File no longer exists.").classes("opacity-60")
            return
        content = {"text": path.read_text(encoding="utf-8")}
        metas = {m["name"]: m for m in list_custom_rules(common.CUSTOM_DIR)}
        meta = metas.get(sel["name"], {})

        ui.label(f"{sel['name']}.py").classes("font-mono font-semibold")
        if meta.get("description"):
            ui.label(meta["description"]).classes("text-xs cc-muted -mt-2")
        if meta.get("error"):
            with ui.row().classes("items-center gap-2"):
                ui.element("span").classes("cc-dot cc-dot--neg")
                ui.label(f"Load error: {meta['error']}").classes("text-xs text-red-500")
        if meta.get("placeholder"):
            with ui.row().classes("items-center gap-2"):
                ui.element("span").classes("cc-dot cc-dot--info")
                ui.label("Placeholder script — skipped by the pipeline until you set "
                         "PLACEHOLDER = False.").classes("text-xs cc-muted")

        if not meta.get("trusted") and not meta.get("placeholder"):
            def trust_this() -> None:
                try:
                    trust_rule(path)
                except Exception as ex:  # noqa: BLE001 — surface, never crash the page
                    ui.notify(f"Could not trust the script: {ex}", type="negative")
                    return
                ui.notify(f"{sel['name']}.py trusted — it will now run in the pipeline.",
                          type="positive")
                select_file(sel["name"])

            with ui.card().classes("w-full cc-tint cc-tint--warn gap-2"):
                status_tag("untrusted", "warn")
                ui.label("This file was not created or saved through this app, so it has "
                         "not been approved to run. Read it above, then trust it — the "
                         "approval is tied to these exact contents and is revoked if the "
                         "file changes.").classes("text-xs")
                ui.button("Trust this script", icon="verified_user", on_click=trust_this) \
                    .props("unelevated color=primary")

        editor = ui.textarea(value=content["text"], on_change=lambda e: content.update(text=e.value))
        editor.props("outlined input-style='min-height: 400px'").classes("w-full cc-mono")
        status = ui.label("").classes("text-xs")

        def save_script() -> None:
            code = content["text"]
            try:
                compile(code, str(path), "exec")
            except SyntaxError as ex:
                status.set_text(f"Syntax error: line {ex.lineno}: {ex.msg}") \
                    .classes("text-red-500 text-xs", remove="text-green-600 text-orange-600")
                return
            path.write_text(code, encoding="utf-8")
            try:
                # Saving here is an explicit act of authorship: approve these bytes.
                trust_rule(path)
            except Exception:  # noqa: BLE001 — trust is best-effort, the save stands
                pass
            m2 = {mm["name"]: mm for mm in list_custom_rules(common.CUSTOM_DIR)}.get(sel["name"], {})
            if m2.get("error"):
                status.set_text(f"Saved, but the script has problems: {m2['error']}") \
                    .classes("text-red-500 text-xs", remove="text-green-600 text-orange-600")
            elif m2.get("placeholder"):
                status.set_text("Saved. Still a placeholder (PLACEHOLDER = True).") \
                    .classes("text-orange-600 text-xs", remove="text-red-500 text-green-600")
            else:
                status.set_text("Saved and loadable.") \
                    .classes("text-green-600 text-xs", remove="text-red-500 text-orange-600")
                ui.notify("Script saved.", type="positive")

        def test_script() -> None:
            def work():
                try:
                    mod = _load_custom_module(path)
                    clean = getattr(mod, "clean", None)
                    if not callable(clean):
                        return "err", "No clean(text, ctx) function."
                    ctx = CleanContext(load_config(common.CONFIG_PATH))
                    sample = PIPE_TEST["text"] or ("Patient John Doe, MRN 123456, phone (555) 010-2030.\n\n"
                                                  "Follow-up imaging plan tomorrow morning at nine.")
                    out = clean(sample, ctx)
                    return ("ok", f"OK — {len(sample):,} → {len(out):,} chars. "
                            f"counters={ctx.counters} logs={ctx.logs[:3]}\n\nOUTPUT PREVIEW:\n{out[:800]}")
                except Exception as ex:
                    return "err", f"{type(ex).__name__}: {ex}"

            async def go() -> None:
                status.set_text("Testing…")
                kind, msg = await run.io_bound(work)
                ok = kind == "ok"
                cls = "text-green-600 text-xs" if ok else "text-red-500 text-xs"
                other = "text-red-500 text-xs" if ok else "text-green-600 text-xs"
                status.set_text(msg).classes(cls, remove=other)

            asyncio.get_running_loop().create_task(go())

        def del_script() -> None:
            confirm_dialog(f"Delete {sel['name']}.py permanently?",
                           lambda: (path.unlink(missing_ok=True), ui.navigate.to("/scripts")))

        with ui.row().classes("gap-2 flex-wrap"):
            ui.button("Save", icon="save", on_click=save_script).props("unelevated color=primary")
            ui.button("Run against test text", icon="play_arrow", on_click=test_script).props("outline")
            ui.button("Revert (reload from disk)", icon="undo",
                      on_click=lambda: (editor_holder["box"].clear(),
                                        with_editor(build_editor))).props("flat")
            ui.button("Delete script", icon="delete", on_click=del_script).props("flat color=negative")

        with ui.expansion("Script API reference", icon="menu_book").classes("w-full"):
            ui.markdown(
                "```python\n"
                "def clean(text: str, ctx) -> str:\n"
                '    ctx.count("name", n)   # counter for the Statistics tracker\n'
                '    ctx.log("message")     # note shown in run details\n'
                "    ctx.config             # read-only view of config.json\n"
                "    ...\n"
                "    return text            # always return the new text\n"
                "```\n"
                "Top-of-file options: `LABEL`, `DESCRIPTION`, `PLACEHOLDER = True/False`.\n"
                "Any stdlib (re, json, …) and any installed package (thefuzz, presidio, …) can be imported.\n"
                "Enable/disable and reorder this rule on the **Pipeline & Rules** page."
            ).classes("text-sm")

    def with_editor(fn) -> None:
        with editor_holder["box"]:
            fn()

    def select_file(name: str | None) -> None:
        sel["name"] = name
        list_holder["box"].clear()
        with list_holder["box"]:
            build_list()
        editor_holder["box"].clear()
        with editor_holder["box"]:
            build_editor()

    def new_script_dialog() -> None:
        with ui.dialog() as dlg, ui.card():
            ui.label("New script name (snake_case):")
            name_in = ui.input(value="", placeholder="my_new_rule")
            err = ui.label("").classes("text-red-500 text-xs")

            def create() -> None:
                name = (name_in.value or "").strip()
                if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                    err.set_text("Use lowercase letters, digits, _ (must start with a letter).")
                    return
                target = common.CUSTOM_DIR / f"{name}.py"
                if target.exists():
                    err.set_text("A file with that name already exists.")
                    return
                target.write_text(CUSTOM_RULE_TEMPLATE, encoding="utf-8")
                try:
                    trust_rule(target)  # created by the app, so it starts approved
                except Exception:  # noqa: BLE001
                    pass
                dlg.close()
                ui.notify(f"Created {name}.py — a placeholder until you set PLACEHOLDER = False.",
                          type="positive")
                select_file(name)

            with ui.row():
                ui.button("Create", on_click=create).props("unelevated color=primary")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    with shell("Custom Scripts — rules limited only by Python", "scripts", measure=True):
        ui.label("Each .py file in custom_rules/ becomes a pipeline stage you can enable, disable and "
                 "reorder on the Pipeline page. Implement clean(text, ctx) and the chart is yours to "
                 "transform however you like.").classes("cc-page-sub")

        with ui.card().classes("w-full cc-tint cc-tint--warn gap-3"):
            status_tag("caution", "warn")
            ui.label("Scripts run with the full privileges of your user account on this machine "
                     "(that is what makes them limitless). Only add scripts you wrote or reviewed.") \
                .classes("text-sm")

        with ui.row().classes("w-full gap-4 items-stretch no-wrap"):
            with ui.column().classes("w-64 shrink-0 gap-2 p-4 border rounded"):
                ui.label("Rule files").classes("cc-eyebrow")
                list_holder["box"] = ui.column().classes("w-full gap-1")
                with list_holder["box"]:
                    build_list()
                ui.button("New script", icon="add", on_click=new_script_dialog).props("outline").classes("w-full")
            editor_holder["box"] = ui.column().classes("flex-grow gap-2")
            with editor_holder["box"]:
                build_editor()


# ===========================================================================
# PAGE: Settings
# ===========================================================================
