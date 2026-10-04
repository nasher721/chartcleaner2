"""The Settings page (/settings)."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers
from app_pages import updates
from chartcleaner import model_compare, recent_charts, regression_set
from chartcleaner.summarizer import PRESET_LABELS


def settings_page():
    def _restore_backup(path: str) -> None:
        ok, msg = store.restore_config_backup(path)
        ui.notify(msg, type="positive" if ok else "negative")
        if ok:
            ui.navigate.to("/settings")

    with shell("Settings", "settings"):
        draft = dict(load_config(common.CONFIG_PATH))
        page_col = ui.context.slot.parent
        with ui.row().classes("w-full items-center gap-2 flex-wrap").mark("settings-search-row"):
            search = ui.input(placeholder="Search settings — e.g. watcher, delete, Ollama") \
                .props("outlined dense clearable").classes("w-96").mark("settings-search")
            search.props('prepend-icon="search"')
        jump_row = ui.row().classes("w-full gap-1 flex-wrap")

        with ui.card().classes("w-full gap-2"):
            ui.label("Updates").classes("font-semibold")
            ui.label(f"Current version: v{__version__}").classes("text-sm")
            update_status = ui.label(str(common.PREFS.get("update_status", "Not checked"))).classes("text-sm opacity-70")
            last_checked = common.PREFS.get("update_last_checked")
            ui.label("Last check: " + (time.strftime("%Y-%m-%d %H:%M", time.localtime(last_checked))
                                       if last_checked else "never")).classes("text-xs opacity-60")

            def toggle_auto(e) -> None:
                common.PREFS["update_auto_check"] = bool(e.value)
                save_prefs()

            ui.switch("Check for updates automatically (at most once every 24 hours)",
                      value=bool(common.PREFS.get("update_auto_check", True)), on_change=toggle_auto)

            async def manual_check() -> None:
                check_btn.set_enabled(False)
                try:
                    manifest, status = await updates._check_for_updates(
                        automatic=False, force=True, status_label=update_status)
                    if manifest is not None and status.state == "update_available":
                        confirm_dialog(
                            f"Download and install Chart Cleaner v{manifest.version}?",
                            lambda: asyncio.create_task(updates._stage_and_handoff(
                                manifest, status, ui.notify)))
                finally:
                    check_btn.set_enabled(True)

            check_btn = ui.button("Check for updates", icon="refresh", on_click=manual_check).props("outline")

        with ui.card().classes("w-full gap-3"):
            ui.label("Output").classes("font-semibold")
            ui.switch("Wrap cleaned text in <patient_chart> tags",
                      value=bool(draft.get("wrap_output", True)),
                      on_change=lambda e: draft.update(wrap_output=e.value))
            ui.input("Wrapper tag name", value=str(draft.get("wrap_tag") or "patient_chart"),
                     on_change=lambda e: draft.update(wrap_tag=e.value.strip())).classes("w-72")

            def save_output() -> None:
                errs, _ = validate_config(draft)
                if errs:
                    ui.notify(errs[0], type="negative")
                    return
                save_config_with_backup(draft)
                ui.notify("Output settings saved.", type="positive")

            ui.button("Save output settings", on_click=save_output).props("outline")

        with ui.card().classes("w-full gap-2"):
            ui.label("NLP redaction status").classes("font-semibold")
            try:
                import presidio_analyzer  # noqa: F401
                nlp_ok = find_spec("en_core_web_sm") is not None
            except Exception:
                nlp_ok = False
            if nlp_ok:
                ui.label("✓ Presidio + spaCy model available — NLP redaction is active.") \
                    .classes("text-green-600 text-sm")
            else:
                ui.label("✗ Presidio or the spaCy model is missing; the NLP stage will be skipped. "
                         "Re-run install.sh (macOS) or install.bat (Windows).").classes("text-orange-600 text-sm")

        # ---- converters & optional engines ------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("File converters & optional engines").classes("font-semibold")
            ui.label("Core parts are always available. Optional engines are picked up "
                     "automatically when installed (pip install …) — the app works fine without them.") \
                .classes("text-xs opacity-60 -mt-1")
            status = converters_status()
            engine_info = {
                "pymupdf": ("PDF text extraction", True, ""),
                "watchdog": ("Folder watcher", True, ""),
                "ex4nicegui": ("Reactive live stats (Clean page)", True, ""),
                "markitdown": ("Word/PDF → Markdown (advanced converter)", False, "pip install markitdown"),
                "docling": ("Layout-aware document parsing", False, "pip install docling (large download)"),
                "ocrmypdf": ("OCR for scanned PDFs", False, "pip install ocrmypdf (or the ocrmypdf CLI)"),
                "medspacy": ("Clinical section detection (Header stage)", False, "pip install medspacy"),
            }
            for key, (desc, core, howto) in engine_info.items():
                ok = bool(status.get(key))
                with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
                    ui.icon("check_circle" if ok else "radio_button_unchecked",
                            ).classes("text-green-600" if ok else "opacity-30")
                    ui.label(desc).classes("text-sm flex-grow")
                    if ok:
                        ui.badge("available", color="green").props("outline")
                    elif core:
                        ui.badge("install with install.sh", color="blue-grey").props("outline")
                    else:
                        ui.badge(howto, color="grey").props("outline").classes("text-[10px]")

        # ---- folder watcher -----------------------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Folder watcher — auto-clean as you drop files").classes("font-semibold")
            ui.label("Any .txt/.md/.docx/.pdf dropped into the watched folder is ingested, cleaned "
                     "with your current rules, and written to the output folder as "
                     "<name>_cleaned.txt. Runs continue to appear in Statistics.") \
                .classes("text-xs opacity-60 -mt-1")
            if not find_spec("watchdog"):
                ui.label("watchdog is not installed — run install.sh / install.bat to enable.") \
                    .classes("text-orange-600 text-sm")
            else:
                wcfg = store.load_watch_config()
                wd_in = ui.input("Watched folder", value=wcfg.get("watch_dir", ""),
                                 placeholder="/path/to/drop charts here").classes("w-full cc-mono")
                od_in = ui.input("Output folder (empty = data/watched_out)",
                                 value=wcfg.get("out_dir", "")).classes("w-full cc-mono")

                def watcher_status_text() -> str:
                    fw = WATCHER.get("obj")
                    if fw is None:
                        return "Not running."
                    s = fw.status
                    base = f"{s.state} · processed {s.processed}"
                    if s.last_file:
                        base += f" · last: {s.last_file} at {s.last_ts}"
                    if s.errors:
                        base += f" · {len(s.errors)} note(s)"
                    return base

                status_lbl = ui.label(watcher_status_text()).classes("text-xs opacity-70")

                def start_watcher() -> None:
                    watch_dir = Path(wd_in.value or "").expanduser()
                    if not watch_dir.is_dir():
                        ui.notify("Watched folder does not exist.", type="negative")
                        return
                    out_dir = Path(od_in.value).expanduser() if od_in.value.strip() \
                        else store.DATA_DIR / "watched_out"
                    WATCHER_CONFIG.update(enabled=True, watch_dir=str(watch_dir), out_dir=str(out_dir))
                    try:
                        store.save_watch_config(WATCHER_CONFIG)
                        fw = watcher_mod.FolderWatcher(
                            watcher_mod.WatchSpec(watch_dir=watch_dir, out_dir=out_dir),
                            load_config(common.CONFIG_PATH), custom_dir=common.CUSTOM_DIR)
                        fw.start()
                        WATCHER["obj"] = fw
                    except Exception as e:
                        report_error("Could not start folder watcher", e)
                        return
                    ui.notify(f"Watching {watch_dir}", type="positive")
                    status_lbl.set_text(watcher_status_text())

                def stop_watcher() -> None:
                    fw = WATCHER.get("obj")
                    if fw is not None:
                        fw.stop()
                        WATCHER["obj"] = None
                    WATCHER_CONFIG.update(enabled=False)
                    store.save_watch_config(WATCHER_CONFIG)
                    status_lbl.set_text("Stopped.")
                    ui.notify("Folder watcher stopped.", type="info")

                with ui.row().classes("gap-2"):
                    ui.button("Start watching", icon="play_arrow", on_click=start_watcher) \
                        .props("unelevated color=primary")
                    ui.button("Stop", icon="stop", on_click=stop_watcher).props("outline")
                    if wcfg.get("watch_dir"):
                        ui.button("Open output folder", icon="folder",
                                  on_click=lambda: open_folder(
                                      Path(wcfg.get("out_dir") or store.DATA_DIR / "watched_out"))) \
                            .props("flat")
                ui.timer(2.0, lambda: status_lbl.set_text(watcher_status_text()))

        with ui.card().classes("w-full gap-2"):
            ui.label("Data on this computer").classes("font-semibold")
            ui.label(f"Config: {common.CONFIG_PATH}").classes("text-xs font-mono opacity-70")
            ui.label(f"History: {store.STATS_FILE}").classes("text-xs font-mono opacity-70")
            ui.label(f"Custom scripts: {common.CUSTOM_DIR}").classes("text-xs font-mono opacity-70")
            ui.label(f"Presets: {store.PRESETS_DIR}").classes("text-xs font-mono opacity-70")
            with ui.row().classes("gap-2 flex-wrap"):
                ui.button("Open data folder", icon="folder",
                          on_click=lambda: open_folder(store.DATA_DIR)).props("flat")
                ui.button("Open custom rules folder", icon="folder_open",
                          on_click=lambda: open_folder(common.CUSTOM_DIR)).props("flat")
                ui.button("Open exports folder", icon="download",
                          on_click=lambda: open_folder(store.EXPORTS_DIR)).props("flat")

            async def quit_app() -> None:
                ui.notify("Shutting down — you can close this tab.", type="positive")
                await asyncio.sleep(0.8)
                os._exit(0)

            ui.button("Quit app (stops the local server)", icon="power_settings_new",
                      on_click=quit_app).props("flat color=negative")

        with ui.card().classes("w-full gap-2"):
            ui.label("Config backups").classes("font-semibold")
            ui.label("Every rules save keeps a timestamped copy in data/backups (newest 5). "
                     "Corrupt backups are refused.") \
                .classes("text-xs opacity-60 -mt-1")
            try:
                current_rules = store._rule_count(load_config(common.CONFIG_PATH))
            except Exception:
                current_rules = -1
            backups = store.list_config_backups()
            if not backups:
                ui.label("No backups yet — they appear after your first rules save.").classes("text-sm opacity-60")
            for b in backups[:5]:
                with ui.row().classes("w-full items-center gap-3 flex-nowrap"):
                    ui.icon("history").classes("opacity-60")
                    ui.label(b["ts"]).classes("text-sm w-40")
                    rules_txt = f"{b['rules']} rule entries" if b["rules"] >= 0 else "⚠ unreadable"
                    ui.label(rules_txt).classes("text-xs opacity-70 w-40")

                    def restore(path=b["file"]) -> None:
                        confirm_dialog(
                            "Replace the current rules with this backup?",
                            lambda: _restore_backup(path))

                    ui.button("Restore", icon="restore", on_click=restore).props("outline dense")
            if backups:
                ui.label(f"Current config: {current_rules} rule entries.") \
                    .classes("text-xs opacity-60 mt-1")
            with ui.row().classes("gap-2"):
                ui.button("Open backups folder", icon="folder",
                          on_click=lambda: open_folder(store.BACKUPS_DIR)).props("flat")

        # ---- local API ------------------------------------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Local API").classes("font-semibold")
            ui.label("Lets scripts, launchers (Raycast, Alfred, Keyboard Maestro, AutoHotkey) and the "
                     "browser extension clean text through this app. It only answers this computer, "
                     "needs this token, and never logs chart text.").classes("text-xs opacity-60 -mt-1")
            token_label = ui.label("Token: ••••••••").classes("text-sm cc-mono")

            def show_token() -> None:
                token_label.set_text(f"Token: {local_api.get_token()}")

            def rotate() -> None:
                local_api.rotate_token()
                show_token()
                ui.notify("New token created; update anything that used the old one.", type="info")

            with ui.row().classes("gap-2"):
                ui.button("Show", icon="visibility", on_click=show_token).props("flat dense")
                ui.button("Copy", icon="content_copy",
                          on_click=lambda: copy_to_clipboard(local_api.get_token(), "Token copied")) \
                    .props("flat dense")
                ui.button("New token", icon="autorenew",
                          on_click=lambda: confirm_dialog("Replace the token? Tools using it will "
                                                          "need the new one.", rotate)).props("flat dense")
            port = updates.SERVER_PORT or 8765
            ui.label(f'curl -s http://127.0.0.1:{port}/api/v1/clean -H "Authorization: Bearer $TOKEN" '
                     f'-H "Content-Type: application/json" -d \'{{"text": "Pt w/ HTN"}}\'') \
                .classes("text-xs cc-mono opacity-80 break-all")

        # ---- clipboard watcher --------------------------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Clipboard watcher").classes("font-semibold")
            ui.label("While on, Epic text you copy anywhere is cleaned with your current rules and "
                     "the clipboard is replaced with the result, so you can paste it straight "
                     "away. Only text that looks like a chart is touched. Runs only while Chart "
                     "Cleaner is open.").classes("text-xs opacity-60 -mt-1")
            cw_prefs = dict(common.PREFS.get("clipboard_watcher") or {"enabled": False, "action": "auto"})
            cw_status = ui.label("").classes("text-sm")

            def cw_save() -> None:
                common.PREFS["clipboard_watcher"] = dict(cw_prefs)
                save_prefs()

            def cw_refresh() -> None:
                watcher = CLIPBOARD_WATCHER["watcher"]
                if watcher is None or not watcher.running:
                    cw_status.set_text("Off.")
                    return
                last = watcher.events[-1].message if watcher.events else "Watching — nothing cleaned yet."
                cw_status.set_text(last)

            def cw_toggle(on: bool) -> None:
                cw_prefs["enabled"] = bool(on)
                cw_save()
                try:
                    clipboard_watcher(start=bool(on))
                except Exception as ex:
                    ui.notify(f"No clipboard available here: {ex}", type="warning")
                cw_refresh()

            def cw_action(value: str) -> None:
                cw_prefs["action"] = value
                cw_save()
                if CLIPBOARD_WATCHER["watcher"] is not None:
                    CLIPBOARD_WATCHER["watcher"].action = value

            def cw_undo() -> None:
                watcher = CLIPBOARD_WATCHER["watcher"]
                if watcher is not None and watcher.undo():
                    ui.notify("The original text is back on the clipboard.", type="positive")
                else:
                    ui.notify("Nothing to undo.", type="info")

            with ui.row().classes("items-center gap-4 flex-wrap"):
                ui.switch("Clean Epic text as soon as it's copied", value=bool(cw_prefs.get("enabled")),
                          on_change=lambda e: cw_toggle(e.value))
                ui.select({"auto": "Replace the clipboard with the cleaned text",
                           "notify": "Only notify me"}, value=cw_prefs.get("action", "auto"),
                          on_change=lambda e: cw_action(e.value)).classes("min-w-[300px]")
                ui.button("Undo last clipboard clean", icon="undo", on_click=cw_undo).props("flat")
            ui.timer(2.0, cw_refresh)

        # ---- notes folder (Obsidian or any Markdown folder) ---------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Notes folder").classes("font-semibold")
            ui.label("Set a folder (for example your Obsidian vault) to get “Save to notes folder” "
                     "on the Clean page. Each save is a new Markdown note with the date and note "
                     "type; existing notes are never overwritten.").classes("text-xs opacity-60 -mt-1")
            ui.input("Folder path", value=str(common.PREFS.get("notes_folder") or ""),
                     placeholder="~/Documents/Obsidian/Charts",
                     on_change=lambda e: (common.PREFS.update(notes_folder=e.value.strip()), save_prefs())) \
                .classes("w-full cc-mono")

        # ---- local AI (Ollama) ----------------------------------------------------------
        with ui.card().classes("w-full gap-2").mark("local-ai-card"):
            ui.label("Local AI (Ollama)").classes("font-semibold")
            ui.label("Summaries and “Ask this chart” run on this computer through Ollama. Chart "
                     "text only ever goes to a loopback address.").classes("text-xs opacity-60 -mt-1")
            ai_status = ui.label("Not checked yet.").classes("text-sm").mark("local-ai-status")
            ai_models = ui.column().classes("w-full gap-1")
            pull_row = ui.row().classes("w-full items-center gap-2")
            pull_state = {"running": False, "status": "", "fraction": None}

            def use_model(name: str) -> None:
                cfg = load_config(common.CONFIG_PATH)
                cfg["local_llm"] = {**merge_llm_config(cfg), "model": name}
                save_config_with_backup(cfg)
                ui.notify(f"Summaries will use {name}.", type="positive")
                draw_health(last_health.get("h"))

            last_health: dict = {}

            def draw_health(h: dict | None) -> None:
                if not h:
                    return
                last_health["h"] = h
                current = merge_llm_config(load_config(common.CONFIG_PATH)).get("model") or ""
                if h["reachable"]:
                    ai_status.set_text(f"✓ Ollama {h['version'] or ''} at {h['base_url']} — "
                                       f"{len(h['models'])} model(s)" + (f" · {h['error']}" if h["error"] else ""))
                else:
                    ai_status.set_text(f"✕ {h['error'] or 'Not reachable'} ({h['base_url']})")
                ai_models.clear()
                with ai_models:
                    for m in h["models"]:
                        with ui.row().classes("w-full items-center gap-2 no-wrap"):
                            ui.label(m.get("name", "")).classes("text-sm cc-mono w-56")
                            ui.label(" · ".join(x for x in (m.get("parameters"), f"{m.get('size_gb', '')} GB"
                                                              if m.get("size_gb") else "", m.get("modified")) if x)) \
                                .classes("text-xs opacity-60 flex-grow")
                            if m.get("name") == current or (not current and m is h["models"][0]):
                                ui.badge("in use", color="green").props("outline")
                            else:
                                ui.button("Use", on_click=lambda n=m["name"]: use_model(n)).props("flat dense")
                pull_row.set_visibility(h["reachable"] and not h["has_recommended"])

            async def check_ai() -> None:
                url = str(merge_llm_config(load_config(common.CONFIG_PATH))["base_url"])
                draw_health(await run.io_bound(local_llm_health, url))

            async def pull_recommended() -> None:
                if pull_state["running"]:
                    return
                pull_state.update(running=True, status="starting…", fraction=None)
                url = str(merge_llm_config(load_config(common.CONFIG_PATH))["base_url"])

                def work():
                    client = LocalLlmClient(url, timeout=30.0)
                    return client.pull(RECOMMENDED_MODEL, on_progress=lambda st, fr: pull_state.update(
                        status=st, fraction=fr))
                try:
                    ok = await run.io_bound(work)
                    ui.notify(f"{RECOMMENDED_MODEL} is ready." if ok else "Pull finished without success.",
                              type="positive" if ok else "warning")
                except Exception as ex:
                    ui.notify(f"Could not pull {RECOMMENDED_MODEL}: {ex}", type="negative")
                finally:
                    pull_state["running"] = False
                    await check_ai()

            def tick_pull() -> None:
                if pull_state["running"]:
                    frac = pull_state["fraction"]
                    pull_label.set_text(pull_state["status"] + (f" — {frac:.0%}" if frac is not None else ""))
                    if frac is not None:
                        pull_bar.set_value(frac)

            with pull_row:
                ui.button(f"Pull {RECOMMENDED_MODEL} (≈5 GB)", icon="download",
                          on_click=pull_recommended).props("outline").mark("pull-model")
                pull_bar = ui.linear_progress(value=0, show_value=False).classes("w-48")
                pull_label = ui.label("").classes("text-xs opacity-70")
            pull_row.set_visibility(False)
            ui.timer(0.5, tick_pull)
            with ui.row().classes("gap-2"):
                ui.button("Check now", icon="refresh", on_click=check_ai).props("flat").mark("check-ai")
            if not os.environ.get("NICEGUI_USER_SIMULATION"):
                ui.timer(0.2, check_ai, once=True)

            # ---- compare models on your own charts ----
            ui.separator()
            ui.label("Compare models").classes("text-sm font-semibold")
            ui.label("Summarizes up to three of your known-good charts (or the sample chart) with "
                     "every installed model and ranks them: values the chart never states first, "
                     "then grounding, cited lines and speed. Nothing is stored.") \
                .classes("text-xs opacity-60 -mt-1")
            cmp_state = {"running": False, "status": "", "fraction": None, "result": None}
            cmp_results = ui.column().classes("w-full gap-1")

            def draw_comparison(res) -> None:
                cmp_results.clear()
                with cmp_results:
                    ui.label(f"Preset “{PRESET_LABELS.get(res.preset, res.preset)}” on "
                             f"{len(res.charts)} chart(s): {', '.join(res.charts)}") \
                        .classes("text-xs opacity-60")
                    with ui.row().classes("w-full gap-2 no-wrap text-xs font-semibold opacity-70"):
                        for title, width in (("Model", "w-48"), ("Not in chart / summary", "w-36"),
                                             ("Grounding", "w-20"), ("Cited lines", "w-20"),
                                             ("Seconds", "w-16")):
                            ui.label(title).classes(width)
                    for i, s in enumerate(res.scores):
                        with ui.row().classes("w-full items-center gap-2 no-wrap").mark(f"compare-row-{i}"):
                            ui.label(s.model).classes("text-sm cc-mono w-48 truncate")
                            if not s.ok_trials:
                                err = s.trials[0].error if s.trials else ""
                                ui.label(f"failed: {err}").classes("text-xs text-red-600 truncate flex-grow")
                                continue
                            bad = s.unsupported_per_summary
                            ui.label(str(bad)).classes(
                                "text-sm w-36 " + ("text-green-600" if bad == 0 else "text-orange-600")) \
                                .tooltip(", ".join(s.examples()) or "none")
                            ui.label(f"{s.grounding}%").classes("text-sm w-20")
                            ui.label(f"{s.citation_coverage}%").classes("text-sm w-20")
                            ui.label(f"{s.median_seconds}").classes("text-sm w-16")
                            if i == 0:
                                ui.badge("best", color="green").props("outline")
                            ui.button("Use", on_click=lambda n=s.model: use_model(n)).props("flat dense")

            async def run_comparison() -> None:
                if cmp_state["running"]:
                    return
                cmp_state.update(running=True, status="starting…", fraction=0.0)
                cfg = load_config(common.CONFIG_PATH)
                preset = cmp_preset.value or "clinical"

                def progress(done: int, total: int, model: str) -> None:
                    cmp_state.update(status=f"{model} ({done + 1}/{total})" if model else "done",
                                     fraction=done / total if total else None)
                try:
                    res = await run.io_bound(model_compare.compare, cfg, None, None,
                                             preset=preset, on_progress=progress)
                    draw_comparison(res)
                except Exception as ex:
                    ui.notify(f"Could not compare models: {ex}", type="warning")
                finally:
                    cmp_state.update(running=False, status="")
                    tick_compare()

            def tick_compare() -> None:
                cmp_label.set_text(cmp_state["status"])
                cmp_bar.set_visibility(cmp_state["running"])
                if cmp_state["fraction"] is not None:
                    cmp_bar.set_value(cmp_state["fraction"])

            with ui.row().classes("w-full items-center gap-2"):
                cmp_preset = ui.select({k: v for k, v in PRESET_LABELS.items() if k != "custom"},
                                       value="clinical", label="Summary preset").classes("w-48")
                ui.button("Compare installed models", icon="leaderboard",
                          on_click=run_comparison).props("outline").mark("compare-models")
                cmp_bar = ui.linear_progress(value=0, show_value=False).classes("w-40")
                cmp_label = ui.label("").classes("text-xs opacity-70")
            cmp_bar.set_visibility(False)
            ui.timer(0.5, tick_compare)

        # ---- prompt templates ---------------------------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Prompt templates").classes("font-semibold")
            ui.label("Used by “Copy as prompt” on the Clean page (and --prompt on the command line). "
                     "Placeholders: {chart} the cleaned chart, {delta} what changed since the last "
                     "note, {date} today. A template with a built-in's name replaces it.") \
                .classes("text-xs opacity-60 -mt-1")
            tmpl_box = ui.column().classes("w-full gap-2")

            def save_templates(items: list[dict]) -> None:
                cfg = load_config(common.CONFIG_PATH)
                cfg["prompt_templates"] = items
                try:
                    save_config_with_backup(cfg)
                except Exception as ex:
                    ui.notify(f"Could not save: {ex}", type="negative")
                    return
                ui.notify("Templates saved.", type="positive")
                draw_templates()

            def draw_templates() -> None:
                tmpl_box.clear()
                mine = list(load_config(common.CONFIG_PATH).get("prompt_templates") or [])
                with tmpl_box:
                    for idx, t in enumerate(mine):
                        with ui.column().classes("w-full gap-1 border rounded p-2"):
                            with ui.row().classes("w-full items-center gap-2"):
                                name = ui.input("Name", value=t.get("name", "")).classes("flex-grow")
                                fmt = ui.select({"text": "Plain text", "markdown": "Markdown",
                                                 "xml": "XML sections"}, value=t.get("format", "text"),
                                                label="Chart format").classes("w-40")
                            body = ui.textarea("Template", value=t.get("template", "")) \
                                .props("outlined autogrow").classes("w-full cc-mono")
                            with ui.row():
                                ui.button("Save", on_click=lambda i=idx, n=name, f=fmt, b=body: save_templates(
                                    [*mine[:i], {"name": n.value.strip(), "format": f.value,
                                                 "template": b.value}, *mine[i + 1:]])).props("flat dense")
                                ui.button("Delete", on_click=lambda i=idx: save_templates(
                                    mine[:i] + mine[i + 1:])).props("flat dense color=negative")
                    ui.button("Add template", icon="add", on_click=lambda: save_templates(
                        mine + [{"name": f"My template {len(mine) + 1}", "format": "text",
                                 "template": "Summarize this chart:\n\n{chart}"}])).props("outline")

            draw_templates()

        # ---- note templates -----------------------------------------------------------
        with ui.card().classes("w-full gap-2").mark("note-templates"):
            ui.label("Note templates").classes("font-semibold")
            ui.label("Used by “Copy as → note template” on the Clean page (and --template on the "
                     "command line): the note itself, filled with the chart's own lines. "
                     "Placeholders: {{chart}}, {{date}}, {{systems}} ([N] [CV] [R] [R/GU] [GI] [E] "
                     "[H] [ID] …), {{system:N}}, {{section:Assessment & Plan}}, {{problems}}, "
                     "{{devices}}, {{micro}}, {{overnight}}, {{trends}}, {{pending}}, {{bundle}}. A template with a "
                     "built-in's name replaces it.").classes("text-xs opacity-60 -mt-1")
            ui.label("Built-in: " + ", ".join(t["name"] for t in note_templates_mod.DEFAULT_TEMPLATES)) \
                .classes("text-xs opacity-60")
            note_box = ui.column().classes("w-full gap-2")

            def save_note_templates(items: list[dict]) -> None:
                cfg = load_config(common.CONFIG_PATH)
                cfg["note_templates"] = items
                try:
                    save_config_with_backup(cfg)
                except Exception as ex:
                    ui.notify(f"Could not save: {ex}", type="negative")
                    return
                ui.notify("Note templates saved.", type="positive")
                draw_note_templates()

            def draw_note_templates() -> None:
                note_box.clear()
                mine = list(load_config(common.CONFIG_PATH).get("note_templates") or [])
                with note_box:
                    for idx, t in enumerate(mine):
                        with ui.column().classes("w-full gap-1 border rounded p-2"):
                            name = ui.input("Name", value=t.get("name", "")).classes("w-full")
                            body = ui.textarea("Template", value=t.get("template", "")) \
                                .props("outlined autogrow").classes("w-full cc-mono")
                            with ui.row():
                                ui.button("Save", on_click=lambda i=idx, n=name, b=body: save_note_templates(
                                    [*mine[:i], {"name": n.value.strip(), "template": b.value},
                                     *mine[i + 1:]])).props("flat dense")
                                ui.button("Delete", on_click=lambda i=idx: save_note_templates(
                                    mine[:i] + mine[i + 1:])).props("flat dense color=negative")
                    ui.button("Add note template", icon="add", on_click=lambda: save_note_templates(
                        mine + [{"name": f"My note {len(mine) + 1}",
                                 "template": "{{date}}\n\n{{overnight}}\n\n{{systems}}"}])) \
                        .props("outline")

            draw_note_templates()

        # ---- note types → presets ---------------------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Note types").classes("font-semibold")
            ui.label("The Clean page recognizes the kind of note (discharge summary, H&P, progress, "
                     "consult, nursing, operative, radiology). Pick a preset for a type and turn on "
                     "auto-apply to clean that type with it. Only that clean uses the preset; your "
                     "saved rules are not changed.").classes("text-xs opacity-60 -mt-1")
            preset_names = {"": "(current rules)", **{n: n for n in store.list_presets()}}
            mapping = dict(common.PREFS.get("note_presets") or {})

            def set_mapping(kind: str, value: str) -> None:
                if value:
                    mapping[kind] = value
                else:
                    mapping.pop(kind, None)
                common.PREFS["note_presets"] = dict(mapping)
                save_prefs()

            with ui.grid(columns=2).classes("w-full gap-2"):
                for kind, label in NOTE_TYPE_LABELS.items():
                    ui.select(preset_names, label=label,
                              value=mapping.get(kind) if mapping.get(kind) in preset_names else "",
                              on_change=lambda e, k=kind: set_mapping(k, e.value or "")).classes("w-full")
            ui.switch("Auto-apply the preset for the detected note type",
                      value=bool(common.PREFS.get("note_auto_apply")),
                      on_change=lambda e: (common.PREFS.update(note_auto_apply=e.value), save_prefs()))

        # ---- learned-rule examples (regression check) -----------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Check my learned rules").classes("font-semibold")
            ui.label("Every rule you teach by highlighting remembers what it was taught to do. "
                     "This replays those examples through your current rules and lists any whose "
                     "result changed, for example because a later rule undoes an earlier one.") \
                .classes("text-xs opacity-60 -mt-1")
            examples_out = ui.column().classes("w-full gap-1")

            def check_examples() -> None:
                report = rule_examples.check(load_config(common.CONFIG_PATH))
                examples_out.clear()
                with examples_out:
                    if report["stage_disabled"]:
                        ui.label("The Learned rules stage is turned off, so none of these rules run.") \
                            .classes("text-sm text-orange-600")
                    if not report["checked"]:
                        ui.label("No examples yet — teach a rule by highlighting text on the Clean page.") \
                            .classes("text-sm")
                    elif not report["failures"]:
                        ui.label(f"✓ All {report['checked']} learned rule(s) still do what they were "
                                 "taught.").classes("text-sm text-green-600")
                    for f in report["failures"][:20]:
                        ui.label(f"“{f['text']}” used to become “{f['expected']}”, now becomes "
                                 f"“{f['now']}”.").classes("text-sm cc-mono")

            def clear_examples() -> None:
                n = rule_examples.clear()
                examples_out.clear()
                ui.notify(f"Removed {n} saved example(s).", type="info")

            with ui.row().classes("gap-2"):
                ui.button("Check my learned rules", icon="fact_check", on_click=check_examples) \
                    .props("outline")
                ui.button("Clear saved examples", icon="delete", on_click=clear_examples).props("flat")

        # ---- export / import settings bundle ------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.button("Share removal, replacement & abbreviation rules", icon="share",
                      on_click=lambda: ui.navigate.to("/rules")).props("unelevated")
            ui.label("Export & import settings").classes("font-semibold")
            ui.label("Bundles everything you customized — all rules (including rules learned by "
                     "highlighting text), output options, presets, custom scripts, folder watcher "
                     "and suggestion dismissals — into one .zip to import on a fresh install. "
                     "Run history and token maps are NOT included: history is reproducible and "
                     "token maps undo the cleaning.") \
                .classes("text-xs opacity-60 -mt-1")

            def export_settings_bundle() -> None:
                try:
                    path, counts = store.export_settings()
                except Exception as e:
                    report_error("Settings export failed", e)
                    return
                download_file(f"/exports/{path.name}", path.name)
                ui.notify(f"Exported {sum(counts.values())} item(s) → {path.name} "
                          "(check your downloads).", type="positive")

            ui.button("Export all settings (.zip)", icon="download",
                      on_click=export_settings_bundle).props("outline")

            async def import_settings_bundle(e) -> None:
                tmp_path = None
                try:
                    _, data = await read_upload(e)
                    tmp_path = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
                    tmp_path.write(data)
                    tmp_path.close()
                    ok, msg = store.import_settings(tmp_path.name)
                    if ok:
                        common.PREFS.clear()
                        common.PREFS.update(store.load_prefs())
                    ui.notify(msg, type="positive" if ok else "negative")
                    if ok:
                        await asyncio.sleep(0.8)
                        ui.navigate.to("/settings")
                except Exception as ex:
                    report_error("Settings import failed", ex)
                finally:
                    if tmp_path:
                        try:
                            Path(tmp_path.name).unlink()
                        except OSError:
                            pass

            ui.upload(on_upload=import_settings_bundle, auto_upload=True) \
                .props("accept=.zip,application/zip flat label='Import settings (.zip)' icon='upload'") \
                .classes("max-w-xs")
            ui.label("Importing replaces the current rules (a config backup is kept first) "
                     "and adds/overwrites presets and scripts with the same names.") \
                .classes("text-xs opacity-60")

        with ui.card().classes("w-full gap-1"):
            ui.label("About").classes("font-semibold")
            ui.markdown(
                f"**Chart Cleaner v{__version__}** — cleans Epic-style EMR exports for safer LLM sharing.\n\n"
                "- Clinical data stays on this computer; update checks are the only built-in outbound request.\n"
                "- Inputs: .txt / .md / .docx / .pdf (Word via built-in reader or markitdown; PDFs via "
                "PyMuPDF, OCR when ocrmypdf is installed).\n"
                "- This tool **reduces** obvious PHI and noise; it is **not** a HIPAA de-identification guarantee.\n"
                "- Review output before sharing. You are responsible for what you send to third parties."
            ).classes("text-sm")

        with ui.card().classes("w-full gap-1"):
            ui.label("Ideas still on the roadmap").classes("font-semibold")
            ui.markdown(
                "- **Code signing / notarization** of the macOS .app bundle.\n"
                "- **Weekly summary** — scheduled stats report.\n"
                "- **Recent runs** — reopen or re-run yesterday's cleaned output."
            ).classes("text-sm")

        # ---- privacy: stored chart data ------------------------------------------
        with ui.card().classes("w-full gap-2").mark("privacy-card"):
            ui.label("Stored chart data").classes("font-semibold")
            ui.label(f"Token maps and recent charts are encrypted; the key is kept in "
                     f"{secure_store.describe()}. Recent charts, "
                     "downloads, batch output and folder-watcher output in the data folder are "
                     "deleted automatically after the period below. Run history, rules and "
                     "settings hold no chart text and are kept.") \
                .classes("text-xs opacity-60 -mt-1")

            def set_retention(e) -> None:
                try:
                    days = max(0, int(e.value or 0))
                except (TypeError, ValueError):
                    return
                common.PREFS["retention_days"] = days
                save_prefs()

            def delete_now() -> None:
                n = store.delete_all_chart_data()
                ui.notify(f"Deleted {n} stored item(s).", type="positive")
                ui.navigate.reload()

            with ui.row().classes("items-center gap-3"):
                ui.number("Delete after (days, 0 = keep)", value=int(common.PREFS.get("retention_days") or 0),
                          min=0, max=3650, step=1, on_change=set_retention) \
                    .classes("w-56").mark("retention-days")
                ui.button("Delete stored chart data now", icon="delete_forever",
                          on_click=lambda: confirm_dialog(
                              "Delete all token maps, recent and known-good charts, downloads, batch "
                              "output and folder-watcher output in the data folder? Token maps "
                              "can't be restored afterwards.",
                              delete_now)).props("outline color=negative")

        # ---- privacy: recent charts for rule suggestions ---------------------------
        with ui.card().classes("w-full gap-2").mark("recent-charts-card"):
            ui.label("Recent charts (for rule suggestions)").classes("font-semibold")
            ui.label("The Clean page keeps encrypted copies of your last few charts so it can "
                     "suggest rules (“this line appeared in 9 of your last 10 charts”) and show "
                     "what a new rule would also change. They are deleted after the period above "
                     "and by “Delete stored chart data now”.").classes("text-xs opacity-60 -mt-1")
            rc = recent_charts.settings(common.PREFS)
            rc_count = ui.label(f"{recent_charts.count()} chart(s) kept now.").classes("text-sm")

            def rc_save(**changes) -> None:
                current = recent_charts.settings(common.PREFS)
                current.update(changes)
                common.PREFS["recent_charts"] = current
                save_prefs()

            def rc_forget() -> None:
                n = recent_charts.clear()
                rc_count.set_text("0 chart(s) kept now.")
                ui.notify(f"Forgot {n} recent chart(s).", type="positive")

            with ui.row().classes("items-center gap-3"):
                ui.switch("Keep recent charts", value=rc["enabled"],
                          on_change=lambda e: rc_save(enabled=bool(e.value))).mark("recent-charts-switch")
                ui.number("How many", value=rc["keep"], min=1, max=200, step=1,
                          on_change=lambda e: rc_save(keep=int(e.value or 20))).classes("w-32")
                ui.button("Forget them now", icon="delete", on_click=rc_forget).props("flat color=negative")

        # ---- known-good charts (personal regression set) ----------------------------
        with ui.card().classes("w-full gap-2").mark("known-good-card"):
            ui.label("Known-good charts").classes("font-semibold")
            ui.label("Charts you marked “known good” on the Clean page, with the output you "
                     "approved (encrypted). Check them after changing rules — Save on the Pipeline "
                     "page does this for you.").classes("text-xs opacity-60 -mt-1")
            kg_holder = ui.column().classes("w-full gap-1")

            def kg_draw(results: dict | None = None) -> None:
                kg_holder.clear()
                charts = regression_set.list_charts()
                with kg_holder:
                    if not charts:
                        ui.label("None yet — on the Clean page use ⋯ → Mark as known good.") \
                            .classes("text-sm opacity-60")
                    for kg in charts:
                        res = (results or {}).get(kg.id)
                        with ui.row().classes("w-full items-center gap-2 no-wrap"):
                            if res is None:
                                ui.icon("radio_button_unchecked").classes("opacity-50")
                            elif res.changed:
                                ui.icon("error", color="orange")
                            else:
                                ui.icon("check_circle", color="green")
                            ui.label(kg.label).classes("text-sm flex-grow")
                            ui.label(kg.ts[:16].replace("T", " ")).classes("text-xs opacity-60")
                            if res is not None and res.changed:
                                ui.button("Show change", icon="difference",
                                          on_click=lambda r=res: kg_show(r)).props("flat dense")
                                ui.button("Accept new output", icon="done",
                                          on_click=lambda k=kg: kg_accept(k)).props("flat dense")
                            ui.button(icon="delete", on_click=lambda k=kg: kg_remove(k.id)) \
                                .props("flat dense round color=negative")

            def kg_show(res) -> None:
                with ui.dialog() as dlg, ui.card().classes("w-[760px] gap-2"):
                    ui.label(res.label).classes("text-lg font-semibold")
                    if res.error:
                        ui.label(res.error).classes("text-sm text-red-600")
                    ui.html("<pre style='white-space:pre-wrap;font-size:11px'>"
                            + esc("\n".join(res.diff)) + "</pre>")
                    ui.button("Close", on_click=dlg.close).props("flat")
                dlg.open()

            def kg_accept(kg) -> None:
                out = Pipeline(load_config(common.CONFIG_PATH), custom_dir=common.CUSTOM_DIR,
                               mode=kg.mode).run(kg.input, fact_check=False).text
                regression_set.approve(kg.id, out)
                ui.notify("New output approved.", type="positive")
                asyncio.get_running_loop().create_task(kg_check())

            def kg_remove(chart_id: str) -> None:
                regression_set.remove(chart_id)
                kg_draw()

            async def kg_check() -> None:
                cfg = load_config(common.CONFIG_PATH)
                results = await run.io_bound(regression_set.check, cfg, common.CUSTOM_DIR)
                changed = sum(r.changed for r in results)
                ui.notify(f"{len(results)} checked — {changed} changed." if results else
                          "No known-good charts yet.", type="warning" if changed else "positive")
                kg_draw({r.id: r for r in results})

            kg_draw()
            ui.button("Check them now", icon="playlist_add_check", on_click=kg_check) \
                .props("outline").mark("known-good-check")

        # ---- token maps (reversible tokenization) -------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Token maps (reversible tokenization)").classes("font-semibold")
            ui.label("When the 'Reversible tokenization' stage is on, each run's value→token map "
                     "is saved here (newest 10). Restore text with the button below, or "
                     "clean-chart --untoken. Treat maps as PHI — they undo the cleaning.") \
                .classes("text-xs opacity-60 -mt-1")

            def restore_tokens(file: str) -> None:
                try:
                    mapping = store.load_token_map(file)
                    current = CLEAN_STATE.get("result_text") or ""
                    if not current:
                        ui.notify("Clean a chart first — its output is what gets restored.",
                                  type="warning")
                        return
                    restored, n = tokens_mod.untokenize(current, mapping)
                    CLEAN_STATE["result_text"] = restored
                    if CLEAN_STATE.get("result") is not None:
                        CLEAN_STATE["result"].text = restored
                    copy_to_clipboard(restored, f"Restored {n} token(s) — copied to clipboard")
                except Exception as e:
                    ui.notify(f"Could not restore: {e}", type="negative")

            maps = store.list_token_maps()
            if not maps:
                ui.label("No maps yet — enable the tokenization stage on the Pipeline page "
                         "and clean a chart.").classes("text-sm opacity-60")
            for m in maps[:10]:
                with ui.row().classes("w-full items-center gap-3 flex-nowrap"):
                    ui.icon("vpn_key").classes("opacity-60")
                    ui.label(m["ts"]).classes("text-xs w-40")
                    count = f"{m['count']} value(s)" if m["count"] >= 0 else "⚠ unreadable"
                    ui.label(count).classes("text-xs opacity-70 w-28")

                    def restore(f=m["file"]) -> None:
                        confirm_dialog("Restore the current cleaned output using this map? "
                                       "(the result will contain real PHI — it stays on this "
                                       "machine)", lambda: restore_tokens(f))

                    ui.button("Restore into result", icon="lock_open", on_click=restore) \
                        .props("outline dense")
            with ui.row().classes("gap-2"):
                ui.button("Open tokens folder", icon="folder",
                          on_click=lambda: open_folder(store.TOKENS_DIR)).props("flat")

        # ---- search + jump links (built last: they index every card above) -------
        privacy_marks = ("privacy-card", "recent-charts-card", "known-good-card")
        cards = [c for c in page_col.default_slot.children if isinstance(c, ui.card)]
        for i, card in enumerate([c for c in cards if any(m in c._markers for m in privacy_marks)]):
            card.move(target_index=3 + i)  # privacy first: right under the search box
        cards = [c for c in page_col.default_slot.children if isinstance(c, ui.card)]

        def card_title(card) -> str:
            first = next(iter(card.default_slot.children), None)
            return getattr(first, "text", "") or ""

        def card_words(card) -> str:
            parts = []
            for el in card.descendants():
                for attr in ("text", "_text"):
                    value = getattr(el, attr, None)
                    if isinstance(value, str):
                        parts.append(value)
                label = el.props.get("label")
                if isinstance(label, str):
                    parts.append(label)
            return " ".join(parts).casefold()

        index = [(card, card_words(card)) for card in cards]

        def filter_cards(e) -> None:
            words = (e.value or "").casefold().split()
            for card, text in index:
                card.set_visibility(all(w in text for w in words))

        search.on_value_change(filter_cards)
        with jump_row:
            for card in cards:
                title = card_title(card)
                if title:
                    ui.chip(title, on_click=lambda c=card: ui.run_javascript(
                        f"document.getElementById('c{c.id}').scrollIntoView({{behavior:'smooth'}})")) \
                        .props("dense outline clickable")
