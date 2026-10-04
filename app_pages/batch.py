"""The Batch page (/batch): clean many files at once."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers


def safe_stem(name: str) -> str:
    """A file-name-safe stem for a file or patient label ("G20-1" stays, "a/b" → "a_b")."""
    stem = Path(name).stem if "." in name else name
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip() or "item"


def batch_page():
    """Clean many charts at once — upload files or point at a folder."""
    state = {"running": False}
    pending: list[Path] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="cc_batch_"))
    out_ref: dict = {"dir": None, "zip": None, "results": [], "filter": "all"}
    progress = {"done": 0, "total": 0, "current": ""}

    # ---- handlers (defined before the UI that references them) -------------
    def set_running(flag: bool) -> None:
        state["running"] = flag
        run_btn.set_enabled(not flag)
        spinner.set_visibility(flag)
        progress_row.set_visibility(flag)

    def tick_progress() -> None:
        if not state["running"]:
            return
        total = progress["total"] or 1
        progress_bar.set_value(progress["done"] / total)
        progress_label.set_text(f"{progress['done']} of {progress['total']} done"
                                + (f" — last: {progress['current']}" if progress["current"] else ""))

    def refresh_pending() -> None:
        if pending:
            names = ", ".join(p.name for p in pending[:8])
            if len(pending) > 8:
                names += " …"
            pending_label.set_text(f"{len(pending)} file(s) queued: {names}")
        else:
            pending_label.set_text("No files queued yet.")
        run_btn.set_enabled(bool(pending) and not state["running"])
        clear_btn.set_enabled(bool(pending))

    async def handle_upload(e) -> None:
        try:
            exts = set(supported_extensions())
            uploaded_name, data = await read_upload(e)
            name = Path(uploaded_name or "upload.txt").name or "upload.txt"
            target = tmp_dir / name
            if target.suffix.lower() not in exts:
                ui.notify(f"Skipped {name} — unsupported type "
                          f"({', '.join(sorted(exts))} only).", type="warning")
                return
            target.write_bytes(data)
            if target not in pending:
                pending.append(target)
            refresh_pending()
        except Exception as ex:
            report_error("Upload failed", ex)

    def add_folder() -> None:
        folder = Path(folder_input.value or "").expanduser()
        if not folder.is_dir():
            ui.notify("Folder not found.", type="negative")
            return
        exts = set(supported_extensions())
        found = sorted(f for f in folder.iterdir()
                       if f.is_file() and f.suffix.lower() in exts)
        if not found:
            ui.notify(f"No supported files ({', '.join(sorted(exts))}) in that folder.",
                      type="warning")
            return
        room = max(0, 500 - len(pending))
        pending.extend(found[:room])
        if len(found) > room:
            ui.notify(f"Added {room} of {len(found)} files — 500-file cap per run.",
                      type="warning")
        refresh_pending()

    def clear_pending() -> None:
        pending.clear()
        refresh_pending()

    def download_zip() -> None:
        name = out_ref.get("zip")
        if name:
            download_file(f"/exports/{name}", name)

    def set_filter(value: str) -> None:
        out_ref["filter"] = value
        render_results()

    async def retry_failed() -> None:
        failed = [Path(r.path) for r in out_ref["results"] if r.status == "error"]
        if not failed:
            return
        pending.clear()
        pending.extend(failed)
        refresh_pending()
        await run_now()

    def render_results() -> None:
        results = out_ref["results"]
        out_dir = out_ref["dir"]
        ok = [r for r in results if r.status == "ok"]
        failed = [r for r in results if r.status == "error"]
        review = [r for r in results if r.needs_review]
        shown = {"all": results, "review": review, "errors": failed}[out_ref["filter"]]
        results_col.clear()
        if not results:
            return
        with results_col:
            with ui.row().classes("w-full items-center gap-2 flex-wrap").mark("batch-filter"):
                ui.toggle({"all": f"All ({len(results)})", "review": f"Needs review ({len(review)})",
                           "errors": f"Errors ({len(failed)})"}, value=out_ref["filter"],
                          on_change=lambda e: set_filter(e.value)).props("no-caps")
                if failed:
                    ui.button(f"Retry {len(failed)} failed", icon="replay",
                              on_click=retry_failed).props("flat")
            cols = [
                {"name": "file", "label": "File", "field": "file", "align": "left", "sortable": True},
                {"name": "chars", "label": "Characters", "field": "chars", "align": "left"},
                {"name": "reduction", "label": "Reduction", "field": "reduction", "sortable": True},
                {"name": "phi", "label": "PHI redacted", "field": "phi"},
                {"name": "findings", "label": "Audit findings", "field": "findings"},
                {"name": "facts", "label": "Clinical facts", "field": "facts", "align": "left"},
                {"name": "note", "label": "Note type", "field": "note", "align": "left"},
                {"name": "ms", "label": "Elapsed", "field": "ms", "align": "left"},
                *([{"name": "summary", "label": "One-liner (local AI)", "field": "summary",
                    "align": "left"}] if any(r.summary for r in shown) else []),
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
            ]
            rows = []
            for r in shown:
                if r.status == "ok":
                    rows.append({"file": r.name,
                                 "chars": f"{r.chars_before:,} → {r.chars_after:,}",
                                 "reduction": f"{r.reduction:+.1f}%",
                                 "phi": r.phi_total, "findings": r.findings,
                                 "facts": ("✓ kept" if r.facts_status == "ok" else
                                           ("⚠ " if r.facts_status == "review" else "✕ ")
                                           + r.facts) if r.facts_status else "—",
                                 "note": (r.note_type + (f" → {r.preset}" if r.preset else "")) or "—",
                                 "ms": f"{r.elapsed_ms:,} ms", "status": "ok", "summary": r.summary})
                else:
                    rows.append({"file": r.name, "chars": "—", "reduction": "—",
                                 "phi": "—", "findings": "—", "facts": "—", "note": "—",
                                 "ms": f"{r.elapsed_ms:,} ms",
                                 "status": f"error: {r.error}"})
            ui.table(columns=cols, rows=rows, row_key="file",
                     pagination=20).classes("w-full").props("flat dense")
            if ok and out_dir is not None:
                with ui.row().classes("gap-2 flex-wrap items-center"):
                    ui.button("Download all (.zip)", icon="archive",
                              on_click=download_zip).props("unelevated color=primary")
                    ui.button("Open exports folder", icon="folder",
                              on_click=lambda: open_folder(out_dir)).props("flat")
                for r in [r for r in shown if r.status == "ok"]:
                    out_name = f"{safe_stem(r.name)}_cleaned.txt"
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.icon("description").classes("opacity-60")
                        ui.label(out_name).classes("text-xs cc-mono flex-grow")
                        ui.button("Download .txt", icon="download",
                                  on_click=lambda _, n=out_name: download_file(
                                      f"/exports/{out_dir.name}/{n}", n)
                                  ).props("flat dense")
            if failed:
                ui.label(f"{len(failed)} file(s) failed: "
                         + "; ".join(f"{r.name} — {r.error}" for r in failed[:5])) \
                    .classes("text-xs text-red-600")
            ui.label("Cleaned copies also live in data/exports; run history is on the "
                     "Statistics page.").classes("text-xs opacity-60")

    def publish(results) -> None:
        """Save cleaned copies + a .zip, record history, and show the results."""
        out_dir = store.EXPORTS_DIR / f"batch_{time.strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
        ok = [r for r in results if r.status == "ok"]
        for r in ok:
            (out_dir / f"{safe_stem(r.name)}_cleaned.txt").write_text(r.cleaned, encoding="utf-8")
            store.append_run({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source": f"batch:{r.name}",
                "chars_before": r.chars_before, "chars_after": r.chars_after,
                "reduction": r.reduction, "duration_ms": r.elapsed_ms,
                "stages": r.stages, "warnings": [],
            })
        zip_name = f"batch_{out_dir.name}.zip"
        zip_path = store.EXPORTS_DIR / zip_name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in ok:
                zf.writestr(f"{safe_stem(r.name)}_cleaned.txt", r.cleaned)
        out_ref.update(dir=out_dir, zip=zip_name)
        out_ref["results"] = results
        render_results()
        ui.notify(f"Processed {len(results)} item(s) — {len(ok)} ok.", type="positive")

    def preview_split() -> None:
        chunks = patients_mod.split(list_input.value or "")
        split_label.set_text(f"{len(chunks)} patient(s): " + ", ".join(c.label for c in chunks[:12])
                             + (" …" if len(chunks) > 12 else "") if chunks else "Paste a list first.")

    async def run_list() -> None:
        if state["running"]:
            return
        chunks = patients_mod.split(list_input.value or "")
        if not chunks:
            ui.notify("Paste a patient list first.", type="warning")
            return
        set_running(True)
        try:
            cfg = load_config(common.CONFIG_PATH)
            progress.update(done=0, total=len(chunks), current="")
            summarize_fn = None
            if ai_switch.value:
                llm_cfg = {**cfg, "local_llm": {**merge_llm_config(cfg), "prompt_preset": "one_liner",
                                                "custom_prompt": ""}}

                def summarize_fn(chart: str) -> str:
                    return summarize(chart, llm_cfg).text

            def report(done: int, total: int, result) -> None:
                progress.update(done=done, total=total, current=result.name)

            def work():
                return run_patient_texts([(c.label, c.text) for c in chunks], cfg,
                                         custom_dir=common.CUSTOM_DIR, on_progress=report,
                                         summarize=summarize_fn)

            publish(await run.io_bound(work))
        except ConfigError as e:
            ui.notify(str(e), type="negative")
        except Exception as e:
            report_error("Patient list failed", e)
        finally:
            set_running(False)

    async def run_now() -> None:
        if state["running"] or not pending:
            return
        set_running(True)
        try:
            cfg = load_config(common.CONFIG_PATH)
            files = list(pending)[:500]

            progress.update(done=0, total=len(files), current="")

            def report(done: int, total: int, result) -> None:
                progress.update(done=done, total=total, current=result.name)

            def work():
                return run_batch_files(files, cfg, custom_dir=common.CUSTOM_DIR,
                                       delta=bool(delta_switch.value), on_progress=report,
                                       note_presets=(dict(common.PREFS.get("note_presets") or {})
                                                     if note_switch.value else None),
                                       preset_loader=store.load_preset)

            results = await run.io_bound(work)
            publish(results)
        except ConfigError as e:
            ui.notify(str(e), type="negative")
        except Exception as e:
            report_error("Batch processing failed", e)
        finally:
            set_running(False)

    # ---- UI ------------------------------------------------------------------
    with shell("Batch clean", "/batch"):
        ui.label("Clean many charts in one run. Upload files or queue a folder — every "
                 "cleaned copy is saved to data/exports, and the per-file stats below show "
                 "exactly what your rules did.").classes("opacity-70")
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            ui.upload(on_upload=handle_upload, multiple=True, auto_upload=True) \
                .props("accept=.txt,.md,.docx,.pdf,text/plain flat").classes("max-w-xs")
            folder_input = ui.input("…or a folder path", placeholder="/path/to/charts") \
                .classes("w-96 cc-mono")
            ui.button("Add folder files", icon="create_new_folder",
                      on_click=add_folder).props("outline")
        with ui.row().classes("w-full items-center gap-2"):
            pending_label = ui.label("No files queued yet.").classes("text-sm flex-grow")
            run_btn = ui.button("Clean queued files", icon="auto_fix_high", on_click=run_now)
            run_btn.props("unelevated color=primary")
            run_btn.set_enabled(False)
            clear_btn = ui.button("Clear queue", icon="delete_sweep",
                                  on_click=clear_pending).props("flat")
            clear_btn.set_enabled(False)
            delta_switch = ui.switch("Only what changed between daily notes").tooltip(
                "Copy-forward delta view: keeps the first note in full, then only new or "
                "changed paragraphs from each later note.")
            note_switch = ui.switch("Use note-type presets",
                                    value=bool(common.PREFS.get("note_auto_apply"))).tooltip(
                "Detect each file's note type and clean it with the preset mapped to that "
                "type in Settings → Note types.").mark("batch-note-types")
            spinner = ui.spinner("dots", size="lg")
            spinner.set_visibility(False)
        with ui.column().classes("w-full gap-1").mark("batch-progress") as progress_row:
            progress_bar = ui.linear_progress(value=0, show_value=False).props("rounded size=10px")
            progress_label = ui.label("").classes("text-xs opacity-70")
        progress_row.set_visibility(False)
        ui.timer(0.3, tick_progress)
        with ui.expansion("…or paste a patient list (sign-out, census, rounding list)",
                          icon="groups").classes("w-full").mark("patient-list"):
            ui.label("Patients are split at bed labels (G20-1, Bed 4), Patient:/Name: lines, "
                     "numbered entries or separator lines; each one is cleaned on its own.") \
                .classes("text-xs opacity-70")
            list_input = ui.textarea("Patient list", on_change=lambda _: preview_split()) \
                .props("outlined input-style='min-height: 180px'").classes("w-full cc-mono") \
                .mark("patient-list-input")
            split_label = ui.label("Paste a list first.").classes("text-xs opacity-70") \
                .mark("patient-split")
            with ui.row().classes("items-center gap-2"):
                ui.button("Split & clean", icon="call_split", on_click=run_list) \
                    .props("unelevated color=primary").mark("patient-list-run")
                ai_switch = ui.switch("Add a one-liner per patient (local AI)").tooltip(
                    "Uses the on-device model (Ollama); left blank when no model answers.")
        results_col = ui.column().classes("w-full gap-3")


# ===========================================================================
# PAGE: Pipeline & Rules
# ===========================================================================
