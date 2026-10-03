"""The Clean page (/): paste or drop a chart, review the result."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers
from chartcleaner.trends import build as build_trends


# Stages whose deletions the Clean page lists under "Removed" for review.
REVIEWABLE_REMOVAL_STAGES = ("metadata_lines", "boilerplate", "learned_rules")
# Regex stages that honor stage_options.<sid>.exceptions ("Never remove this").
EXCEPTION_STAGES = ("metadata_lines", "boilerplate", "learned_rules", "literal_replacements")


async def clean_page():
    state = {"running": False}
    highlight = {"mode": "off", "pending": False, "undo": None}

    # ---- handlers (defined before the UI that references them) -------------
    def set_running(flag: bool) -> None:
        state["running"] = flag
        clean_btn.set_enabled(not flag)
        mode_sel.set_enabled(not flag)
        input_area.set_enabled(not flag)
        spinner.set_visibility(flag)

    async def do_clean_core(text: str, source: str) -> None:
        mode = mode_sel.value
        note_info: dict = {}
        set_running(True)
        try:
            def work():
                cfg = load_config(common.CONFIG_PATH)
                if mode != "clean":
                    return Pipeline(cfg, mode=mode).run(text, track_changes=True), None
                found = detect_note_type(text, cfg)
                note_info.update(label=found.label, preset=None)
                preset = (common.PREFS.get("note_presets") or {}).get(found.note_type or "")
                if common.PREFS.get("note_auto_apply") and preset and preset in store.list_presets():
                    # This run only: applying a preset for real would overwrite config.json.
                    cfg = store.load_preset(preset)
                    note_info["preset"] = preset
                # Tracked changes stay in memory for the inspect tabs; history drops them.
                result = Pipeline(cfg, custom_dir=common.CUSTOM_DIR).run(text, track_changes=True)
                return result, run_audit(result.text, cfg)

            def delta_work(cleaned: str):
                try:
                    return extract_note_deltas(cleaned)
                except Exception:
                    return None  # the delta view is a bonus; never fail a clean over it

            def trends_work(cleaned: str):
                try:
                    return build_trends(cleaned)
                except Exception:
                    return None  # same contract as the delta view

            result, audit = await run.io_bound(work)
            delta = await run.io_bound(delta_work, result.text) if mode == "clean" else None
            trends = await run.io_bound(trends_work, result.text) if mode == "clean" else None
            CLEAN_STATE.update(input=text, result_text=result.text, result=result, audit=audit,
                               summary=None, qa=[], result_mode=mode, delta=delta, note=note_info,
                               trends=trends)
            AUTO_LAST["text"] = text
            store.append_run(result.to_history_dict(
                f"{source}:{mode}" if mode != "clean" else source))
            # persist reversible-token maps produced by the tokenize stage
            try:
                for st in result.stages:
                    tmap = st.details.get("token_map")
                    if tmap:
                        store.save_token_map(tmap, source)
            except Exception:
                pass  # map saving must never break a run
            store.maybe_purge_old_data()
            try:
                if audit is not None:
                    store.append_audit_hits(f.signature for f in audit.findings)
            except Exception:
                pass  # history of findings must never break a run
            input_area.set_value(text)
            render_results()
        except ConfigError as e:
            ui.notify(str(e), type="negative")
        except Exception as e:
            report_error("Cleaning failed", e)
        finally:
            set_running(False)

    async def run_clean(_=None) -> None:
        text = input_area.value or ""
        if not text.strip():
            ui.notify("Nothing to clean — paste some text first.", type="warning")
            return
        if len(text) > 2_000_000:
            if mode_sel.value != "clean":
                ui.notify("Input over 2M characters; split it into smaller sections.", type="warning")
                return
            ui.notify("Input over 2M characters; truncated to 2M.", type="warning")
            text = text[:2_000_000]
        if state["running"]:
            return
        await do_clean_core(text, "editor")

    def render_results() -> None:
        results_col.clear()
        result = CLEAN_STATE.get("result")
        if not result:
            return
        with results_col:
            result_mode = CLEAN_STATE.get("result_mode")
            single_pass = result_mode in ("abbreviations", "expand")
            if result_mode == "abbreviations":
                ui.label("Abbreviations only — other text and formatting preserved. "
                         "PHI has not been removed.").classes("text-sm")
            elif result_mode == "expand":
                ui.label("Abbreviations expanded — other text and formatting preserved. "
                         "PHI has not been removed.").classes("text-sm")
            phi = result.phi_counts()
            chips = ui.row().classes("gap-3 flex-wrap items-stretch")
            stat_chip(chips, "characters", f"{result.chars_before:,} → {result.chars_after:,}")
            stat_chip(chips, "reduction", f"{result.reduction:+.1f}%",
                      "green" if result.reduction >= 0 else "orange")
            stat_chip(chips, "words", f"{result.words_before:,} → {result.words_after:,}", "indigo")
            if result_mode == "abbreviations":
                stat_chip(chips, "abbreviations applied", str(sum(s.matches for s in result.stages)))
            elif result_mode == "expand":
                stat_chip(chips, "abbreviations expanded", str(sum(s.matches for s in result.stages)))
                ambiguous = dict(result.stages[0].details.get("ambiguous") or {})
                if ambiguous:
                    stat_chip(chips, "ambiguous, left as written", str(sum(ambiguous.values())), "orange")
            else:
                stat_chip(chips, "PHI redacted", str(sum(phi.values())), "red")
            stat_chip(chips, "elapsed", f"{result.duration_ms:.0f} ms", "blue-grey")
            note = CLEAN_STATE.get("note") or {}
            if note.get("label") and result_mode == "clean":
                stat_chip(chips, "note type" + (f" · preset {note['preset']}" if note.get("preset") else ""),
                          note["label"], "teal")

            # ---- post-run review: what survived and deserves a second look ----
            audit = CLEAN_STATE.get("audit")
            flagged: set[int] = set()
            if audit is not None and not audit.skipped:
                total = sum(audit.counts.values())
                flagged = {f.line for f in audit.findings}
                if total or audit.errors:
                    with ui.expansion(
                            f"Review — {total} finding(s) to double-check before sharing",
                            icon="fact_check").classes("w-full"):
                        if audit.errors:
                            ui.label("Some checks could not run: " + " | ".join(audit.errors)) \
                                .classes("text-xs text-orange-600")
                        by_check: dict[str, list] = {}
                        for f in audit.findings:
                            by_check.setdefault(f.check, []).append(f)
                        for cid, items in by_check.items():
                            ui.label(f"{CHECK_LABELS.get(cid, cid)} — {audit.counts.get(cid, 0)}"
                                     f" · {CHECK_DESCRIPTIONS.get(cid, '')}") \
                                .classes("text-sm font-semibold mt-1")
                            for f in items[:10]:
                                with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
                                    ui.badge(f"line {f.line}").props("outline color=orange")
                                    ui.label(f.excerpt).classes("text-xs cc-mono flex-grow")
                                    ui.button("Build rule", icon="rule",
                                              on_click=lambda ff=f: start_pending_rule(
                                                  ff.suggested_regex, ff.suggested_replacement,
                                                  ff.suggested_stage)
                                              ).props("flat dense")
                            if len(items) > 10:
                                ui.label(f"… {len(items) - 10} more of this type") \
                                    .classes("text-xs opacity-60")
                        ui.label("Findings are hints, not verdicts — confirm before acting on them.") \
                            .classes("text-xs opacity-60 mt-1")
                else:
                    ui.label("✓ Audit: no leftover PHI patterns flagged.").classes("text-xs text-green-600")

            if result.fact_check is not None and not single_pass:
                render_fact_check(result.fact_check)

            abbr_changes = next((st.details.get("changes") or [] for st in result.stages
                                 if st.id == "medical_abbreviations"), [])
            with ui.tabs() as tabs:
                t_result = ui.tab("Result")
                t_diff = ui.tab("Side-by-side diff")
                t_stages = ui.tab("What each stage did")
                t_abbr = ui.tab(f"Abbreviations ({len(abbr_changes)})") if abbr_changes else None
                removed = removed_groups(result)
                t_removed = (ui.tab(f"Removed ({sum(len(g['items']) for g in removed)})")
                             if removed else None)
                trends = CLEAN_STATE.get("trends")
                t_trends = (ui.tab("Trends") if trends is not None and not trends.empty else None)
                delta = CLEAN_STATE.get("delta")
                t_delta = (ui.tab("Changes over time")
                           if delta is not None and delta.notes_found > 1 else None)
            with ui.tab_panels(tabs, value=t_result).classes("w-full"):
                if t_trends is not None:
                    with ui.tab_panel(t_trends):
                        render_trends(trends)
                if t_delta is not None:
                    with ui.tab_panel(t_delta):
                        render_delta(delta)
                if t_removed is not None:
                    with ui.tab_panel(t_removed):
                        render_removed(removed)
                if t_abbr is not None:
                    with ui.tab_panel(t_abbr):
                        render_abbreviation_changes(abbr_changes)
                with ui.tab_panel(t_result):
                    out = ui.textarea("", value=result.text)
                    out.props("outlined readonly input-style='min-height: 240px'").classes("w-full cc-mono")
                    with ui.row().classes("gap-2"):
                        ui.button("Copy result", icon="content_copy",
                                  on_click=lambda: copy_to_clipboard(result.text)).props("unelevated color=primary")
                        with ui.dropdown_button("Download", icon="download", auto_close=True):
                            ui.item("Text (.txt)", on_click=download_result)
                            ui.item("Word (.docx)", on_click=lambda: ui.download.content(
                                to_docx(CLEAN_STATE["result_text"]), "cleaned_chart.docx"))
                            ui.item("Markdown (.md)", on_click=lambda: ui.download.content(
                                chart_markdown().encode("utf-8"), "cleaned_chart.md"))
                        ui.button("Copy for Epic", icon="assignment",
                                  on_click=lambda: copy_to_clipboard(
                                      to_smartphrase(CLEAN_STATE["result_text"]),
                                      "Copied as plain text that pastes cleanly into Epic")) \
                            .props("flat").tooltip("Plain ASCII, no tabs, lines wrapped at 80 characters")
                        if common.PREFS.get("notes_folder"):
                            ui.button("Save to notes folder", icon="note_add",
                                      on_click=save_to_notes_folder).props("flat")
                        ui.button("Copy result + stats", icon="data_object",
                                  on_click=lambda: copy_to_clipboard(
                                      result.text + "\n\n<!-- " + result.summary() + " -->")).props("flat")
                        with ui.dropdown_button("Copy as prompt", icon="smart_toy", auto_close=True) \
                                .props("flat"):
                            for tmpl in prompt_templates(load_config(common.CONFIG_PATH)):
                                ui.item(tmpl["name"], on_click=lambda n=tmpl["name"]: copy_prompt(n))
                with ui.tab_panel(t_diff):
                    with ui.scroll_area().classes("w-full border rounded h-[420px] bg-grey-1 dark:bg-grey-10"):
                        ui.html(diff_html(CLEAN_STATE["input"], result.text, flagged))
                with ui.tab_panel(t_stages):
                    cols = [
                        {"name": "stage", "label": "Stage", "field": "stage", "align": "left"},
                        {"name": "matches", "label": "Matches", "field": "matches"},
                        {"name": "delta", "label": "Δ chars", "field": "delta"},
                        {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    ]
                    rows = []
                    for s in result.stages:
                        delta = s.chars_before - s.chars_after
                        if s.skipped:
                            status = "skipped"
                        elif s.error:
                            status = "error: " + s.error
                        else:
                            status = "ok"
                        rows.append({"stage": s.label, "matches": s.matches,
                                     "delta": f"{delta:+,}", "status": status})
                    ui.table(columns=cols, rows=rows, row_key="stage").classes("w-full").props("flat dense")
            if result_mode == "expand" and ambiguous:
                with ui.row().classes("items-center gap-2"):
                    ui.label("Not expanded because they have several meanings: "
                             + ", ".join(f"{a} ×{n}" for a, n in sorted(ambiguous.items())))\
                        .classes("text-sm")
                    ui.button("Choose meanings", icon="rule",
                              on_click=lambda amb=ambiguous: open_meanings_dialog(amb)).props("flat dense")
            if single_pass:
                return
            # ---- local AI summary (on-device via Ollama) ----
            summary_refs.clear()
            with ui.expansion("Local AI summary", icon="psychology").classes("w-full"):
                try:
                    opts = merge_llm_config(load_config(common.CONFIG_PATH))
                except Exception:
                    opts = merge_llm_config({})
                try:
                    probe = LocalLlmClient(str(opts["base_url"]), timeout=2.0)
                    listed = probe.list_models() if probe.is_available() else []
                except Exception:
                    listed = []
                model_val = str(opts["model"] or (listed[0] if listed else ""))
                model_options = list(listed)
                if model_val and model_val not in model_options:
                    model_options.append(model_val)
                if not listed:
                    ui.label(
                        f"No local LLM detected at {opts['base_url']} — install Ollama "
                        "(ollama.com) and pull a model, e.g. `ollama pull llama3.1`."
                    ).classes("text-xs text-orange-600")
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.select(model_options,
                              value=model_val or None, label="Model", new_value_mode="add",
                              on_change=lambda e: save_llm_pref("model", e.value)
                              ).classes("min-w-[190px]")
                    ui.select({"clinical": "Clinical sections", "brief": "Brief paragraph",
                               "findings": "Key findings", "custom": "Custom prompt"},
                              value=str(opts["prompt_preset"]), label="Prompt style",
                              on_change=lambda e: (save_llm_pref("prompt_preset", e.value),
                                                   custom_box.set_visibility(e.value == "custom"))
                              ).classes("min-w-[190px]")
                    summary_refs["button"] = ui.button("Summarize", icon="psychology",
                                                       on_click=run_summarize
                                                       ).props("unelevated color=primary")
                    summary_refs["spinner"] = ui.spinner("dots", size="sm")
                    summary_refs["spinner"].set_visibility(False)
                custom_box = ui.textarea("Custom prompt (replaces the preset)",
                                         value=str(opts["custom_prompt"]),
                                         on_change=lambda e: save_llm_pref("custom_prompt", e.value)
                                         ).props("outlined").classes("w-full cc-mono")
                custom_box.set_visibility(str(opts["prompt_preset"]) == "custom")
                summary_refs["output"] = ui.textarea("").props(
                    "outlined readonly input-style='min-height: 140px'"
                ).classes("w-full cc-mono")
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    summary_refs["grounding_row"] = ui.row().classes("items-center gap-1 flex-wrap")
                    summary_refs["meta"] = ui.label("").classes("text-xs opacity-60")
                render_summary_output()

            # ---- ask this chart (grounded local Q&A; design: .specs/plans/chart-qa) ----
            qa_refs.clear()
            with ui.expansion("Ask this chart", icon="forum").classes("w-full"):
                if not listed:
                    ui.label(
                        f"No local LLM detected at {opts['base_url']} — install Ollama "
                        "(ollama.com) and pull a model, e.g. `ollama pull llama3.1`."
                    ).classes("text-xs text-orange-600")
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    qa_refs["question"] = ui.input(
                        "Ask about this chart",
                        placeholder="e.g. What are the active antibiotics?").classes("min-w-[320px] flex-grow")
                    qa_refs["question"].on("keydown.enter", _ask_on_enter)
                    qa_refs["button"] = ui.button("Ask", icon="send", on_click=run_ask
                                                  ).props("unelevated color=primary")
                    qa_refs["spinner"] = ui.spinner("dots", size="sm")
                    qa_refs["spinner"].set_visibility(False)
                if model_val:
                    ui.label(f"answers on-device via {model_val} · every number/date in an answer is "
                             "verified against the chart").classes("text-xs opacity-60")
                qa_refs["log"] = ui.column().classes("w-full gap-2")
            render_qa_log()

            if result.warnings:
                ui.label("⚠ " + " | ".join(result.warnings)).classes("text-xs text-orange-600")

    def download_result() -> None:
        name = store.save_export(CLEAN_STATE["result_text"], "cleaned_chart")
        download_file(f"/exports/{name}", name)

    # ---- local AI summary (Ollama on-device; design: .specs/plans/local-ai-summarizer) ----
    summary_state = {"running": False}
    summary_refs: dict = {}  # panel widgets, repopulated by render_results

    def save_llm_pref(key: str, value) -> None:
        try:
            cfg = load_config(common.CONFIG_PATH)
            cfg["local_llm"] = {**merge_llm_config(cfg), key: value}
            save_config_with_backup(cfg)
        except Exception:
            pass  # preference saving must never break the panel

    def render_summary_output() -> None:
        res = CLEAN_STATE.get("summary")
        if not res or "output" not in summary_refs:
            return
        summary_refs["output"].set_value(res.text)
        summary_refs["meta"].set_text(f"model: {res.model} · {res.duration_ms:,} ms")
        g = res.grounding
        row = summary_refs["grounding_row"]
        row.clear()
        with row:
            if not g.total_entities:
                ui.badge("No checkable facts (no numbers/dates in output)", color="grey") \
                    .props("outline")
            elif not g.is_safe:
                ui.badge(f"UNGROUNDED — only {g.grounding_score}% of numbers/dates found in chart",
                         color="red")
            elif g.grounding_score >= 100.0:
                ui.badge(f"Grounded 100%", color="green")
            else:
                ui.badge(f"Grounded {g.grounding_score}%", color="amber")
            for item in g.ungrounded_entities:
                ui.button(item, icon="content_copy",
                          on_click=lambda _, it=item: copy_to_clipboard(it, "Copied ungrounded value")
                          ).props("flat dense size=sm color=red")

    async def run_summarize() -> None:
        if summary_state["running"] or not CLEAN_STATE.get("result_text"):
            return
        chart_result = CLEAN_STATE.get("result")
        chart_text = CLEAN_STATE["result_text"]
        summary_state["running"] = True
        sum_btn = summary_refs.get("button")
        sum_spin = summary_refs.get("spinner")
        if sum_btn:
            sum_btn.set_enabled(False)
        if sum_spin:
            sum_spin.set_visibility(True)
        try:
            def work():
                return summarize(chart_text, load_config(common.CONFIG_PATH))

            result = await run.io_bound(work)
            if (CLEAN_STATE.get("result") is not chart_result
                    or CLEAN_STATE.get("result_text") != chart_text):
                return
            CLEAN_STATE["summary"] = result
            render_summary_output()
        except LlmUnavailableError as e:
            ui.notify(
                f"{e} — install Ollama from ollama.com, pull a model "
                "(e.g. `ollama pull llama3.1`), then retry.",
                type="warning", multi_line=True)
        except NoModelError as e:
            ui.notify(str(e), type="warning", multi_line=True)
        except ConfigError as e:
            ui.notify(str(e), type="negative")
        except Exception as e:
            report_error("Summarization failed", e)
        finally:
            summary_state["running"] = False
            try:
                if sum_btn:
                    sum_btn.set_enabled(True)
                if sum_spin:
                    sum_spin.set_visibility(False)
            except Exception:
                pass  # the panel may have been re-rendered mid-run

    # ---- ask this chart (grounded Q&A over the cleaned text) ----
    qa_state = {"running": False}
    qa_refs: dict = {}  # panel widgets, repopulated by render_results

    def render_qa_log() -> None:
        if "log" not in qa_refs:
            return
        log_col = qa_refs["log"]
        log_col.clear()
        turns = CLEAN_STATE.get("qa") or []
        if not turns:
            return
        with log_col:
            for t in turns:
                ui.label("Q: " + t["q"]).classes("text-sm font-semibold")
                ui.markdown(t["a"]).classes("w-full")
                g = t["g"]
                with ui.row().classes("items-center gap-1 flex-wrap"):
                    if not g["total"]:
                        ui.badge("No checkable facts (no numbers/dates in answer)",
                                 color="grey").props("outline")
                    elif not g["safe"]:
                        ui.badge(f"UNGROUNDED — only {g['score']}% of numbers/dates found in chart",
                                 color="red")
                    elif g["score"] >= 100.0:
                        ui.badge("Grounded 100%", color="green")
                    else:
                        ui.badge(f"Grounded {g['score']}%", color="amber")
                    ui.button("Copy answer", icon="content_copy",
                              on_click=lambda _, a=t["a"]: copy_to_clipboard(a)
                              ).props("flat dense")
                    ui.label(f"{t['model']} · {t['ms']:,} ms").classes("text-xs opacity-60")
                ui.separator().classes("w-full opacity-30")

    def _ask_on_enter() -> None:
        asyncio.get_running_loop().create_task(run_ask())

    async def run_ask() -> None:
        if qa_state["running"] or not CLEAN_STATE.get("result_text"):
            return
        chart_result = CLEAN_STATE.get("result")
        chart_text = CLEAN_STATE["result_text"]
        q_box = qa_refs.get("question")
        question = (q_box.value or "").strip() if q_box else ""
        if not question:
            ui.notify("Type a question about the chart first.", type="warning")
            return
        qa_state["running"] = True
        btn, spin = qa_refs.get("button"), qa_refs.get("spinner")
        if btn:
            btn.set_enabled(False)
        if spin:
            spin.set_visibility(True)
        try:
            history = [QaTurn(t["q"], t["a"]) for t in (CLEAN_STATE.get("qa") or [])]

            def work():
                return ask_chart(question, chart_text,
                                 load_config(common.CONFIG_PATH), history=history)

            res = await run.io_bound(work)
            if (CLEAN_STATE.get("result") is not chart_result
                    or CLEAN_STATE.get("result_text") != chart_text):
                return
            CLEAN_STATE.setdefault("qa", []).append({
                "q": res.question, "a": res.answer,
                "g": {"score": res.grounding.grounding_score,
                      "safe": res.grounding.is_safe,
                      "total": res.grounding.total_entities},
                "model": res.model, "ms": res.duration_ms})
            if q_box:
                q_box.set_value("")
            render_qa_log()
        except LlmUnavailableError as e:
            ui.notify(
                f"{e} — install Ollama from ollama.com, pull a model "
                "(e.g. `ollama pull llama3.1`), then retry.",
                type="warning", multi_line=True)
        except NoModelError as e:
            ui.notify(str(e), type="warning", multi_line=True)
        except ConfigError as e:
            ui.notify(str(e), type="negative")
        except Exception as e:
            report_error("Chart Q&A failed", e)
        finally:
            qa_state["running"] = False
            try:
                if btn:
                    btn.set_enabled(True)
                if spin:
                    spin.set_visibility(False)
            except Exception:
                pass  # the panel may have been re-rendered mid-run

    async def paste_clipboard() -> None:
        try:
            import pyperclip
            text = await run.io_bound(pyperclip.paste)
            if text:
                input_area.set_value(text)
                CLEAN_STATE["input"] = text
            else:
                ui.notify("Clipboard is empty.", type="warning")
        except Exception as e:
            ui.notify(f"Clipboard error: {e}", type="negative")

    def clear_all() -> None:
        CLEAN_STATE.update(input="", result=None, result_text="", summary=None, qa=[])
        AUTO_LAST["text"] = None
        input_area.set_value("")
        results_col.clear()

    def load_sample() -> None:
        sample = BASE_DIR / "sample_chart.txt"
        if sample.exists():
            text = sample.read_text(encoding="utf-8")
            input_area.set_value(text)
            CLEAN_STATE["input"] = text

    async def handle_upload(e) -> None:
        try:
            name, data = await read_upload(e)
        except Exception as ex:
            ui.notify(f"Could not read the upload: {ex}", type="negative")
            return
        suffix = Path(name).suffix.lower()
        if suffix not in supported_extensions():
            ui.notify(f"Unsupported file type '{suffix}'", type="negative")
            return

        def work() -> tuple[str, str, list[str]]:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(data)
                tmp_path = Path(tf.name)
            try:
                ing = load_file(tmp_path, load_config(common.CONFIG_PATH))
                return ing.text, ing.engine, ing.warnings
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        try:
            text, engine, warns = await run.io_bound(work)
        except IngestError as ex:
            ui.notify(f"Could not ingest {name}: {ex}", type="negative")
            return
        except Exception as ex:
            report_error(f"Ingesting {name} failed", ex)
            return
        if CLEAN_STATE["input"].strip():
            CLEAN_STATE["input"] += "\n\n===== " + name + " =====\n" + text
        else:
            CLEAN_STATE["input"] = text
        input_area.set_value(CLEAN_STATE["input"])
        note = f"Loaded {name} via {engine}"
        if warns:
            note += " — " + " | ".join(warns[:2])
        ui.notify(note, type="positive")

    async def on_preset_change(e) -> None:
        name = e.value
        common.PREFS.update(last_preset=name)
        save_prefs()
        if not name:
            return
        try:
            cfg = store.load_preset(name)
            perrs, _ = validate_config(cfg)
            if perrs:
                ui.notify("Preset has invalid rules: " + "; ".join(perrs[:3]), type="negative")
                return
            save_config(cfg, common.CONFIG_PATH)
            ui.notify(f"Preset '{name}' applied.", type="positive")
        except Exception as ex:
            ui.notify(f"Could not apply preset: {ex}", type="negative")

    def on_key(e) -> None:
        try:
            mods = set(e.modifiers or [])
            if e.key == "Enter" and mods & {"Control", "Meta"}:
                asyncio.get_running_loop().create_task(run_clean())
        except Exception:
            pass

    async def auto_tick() -> None:
        if (not common.PREFS.get("auto_clean") or state["running"]
                or highlight["mode"] != "off" or highlight["pending"]):
            return
        text = input_area.value or ""
        if text.strip() and text != AUTO_LAST["text"]:
            AUTO_LAST["text"] = text
            await run_clean()
        elif not text.strip():
            AUTO_LAST["text"] = None

    # ---- learn a rule from highlighted text ---------------------------------

    def invalidate_output() -> None:
        CLEAN_STATE.update(input=input_area.value, result=None, result_text="", audit=None,
                           summary=None, qa=[], result_mode=None)
        AUTO_LAST["text"] = None
        results_col.clear()

    def set_highlight_mode(mode: str, checked: bool) -> None:
        if highlight.get("syncing"):
            return
        if checked:
            highlight["mode"] = mode
        elif highlight["mode"] == mode:
            highlight["mode"] = "off"
        highlight["syncing"] = True
        try:
            remove_check.set_value(highlight["mode"] == "remove")
            replace_check.set_value(highlight["mode"] == "replace")
            abbreviate_check.set_value(highlight["mode"] == "abbreviate")
        finally:
            highlight["syncing"] = False
        highlight_note.set_text({
            "off": "Select text normally, or check a mode to teach a rule.",
            "remove": "Highlight text to remove that selection immediately and remember it for future full cleans.",
            "replace": "Highlight text to enter a replacement and remember it for future full cleans.",
            "abbreviate": "Highlight a term to choose its abbreviation; it is added to your abbreviation dictionary.",
        }[highlight["mode"]])

    def apply_highlight(selection: dict, replacement: str) -> bool:
        if state["running"]:
            return False
        try:
            before = input_area.value or ""
            after = replace_selection(before, selection, replacement)
            if selection["text"] == replacement:
                ui.notify("The replacement is unchanged.", type="info")
                return False
            pair = make_rule(selection["text"], replacement,
                             case_sensitive=case_check.value, whole_words=word_check.value)
            cfg = load_config(common.CONFIG_PATH)
            rules = cfg.setdefault("learned_rules", [])
            if any(p[0] == pair[0] and p[1] != pair[1] for p in rules):
                ui.notify("This text already has a different rule. Edit it in My text rules.",
                          type="warning")
                return False
            added = pair not in rules
            old_enabled = cfg.get("stage_options", {}).get("learned_rules", {}).get("enabled")
            if added:
                rules.append(pair)
            cfg.setdefault("stage_options", {}).setdefault("learned_rules", {})["enabled"] = True
            errors, _ = validate_config(cfg)
            if errors:
                raise ValueError(errors[0])
            save_config_with_backup(cfg)
            if added:
                remember_rule_example(pair, selection["text"], cfg)
            highlight["undo"] = dict(before=before, after=after, pair=pair, added=added,
                                     rules=[list(p) for p in rules], old_enabled=old_enabled)
            input_area.set_value(after)
            invalidate_output()
            undo_btn.enable()
            highlight_status.set_text("Selection updated. Rule saved for future full cleans."
                                      + (" Learned rules enabled." if old_enabled is False else ""))
            return True
        except Exception as ex:
            ui.notify(str(ex), type="negative")
            return False

    def apply_abbreviation(selection: dict, term: str, abbreviation: str, acknowledged: bool) -> bool:
        if state["running"]:
            return False
        try:
            before = input_area.value or ""
            text = selection["text"]
            lead = text[:len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            after = replace_selection(before, selection, lead + abbreviation + trail)
            cfg = load_config(common.CONFIG_PATH)
            old_group = cfg.get("abbreviations")
            old_enabled = cfg.get("stage_options", {}).get("medical_abbreviations", {}).get("enabled")
            cfg = abbreviation_with_custom(cfg, term, abbreviation, acknowledged=acknowledged)
            if old_enabled is False:
                cfg.setdefault("stage_options", {}).setdefault("medical_abbreviations", {})["enabled"] = True
            errors, _ = validate_config(cfg)
            if errors:
                raise ValueError(errors[0])
            save_config_with_backup(cfg)
            highlight["undo"] = dict(kind="abbreviation", before=before, after=after,
                                     old_group=old_group, new_group=cfg["abbreviations"],
                                     old_enabled=old_enabled)
            input_area.set_value(after)
            invalidate_output()
            undo_btn.enable()
            highlight_status.set_text(
                f"“{term}” → “{abbreviation}” added to your abbreviations."
                + (" Medical abbreviations stage enabled." if old_enabled is False else ""))
            return True
        except Exception as ex:
            ui.notify(str(ex), type="negative")
            return False

    def undo_abbreviation(undo: dict) -> None:
        cfg = load_config(common.CONFIG_PATH)
        if cfg.get("abbreviations") != undo["new_group"]:
            raise ValueError("Abbreviations changed since that edit. Review them in My text rules.")
        if undo["old_group"] is None:
            cfg.pop("abbreviations", None)
        else:
            cfg["abbreviations"] = undo["old_group"]
        if undo["old_enabled"] is False:
            cfg.setdefault("stage_options", {}).setdefault("medical_abbreviations", {})["enabled"] = False
        save_config_with_backup(cfg)

    def open_abbreviate_dialog(selection: dict) -> None:
        term = selection["text"].strip()
        if not term:
            ui.notify("Highlight a term to abbreviate.", type="info")
            return
        cfg = load_config(common.CONFIG_PATH)
        highlight["pending"] = True
        with ui.dialog() as dlg, ui.card().classes("w-full max-w-xl gap-3"):
            ui.label("Abbreviate highlighted text").classes("text-lg font-semibold")
            ui.label(term).classes("whitespace-pre-wrap break-all max-h-40 overflow-auto font-medium")
            abbr_input = ui.input("Abbreviation", value=suggest_abbreviation(term, cfg)) \
                .props("outlined autofocus").classes("w-full")
            issues_box = ui.column().classes("gap-1")
            hits = ui.label("").classes("text-sm opacity-80")

            def sync() -> None:
                abbr = (abbr_input.value or "").strip()
                if not abbr:
                    issues_box.clear()
                    hits.set_text("")
                    return
                if lookup_abbreviation(term, cfg) == abbr:
                    issues_box.clear()
                    hits.set_text(f"Already in your dictionary: “{term}” → “{abbr}” on every clean.")
                    return
                show_abbreviation_issues(issues_box, abbreviation_check(term, abbr, cfg))
                count = abbreviation_preview(input_area.value or "", cfg, term, abbr)["count"]
                hits.set_text(f"Would change {count} place(s) in this chart on the next clean.")

            abbr_input.on_value_change(lambda _: sync())
            sync()

            def save() -> None:
                abbr = (abbr_input.value or "").strip()
                if not abbr or abbr == term:
                    ui.notify("Enter a different, shorter form.", type="warning")
                    return

                def persist(acknowledged: bool) -> None:
                    if apply_abbreviation(selection, term, abbr, acknowledged):
                        dlg.close()

                save_abbreviation_with_safety(term, abbr, load_config(common.CONFIG_PATH), persist)

            expanded, n_expanded, _ = expand_abbreviations(term, cfg)
            can_expand = n_expanded == 1 and expanded != term

            def expand_instead() -> None:
                text = selection["text"]
                lead = text[:len(text) - len(text.lstrip())]
                trail = text[len(text.rstrip()):]
                try:
                    after = replace_selection(input_area.value or "", selection, lead + expanded + trail)
                except ValueError as ex:
                    ui.notify(str(ex), type="warning")
                    return
                input_area.set_value(after)
                invalidate_output()
                highlight_status.set_text(f"“{term}” expanded to “{expanded}” in this chart (no rule saved).")
                dlg.close()

            ui.label("Updates this selection now and adds the term to your abbreviation dictionary "
                     "(used by Full clean and Abbreviations only).").classes("text-sm opacity-70")
            with ui.row():
                ui.button("Abbreviate & remember", on_click=save).props("unelevated")
                if can_expand:
                    ui.button(f"Expand instead → {expanded}", on_click=expand_instead).props("flat")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.on("hide", lambda: (highlight.update(pending=False), dlg.delete()))
        dlg.open()

    def undo_highlight() -> None:
        undo = highlight["undo"]
        if not undo or state["running"]:
            return
        if input_area.value != undo["after"]:
            ui.notify("The chart changed since that edit. Use My text rules to remove the saved rule.",
                      type="warning")
            return
        if undo.get("kind") == "abbreviation":
            try:
                undo_abbreviation(undo)
                input_area.set_value(undo["before"])
                invalidate_output()
                highlight["undo"] = None
                undo_btn.disable()
                highlight_status.set_text("Edit undone; your previous abbreviations are restored.")
            except Exception as ex:
                ui.notify(str(ex), type="negative")
            return
        try:
            cfg = load_config(common.CONFIG_PATH)
            if cfg.get("learned_rules", []) != undo["rules"]:
                raise ValueError("Saved rules changed since that edit. Review them in My text rules.")
            if undo["added"]:
                cfg["learned_rules"].remove(undo["pair"])
            opts = cfg.setdefault("stage_options", {}).setdefault("learned_rules", {})
            if undo["old_enabled"] is None:
                opts.pop("enabled", None)
            else:
                opts["enabled"] = undo["old_enabled"]
            save_config_with_backup(cfg)
            input_area.set_value(undo["before"])
            invalidate_output()
            highlight["undo"] = None
            undo_btn.disable()
            highlight_status.set_text("Edit undone; the previous saved rules are restored.")
        except Exception as ex:
            ui.notify(str(ex), type="negative")

    def on_highlight(e) -> None:
        if highlight["mode"] == "off" or highlight["pending"] or state["running"]:
            return
        selection = e.args
        if not isinstance(selection, dict):
            return
        try:
            replace_selection(input_area.value or "", selection, "")
        except ValueError as ex:
            ui.notify(str(ex), type="warning")
            return
        if highlight["mode"] == "remove":
            apply_highlight(selection, "")
            return
        if highlight["mode"] == "abbreviate":
            open_abbreviate_dialog(selection)
            return
        highlight["pending"] = True
        with ui.dialog() as dlg, ui.card().classes("w-full max-w-xl gap-3"):
            ui.label("Replace highlighted text").classes("text-lg font-semibold")
            ui.label(selection["text"]).classes("whitespace-pre-wrap break-all max-h-40 overflow-auto")
            replacement = ui.textarea("Replace with").props("outlined autofocus").classes("w-full")
            ui.label("Updates this selection now and saves a rule for future full cleans. "
                     "An empty replacement removes the selection.").classes("text-sm opacity-70")

            def save_replacement() -> None:
                if apply_highlight(selection, replacement.value or ""):
                    dlg.close()

            with ui.row():
                ui.button("Replace & remember", on_click=save_replacement).props("unelevated")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.on("hide", lambda: (highlight.update(pending=False), dlg.delete()))
        dlg.open()

    def open_learn_dialog(selected: str) -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[760px] gap-2"):
            ui.label("Learn a rule from highlighted text").classes("font-semibold text-blue-600")
            ui.label("The rule joins your other rules (Pipeline & Rules → Learned rules) and is "
                     "applied on every future Clean. It is saved in config.json, so it also "
                     "travels with a settings export.").classes("text-xs opacity-60 -mt-2")
            txt = ui.textarea("Highlighted text (edit it down to exactly what should change)",
                              value=selected) \
                .props("outlined input-style='min-height: 90px'").classes("w-full cc-mono")
            mode = ui.radio(
                {LEARN_REMOVE_TEXT: "Remove this text wherever it appears",
                 LEARN_REMOVE_LINES: "Remove whole line(s) that are exactly this text",
                 LEARN_REPLACE: "Replace this text with…"},
                value=LEARN_REMOVE_TEXT).props("dense")
            repl = ui.input("Replace with (used by the last option only)",
                            value="").props("outlined dense").classes("w-full")
            hit_lbl = ui.label("").classes("text-xs opacity-80")

            def sync_hits() -> None:
                m = mode.value or LEARN_REMOVE_TEXT
                pat = learned_pattern(txt.value, m)
                n = count_matches(pat, input_area.value or "", re.IGNORECASE) if pat else 0
                hit_lbl.set_text(
                    f"Preview: matches {n} time(s) in your current chart" if n >= 0
                    else "Preview: pattern problem — adjust the highlighted text")

            txt.on_value_change(lambda e: sync_hits())
            mode.on_value_change(lambda e: sync_hits())
            sync_hits()

            def save_learned() -> None:
                snippet = (txt.value or "").replace("\r\n", "\n").strip()
                m = mode.value or LEARN_REMOVE_TEXT
                if not snippet:
                    ui.notify("Nothing to learn — the highlighted text is empty.", type="warning")
                    return
                if m == LEARN_REPLACE and not (repl.value or "").strip():
                    ui.notify("Enter the replacement text, or pick one of the remove options.",
                              type="warning")
                    return
                try:
                    cfg = load_config(common.CONFIG_PATH)
                    pair = [learned_pattern(snippet, m),
                            (repl.value or "").replace("\\", "\\\\") if m == LEARN_REPLACE else ""]
                    if pair not in cfg.setdefault("learned_rules", []):
                        cfg["learned_rules"].append(pair)
                    cfg.setdefault("stage_options", {}).setdefault("learned_rules", {})["enabled"] = True
                    errs, _ = validate_config(cfg)
                    if errs:
                        ui.notify("Cannot save rule — " + errs[0], type="negative")
                        return
                    save_config_with_backup(cfg)
                    remember_rule_example(pair, snippet, cfg)
                except Exception as ex:
                    report_error("Could not save the learned rule", ex)
                    return
                dlg.close()
                verb = "replace" if m == LEARN_REPLACE else "remove"
                ui.notify(f"Rule saved — matches will be {verb}d every time you Clean "
                          "(manage them on the Pipeline page → Learned rules).", type="positive")

            with ui.row().classes("gap-2"):
                ui.button("Save rule", icon="school", on_click=save_learned) \
                    .props("unelevated color=primary")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    async def learn_from_selection() -> None:
        try:
            sel = await ui.run_javascript(
                "(function(){var ta=document.querySelector('.cc-learn-src textarea');"
                "if(!ta)return '';"
                "var s=ta.selectionStart,e=ta.selectionEnd;"
                "return (s===e)?'':ta.value.substring(s,e);})()")
        except Exception:
            sel = None
        if not sel or not str(sel).strip():
            ui.notify("Highlight some text in the chart above first, then click Learn.",
                      type="info")
            return
        open_learn_dialog(str(sel))

    def removed_groups(result) -> list[dict]:
        """Text deleted by the line, block and learned-rule stages, grouped by rule."""
        groups: dict[tuple[str, str], dict] = {}
        for st in result.stages:
            if st.id not in REVIEWABLE_REMOVAL_STAGES:
                continue
            for c in st.details.get("changes") or []:
                if c["after"] != "" or not c["before"].strip():
                    continue
                g = groups.setdefault((st.id, c["rule"]), {"sid": st.id, "stage": st.label,
                                                           "rule": c["rule"], "items": []})
                g["items"].append(c["before"])
        return sorted(groups.values(), key=lambda g: (-len(g["items"]), g["stage"]))

    def render_removed(groups: list[dict]) -> None:
        ui.label("Everything the line, block and learned-rule stages deleted. If something "
                 "clinical was removed, “Never remove this” keeps it on every future clean.") \
            .classes("text-sm opacity-70")
        for g in groups:
            with ui.expansion(f"{g['stage']} — {len(g['items'])} removed",
                              caption=g["rule"][:120]).classes("w-full"):
                seen: list[str] = []
                for text in g["items"]:
                    if text.strip() not in seen:
                        seen.append(text.strip())
                for text in seen[:12]:
                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                        ui.label(text[:300] + ("…" if len(text) > 300 else "")) \
                            .classes("text-xs cc-mono flex-grow whitespace-pre-wrap break-all")
                        ui.button(icon="content_copy",
                                  on_click=lambda t=text: copy_to_clipboard(t)).props("flat dense")
                        ui.button("Never remove this", icon="shield",
                                  on_click=lambda t=text, sid=g["sid"]: keep_text(sid, t)) \
                            .props("flat dense").mark("never-remove")
                if len(seen) > 12:
                    ui.label(f"… {len(seen) - 12} more").classes("text-xs opacity-60")

    def render_fact_check(report) -> None:
        """The "Nothing clinical lost" badge and, when something went, the list."""
        status = report.status
        icon, color = {"ok": ("verified", "green"), "review": ("rule", "orange"),
                       "alert": ("report", "red")}[status]
        if not report.losses:
            with ui.row().classes("items-center gap-1").mark("fact-check"):
                ui.icon(icon, color=color)
                ui.label(report.headline()).classes(f"text-sm text-{color}")
            return
        titles = {"unexpected": "Lost by a stage that should only reformat",
                  "rule": "Removed by a removal rule",
                  "by_design": "Dropped by a section or summary setting"}
        with ui.expansion(report.headline(), icon=icon, value=status == "alert") \
                .classes(f"w-full text-{color}").mark("fact-check"):
            ui.label("Numbers with units, lab and vital values, medications and safety words "
                     "(allergies, code status) are counted before and after every stage.") \
                .classes("text-xs opacity-70")
            for cat in ("unexpected", "rule", "by_design"):
                items = [x for x in report.losses if x.category == cat]
                if not items:
                    continue
                ui.label(titles[cat]).classes("text-sm font-semibold mt-1")
                for x in items[:20]:
                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                        ui.badge(x.display + (f" ×{x.count}" if x.count > 1 else "")) \
                            .props(f"outline color={'red' if cat == 'unexpected' else 'orange' if cat == 'rule' else 'grey'}")
                        ui.label(f"{x.stage_label}: {x.lines[0][:240] if x.lines else ''}") \
                            .classes("text-xs cc-mono flex-grow whitespace-pre-wrap break-all")
                        if x.lines:
                            ui.button(icon="content_copy",
                                      on_click=lambda t=x.lines[0]: copy_to_clipboard(t)).props("flat dense")
                        if x.lines and x.stage_id in EXCEPTION_STAGES:
                            ui.button("Never remove this", icon="shield",
                                      on_click=lambda t=x.lines[0], sid=x.stage_id: keep_text(sid, t)) \
                                .props("flat dense")
                if len(items) > 20:
                    ui.label(f"… {len(items) - 20} more").classes("text-xs opacity-60")

    async def keep_text(sid: str, text: str) -> None:
        cfg = load_config(common.CONFIG_PATH)
        options = cfg.setdefault("stage_options", {}).setdefault(sid, {})
        keep = options.setdefault("exceptions", [])
        snippet = text.strip()[:200]
        if snippet not in keep:
            keep.append(snippet)
        save_config_with_backup(cfg)
        ui.notify("Saved — this text will be kept on future cleans.", type="positive")
        await run_clean()

    def chart_markdown() -> str:
        note = CLEAN_STATE.get("note") or {}
        return to_markdown(CLEAN_STATE["result_text"], note_type=note.get("label", "")
                           if note.get("label") != "Not sure" else "")

    def save_to_notes_folder() -> None:
        try:
            path = save_to_vault(chart_markdown(), common.PREFS["notes_folder"])
        except Exception as ex:
            ui.notify(f"Could not save: {ex}", type="negative")
            return
        ui.notify(f"Saved {path.name} to your notes folder.", type="positive")

    def copy_prompt(name: str) -> None:
        try:
            text = render_prompt(name, CLEAN_STATE.get("result_text") or "", load_config(common.CONFIG_PATH))
        except Exception as ex:
            ui.notify(f"Could not build the prompt: {ex}", type="negative")
            return
        copy_to_clipboard(text, f"“{name}” prompt copied — paste it into your AI tool")

    def render_trends(trends) -> None:
        ui.label(f"Across {len(trends.notes)} notes: each lab's last value per note "
                 "(— = not in that note) and medication-list changes between notes.") \
            .classes("text-sm opacity-70")
        if trends.labs:
            cols = [{"name": "lab", "label": "Lab", "field": "lab", "align": "left"}] + [
                {"name": f"n{i}", "label": label, "field": f"n{i}"}
                for i, label in enumerate(trends.notes)] + [
                {"name": "dir", "label": "", "field": "dir"}]
            rows = [{"lab": t.name, "dir": t.direction(),
                     **{f"n{i}": v or "—" for i, v in enumerate(t.values)}} for t in trends.labs]
            ui.table(columns=cols, rows=rows, row_key="lab").classes("w-full").props("flat dense") \
                .mark("trends-table")
        for change in trends.meds:
            with ui.column().classes("gap-0 mt-1"):
                ui.label(f"{change.before} → {change.after}").classes("text-sm font-semibold")
                for m in change.started:
                    ui.label(f"+ started {m}").classes("text-xs cc-mono text-green-700")
                for m in change.stopped:
                    ui.label(f"− stopped {m}").classes("text-xs cc-mono text-red-700")
                for a, b in change.changed:
                    ui.label(f"~ {a} → {b}").classes("text-xs cc-mono text-orange-700")
        block = trends.to_text()

        def prepend() -> None:
            copy_to_clipboard(f"{block}\n\n{CLEAN_STATE['result_text']}", "Result with trends copied")

        with ui.row().classes("gap-2"):
            ui.button("Copy trends", icon="content_copy",
                      on_click=lambda: copy_to_clipboard(block)).props("unelevated")
            ui.button("Copy result with trends on top", icon="vertical_align_top",
                      on_click=prepend).props("flat")

    def render_delta(delta) -> None:
        ui.label(f"{delta.notes_found} daily notes · {delta.compression_ratio}% copied-forward text "
                 "removed. The first note is kept in full; later notes show only sentences that are "
                 "new or changed.").classes("text-sm opacity-70")
        box = ui.textarea("", value=delta.compact_text)
        box.props("outlined readonly input-style='min-height: 240px'").classes("w-full cc-mono")

        def use_for_ai() -> None:
            CLEAN_STATE["result_text"] = delta.compact_text
            ui.notify("Local AI summary and Ask this chart now use the changes-over-time view.",
                      type="positive")

        with ui.row().classes("gap-2"):
            ui.button("Copy", icon="content_copy",
                      on_click=lambda: copy_to_clipboard(delta.compact_text)).props("unelevated")
            ui.button("Use for AI summary & questions", icon="psychology",
                      on_click=use_for_ai).props("flat")

    def render_abbreviation_changes(changes: list[dict]) -> None:
        """Every abbreviation applied in this result, with quick fixes."""
        cfg = load_config(common.CONFIG_PATH)
        custom = {c["term"].casefold(): c for c in
                  normalize_abbreviation_settings(cfg.get("abbreviations"))["custom"]}
        groups: dict[tuple[str, str], dict] = {}
        for c in changes:
            key = (c["rule"], c["after"])
            g = groups.setdefault(key, {"term": c["before"], "rule": c["rule"], "after": c["after"],
                                        "source": c.get("source", "bundled"), "count": 0})
            g["count"] += 1
        ui.label("Each abbreviation applied to this result. Disable or change one and the chart "
                 "is cleaned again.").classes("text-sm opacity-70")
        for g in sorted(groups.values(), key=lambda g: (-g["count"], g["rule"])):
            entry = custom.get(g["rule"])
            source = (f"pack: {entry['pack']}" if entry and entry.get("pack")
                      else "my rule" if g["source"] == "custom" else "dictionary")
            with ui.row().classes("w-full items-center gap-2 border-b pb-1"):
                ui.label(g["term"]).classes("font-medium min-w-[240px]")
                ui.label("→")
                ui.label(g["after"]).classes("min-w-[80px]")
                ui.badge(f"×{g['count']}").props("outline")
                ui.label(source).classes("text-xs opacity-70 flex-grow")
                ui.button("Change", icon="edit",
                          on_click=lambda gg=g: change_abbreviation(gg)).props("flat dense")
                ui.button("Disable", icon="block",
                          on_click=lambda gg=g: disable_abbreviation(gg)) \
                    .props("flat dense color=negative").mark("abbr-disable")

    async def disable_abbreviation(group: dict) -> None:
        cfg = load_config(common.CONFIG_PATH)
        settings = normalize_abbreviation_settings(cfg.get("abbreviations"))
        match = next((c for c in settings["custom"] if c["term"].casefold() == group["rule"]), None)
        if match is not None:
            match["enabled"] = False
        else:
            settings["disabled"].append(group["term"])
        cfg["abbreviations"] = normalize_abbreviation_settings(settings)
        save_config_with_backup(cfg)
        ui.notify(f"“{group['term']}” will no longer be abbreviated.", type="positive")
        await run_clean()

    def change_abbreviation(group: dict) -> None:
        with ui.dialog() as dlg, ui.card().classes("w-full max-w-md gap-2"):
            ui.label(f"Abbreviate “{group['term']}” as…").classes("text-lg font-semibold")
            new = ui.input("Abbreviation", value=group["after"]).props("outlined autofocus").classes("w-full")

            def save() -> None:
                value = (new.value or "").strip()
                if not value:
                    ui.notify("Enter an abbreviation.", type="warning")
                    return

                async def persist(acknowledged: bool) -> None:
                    cfg = abbreviation_with_custom(load_config(common.CONFIG_PATH), group["term"], value,
                                                   acknowledged=acknowledged)
                    save_config_with_backup(cfg)
                    dlg.close()
                    await run_clean()

                save_abbreviation_with_safety(
                    group["term"], value, load_config(common.CONFIG_PATH),
                    lambda ack: asyncio.get_running_loop().create_task(persist(ack)))

            with ui.row():
                ui.button("Save & clean again", on_click=save).props("unelevated")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def open_meanings_dialog(ambiguous: dict[str, int]) -> None:
        keep = "(leave as written)"
        cfg = load_config(common.CONFIG_PATH)
        current = normalize_abbreviation_settings(cfg.get("abbreviations")).get("expand_prefer", {})
        with ui.dialog() as dlg, ui.card().classes("w-full max-w-xl gap-2"):
            ui.label("Choose what each abbreviation means").classes("text-lg font-semibold")
            ui.label("Saved to your abbreviation settings and used every time you expand.") \
                .classes("text-sm opacity-70")
            picks = {}
            for abbr in sorted(ambiguous):
                options = abbreviation_meanings(abbr) + [keep]
                picks[abbr] = ui.select(options, label=abbr,
                                        value=current.get(abbr) if current.get(abbr) in options else keep) \
                    .classes("w-full")

            async def save() -> None:
                cfg = load_config(common.CONFIG_PATH)
                group = normalize_abbreviation_settings(cfg.get("abbreviations"))
                prefer = dict(group.get("expand_prefer", {}))
                for abbr, control in picks.items():
                    if control.value and control.value != keep:
                        prefer[abbr] = control.value
                    else:
                        prefer.pop(abbr, None)
                group["expand_prefer"] = prefer
                cfg["abbreviations"] = normalize_abbreviation_settings(group)
                save_config_with_backup(cfg)
                dlg.close()
                await run_clean()

            with ui.row():
                ui.button("Save & expand again", on_click=save).props("unelevated")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    # ---- UI ------------------------------------------------------------------
    def on_mode_change(e) -> None:
        CLEAN_STATE.update(mode=e.value, result=None, result_text="", audit=None,
                           summary=None, qa=[], result_mode=None)
        AUTO_LAST["text"] = None
        results_col.clear()
        sync_mode_controls()

    def sync_mode_controls() -> None:
        mode = mode_sel.value
        clean_btn.set_text({"abbreviations": "Apply abbreviations",
                            "expand": "Expand abbreviations"}.get(mode, "Clean"))
        preset_sel.set_enabled(mode == "clean")
        mode_note.set_text({
            "abbreviations": "Shortens full medical terms using your abbreviation dictionary. Other text "
                             "and formatting are preserved; PHI is not removed.",
            "expand": "Spells abbreviations out in full, for colleagues, patients or the AI tools. "
                      "Abbreviations with several meanings are left as written until you choose one. "
                      "PHI is not removed.",
        }.get(mode, "Runs your cleaning pipeline, including your medical abbreviation dictionary."))

    with shell("Clean a chart", "clean"):
        errs, _warns = validate_config(load_config(common.CONFIG_PATH))
        if errs:
            with ui.card().classes("w-full border-red-400"):
                ui.label("Current config has problems — fix them on the Pipeline page").classes("font-semibold text-red-600")
                for e in errs[:5]:
                    ui.label(f"• {e}").classes("text-xs text-red-500")

        mode_sel = ui.toggle({"clean": "Full clean", "abbreviations": "Abbreviations only",
                              "expand": "Expand abbreviations"},
                             value=CLEAN_STATE.get("mode", "clean"),
                             on_change=on_mode_change)
        mode_note = ui.label("").classes("text-sm opacity-70")
        presets = store.list_presets()
        preset_sel = ui.select(
            options={**{p: f"📦 {p}" for p in presets}, "": "(config.json — current rules)"},
            value=common.PREFS.get("last_preset") if common.PREFS.get("last_preset") in presets else "",
            label="Rule preset",
        ).classes("w-60")

        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            clean_btn = ui.button("Clean", icon="auto_fix_high", on_click=run_clean)
            clean_btn.mark("run-clean")
            clean_btn.props("unelevated color=primary")
            spinner = ui.spinner("dots", size="lg")
            spinner.set_visibility(False)
            ui.button("Paste from clipboard", icon="content_paste", on_click=paste_clipboard).props("outline")
            ui.button("Learn rule from selection", icon="highlight", on_click=learn_from_selection) \
                .props("flat").tooltip("Advanced: remove text, whole lines, or save a replacement")
            ui.button("Clear", icon="delete_sweep", on_click=clear_all).props("flat")
        with ui.row().classes("w-full items-center gap-2"):
            ui.switch("Auto-clean as I type", value=bool(common.PREFS.get("auto_clean")),
                      on_change=lambda e: (common.PREFS.update(auto_clean=e.value), save_prefs()))
            ui.label("Tip: Ctrl/⌘+Enter cleans.").classes("text-xs opacity-60 ml-auto")

        with ui.card().classes("w-full gap-2 bg-blue-50 dark:bg-slate-900"):
            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                ui.icon("highlight").classes("text-primary")
                ui.label("Highlight to teach").classes("font-semibold")
                remove_check = ui.checkbox("Remove mode", value=False,
                    on_change=lambda e: set_highlight_mode("remove", e.value))
                replace_check = ui.checkbox("Replace mode", value=False,
                    on_change=lambda e: set_highlight_mode("replace", e.value))
                abbreviate_check = ui.checkbox("Abbreviate mode", value=False,
                    on_change=lambda e: set_highlight_mode("abbreviate", e.value))
                undo_btn = ui.button("Undo last highlight", icon="undo", on_click=undo_highlight).props("flat dense")
                undo_btn.disable()
            highlight_note = ui.label("Select text normally, or check a mode to teach a rule.").classes("text-sm")
            with ui.row().classes("items-center gap-3 flex-wrap"):
                word_check = ui.checkbox("Whole words only", value=True)
                case_check = ui.checkbox("Match case", value=False)
                ui.button("Manage & share rules", icon="tune",
                          on_click=lambda: ui.navigate.to("/rules")).props("flat dense")
            ui.label("Auto-clean pauses while a highlighting mode is checked. Undo restores the last edit and its rule.") \
                .classes("text-xs opacity-70")
            highlight_status = ui.label("").classes("text-sm text-primary").props("role=status aria-live=polite")

        if HAS_EX4:
            # ex4nicegui gives the textarea a reactive value signal; the plain
            # NiceGUI element underneath keeps every existing code path intact.
            _rx_input = rxui.textarea(
                "Chart text (paste an Epic export, drop a file, or load the sample)",
                value=CLEAN_STATE["input"],
                on_change=lambda e: CLEAN_STATE.update(input=e.value))
            input_area = _rx_input.element
            input_area.props("outlined input-style='min-height: 220px'") \
                .classes("w-full cc-mono cc-learn-src")
        else:
            input_area = ui.textarea("Chart text (paste an Epic export, drop a file, or load the sample)",
                                     value=CLEAN_STATE["input"],
                                     on_change=lambda e: CLEAN_STATE.update(input=e.value))
            input_area.props("outlined input-style='min-height: 220px'") \
                .classes("w-full cc-mono cc-learn-src")

        ui.label().bind_text_from(input_area, "value", lambda t:
            f"{len(t or ''):,} chars · {len((t or '').split()):,} words").classes("text-xs opacity-60")

        input_area.mark("highlight-source")
        for event_name in ("mouseup", "keyup", "touchend"):
            input_area.on(event_name, on_highlight, js_handler=SELECTION_HANDLER)

        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            ui.upload(on_upload=handle_upload, multiple=True, auto_upload=True) \
                .props("accept=.txt,.md,.docx,.pdf,text/plain flat").classes("max-w-xs")
            ui.button("Load sample chart", icon="science", on_click=load_sample).props("flat")

        results_col = ui.column().classes("w-full gap-3")
        sync_mode_controls()

        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Many files to clean?").classes("text-xs opacity-60")
            ui.button("Open Batch page", icon="layers",
                      on_click=lambda: ui.navigate.to("/batch")).props("flat dense")

        preset_sel.on_value_change(on_preset_change)
        ui.keyboard(on_key=on_key)
        ui.timer(1.2, auto_tick)

        if CLEAN_STATE.get("result"):
            render_results()


# ===========================================================================
# PAGE: Batch
# ===========================================================================
