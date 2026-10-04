"""The Clean page (/): paste or drop a chart, review the result.

Layout: the chart on the left, the result on the right (stacked on narrow
windows), and the local AI (summary + "Ask this chart") in a drawer on the
right. The result has three tabs — **Output** (copy as…, timeline),
**Review** (every change, clickable, plus what was removed and why) and
**Insights** (trends, changes over time).
"""

from __future__ import annotations

import time

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers
from chartcleaner import edit_log, recent_charts, regression_set, rule_inbox
from chartcleaner import service as service_mod
from chartcleaner.rule_preview import preview as preview_rule
from chartcleaner.summarizer import PRESET_LABELS
from chartcleaner.timeline import build as build_timeline
from chartcleaner.trends import REFERENCE_RANGES
from chartcleaner.trends import build as build_trends
from chartcleaner.devices import build as build_devices
from chartcleaner.micro import build as build_micro
from chartcleaner.overnight import build as build_overnight
from chartcleaner.problems import build as build_problems
from chartcleaner.daily_note import build as build_daily_note


# Stages whose deletions the Clean page lists under "Removed" for review.
REVIEWABLE_REMOVAL_STAGES = ("metadata_lines", "boilerplate", "learned_rules")
# Regex stages that honor stage_options.<sid>.exceptions ("Never remove this").
EXCEPTION_STAGES = ("metadata_lines", "boilerplate", "learned_rules", "literal_replacements")
# Regex stages whose rules live in a config list (stage id -> config key).
RULE_LISTS = {"metadata_lines": "emr_line_metadata", "boilerplate": "boilerplate",
              "phi_patterns": "epic_phi_patterns", "literal_replacements": "literal_replacements",
              "learned_rules": "learned_rules"}

# "Copy as…" formats: key -> (label, icon). The main button copies the "copy_default" pref.
COPY_FORMATS = {
    "text": ("Plain text", "content_copy"),
    "epic": ("Epic-safe text", "assignment"),
    "markdown": ("Markdown", "notes"),
    "stats": ("Text + stats", "data_object"),
    "trends": ("Text with trends on top", "vertical_align_top"),
    "ai": ("Tokenized, for an external AI", "smart_toy"),
}
AUTO_CLEAN_PAUSE = 1.5  # seconds of no typing before auto-clean runs


async def clean_page():
    state = {"running": False, "edited": 0.0}
    highlight = {"pending": False, "undo": None, "selection": None}
    refs: dict = {}          # result widgets (tabs, output box), repopulated by render_results
    inbox = {"items": []}    # rule-inbox suggestions for the current recent charts

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
                cache = service_mod.result_cache()
                if mode != "clean":
                    return Pipeline(cfg, mode=mode).run(text, track_changes=True, cache=cache), None
                found = detect_note_type(text, cfg)
                note_info.update(label=found.label, type=found.note_type, preset=None)
                preset = (common.PREFS.get("note_presets") or {}).get(found.note_type or "")
                if common.PREFS.get("note_auto_apply") and preset and preset in store.list_presets():
                    # This run only: applying a preset for real would overwrite config.json.
                    cfg = store.load_preset(preset)
                    note_info["preset"] = preset
                # Tracked changes stay in memory for the inspect tabs; history drops them.
                result = Pipeline(cfg, custom_dir=common.CUSTOM_DIR).run(
                    text, track_changes=True, cache=cache)
                return result, run_audit(result.text, cfg)

            def extras_work(cleaned: str):
                out = {}
                for key, fn in (("delta", extract_note_deltas), ("trends", build_trends),
                                ("timeline", build_timeline), ("problems", build_problems),
                                ("devices", build_devices), ("micro", build_micro),
                                ("overnight", build_overnight)):
                    try:
                        out[key] = fn(cleaned)
                    except Exception:
                        out[key] = None  # bonus views; never fail a clean over them
                return out

            result, audit = await run.io_bound(work)
            extras = await run.io_bound(extras_work, result.text) if mode == "clean" else {}
            CLEAN_STATE.update(input=text, result_text=result.text, result=result, audit=audit,
                               summary=None, qa=[], result_mode=mode, delta=extras.get("delta"),
                               note=note_info, trends=extras.get("trends"),
                               timeline=extras.get("timeline"), problems=extras.get("problems"),
                               devices=extras.get("devices"), micro=extras.get("micro"),
                               overnight=extras.get("overnight"))
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
            if mode == "clean":
                try:
                    recent_charts.remember(text, source, common.PREFS, tag=CLEAN_STATE.get("tag") or "")
                except Exception:
                    pass  # suggestions are a bonus; never break a run
            store.maybe_purge_old_data()
            try:
                if audit is not None:
                    store.append_audit_hits(f.signature for f in audit.findings)
            except Exception:
                pass  # history of findings must never break a run
            input_area.set_value(text)
            render_results()
            if mode == "clean":
                asyncio.get_running_loop().create_task(refresh_inbox())
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

    # ---- result pane ----------------------------------------------------------
    def change_groups(result) -> list[dict]:
        """Tracked changes grouped by rule — what the Review tab makes clickable."""
        groups: dict[tuple, dict] = {}
        for st in result.stages:
            for c in st.details.get("changes") or []:
                if st.id == "medical_abbreviations":
                    key = ("abbr", c["rule"], c["after"])
                    g = groups.setdefault(key, {"kind": "abbr", "sid": st.id, "stage": st.label,
                                                "rule": c["rule"], "term": c["before"],
                                                "after": c["after"], "count": 0,
                                                "source": c.get("source", "bundled")})
                    g["count"] += 1
                    continue
                if not c["before"].strip():
                    continue
                kind = "removal" if c["after"] == "" else "replace"
                key = (kind, st.id, c["rule"])
                g = groups.setdefault(key, {"kind": kind, "sid": st.id, "stage": st.label,
                                            "rule": c["rule"], "befores": [], "afters": [],
                                            "count": 0})
                g["count"] += 1
                if c["before"] not in g["befores"] and len(g["befores"]) < 30:
                    g["befores"].append(c["before"])
                    g["afters"].append(c["after"])
        return list(groups.values())

    def render_results() -> None:
        results_col.clear()
        refs.clear()
        result = CLEAN_STATE.get("result")
        render_ai()
        if not result:
            with results_col:
                with ui.column().classes("w-full items-center justify-center py-16 opacity-60 border "
                                         "border-dashed rounded-lg"):
                    ui.icon("auto_fix_high").classes("text-4xl")
                    ui.label("Your cleaned chart appears here.").classes("text-sm")
                    ui.label(f"Paste a chart on the left and press Clean ({MOD}+Enter).") \
                        .classes("text-xs")
            return
        result_mode = CLEAN_STATE.get("result_mode")
        single_pass = result_mode in ("abbreviations", "expand")
        with results_col:
            render_trust_strip(result, single_pass)
            phi = result.phi_counts()
            with ui.row().classes("w-full items-center gap-x-4 gap-y-1 flex-wrap text-sm"):
                ui.label(f"{result.chars_before:,} → {result.chars_after:,} chars")
                ui.label(f"{result.reduction:+.1f}%").classes(
                    "font-semibold " + ("text-green-600" if result.reduction >= 0 else "text-orange-600"))
                if result_mode == "abbreviations":
                    ui.label(f"{sum(s.matches for s in result.stages)} abbreviations applied")
                elif result_mode == "expand":
                    ui.label(f"{sum(s.matches for s in result.stages)} abbreviations expanded")
                else:
                    ui.label(f"{sum(phi.values())} PHI redacted").classes("text-red-600")
                ui.label(f"{result.duration_ms:.0f} ms").classes("opacity-60")
                render_note_chip(result_mode)
            if result_mode == "abbreviations":
                ui.label("Abbreviations only — other text and formatting preserved. "
                         "PHI has not been removed.").classes("text-xs opacity-70")
            elif result_mode == "expand":
                ui.label("Abbreviations expanded — other text and formatting preserved. "
                         "PHI has not been removed.").classes("text-xs opacity-70")

            groups = change_groups(result)
            refs["groups"] = groups
            with ui.tabs().props("dense align=left") as tabs:
                t_out = ui.tab("Output", icon="description")
                t_review = ui.tab("Review", icon="rule")
                t_insights = ui.tab("Insights", icon="insights") if not single_pass else None
            refs["tabs"] = (tabs, [t for t in (t_out, t_review, t_insights) if t is not None])
            with ui.tab_panels(tabs, value=t_out).classes("w-full").props("keep-alive"):
                with ui.tab_panel(t_out).classes("px-0"):
                    render_output(result, single_pass)
                with ui.tab_panel(t_review).classes("px-0"):
                    render_review(result, groups, single_pass)
                if t_insights is not None:
                    with ui.tab_panel(t_insights).classes("px-0"):
                        render_insights()
            if result.warnings:
                ui.label("⚠ " + " | ".join(result.warnings)).classes("text-xs text-orange-600")

    def render_trust_strip(result, single_pass: bool) -> None:
        """The one-glance "can I trust this?" badges above the output."""
        with ui.row().classes("w-full items-center gap-2 flex-wrap").mark("trust-strip"):
            report = result.fact_check
            if report is not None and not single_pass:
                status = report.status
                icon, color = {"ok": ("verified", "green"), "review": ("rule", "orange"),
                               "alert": ("report", "red")}[status]
                badge = ui.button(report.headline(), icon=icon,
                                  on_click=lambda: show_tab(1)) \
                    .props(f"unelevated no-caps color={color}").classes("text-sm")
                badge.mark("fact-check")
                badge.tooltip("Clinical values (numbers with units, labs, vitals, medications, "
                              "allergies, code status) counted before and after every stage. "
                              "Click to review.")
            audit = CLEAN_STATE.get("audit")
            if audit is not None and not audit.skipped and not single_pass:
                total = sum(audit.counts.values())
                if total:
                    ui.button(f"{total} possible PHI leftover(s)", icon="privacy_tip",
                              on_click=lambda: show_tab(1)) \
                        .props("outline no-caps color=orange").classes("text-sm")
                else:
                    ui.chip("No leftover PHI patterns", icon="shield", color="green-1",
                            text_color="green-9").props("dense")

    def render_note_chip(result_mode: str | None) -> None:
        note = CLEAN_STATE.get("note") or {}
        if result_mode != "clean" or not note.get("label"):
            return
        text = f"Detected: {note['label']}"
        if note.get("preset"):
            text += f" → preset {note['preset']}"
        ui.chip(text, icon="label", color="teal-1", text_color="teal-9",
                on_click=open_note_type_dialog).props("dense clickable").mark("note-chip") \
            .tooltip("Click to choose which preset this note type uses")

    def open_note_type_dialog() -> None:
        note = CLEAN_STATE.get("note") or {}
        kind = note.get("type")
        if not kind:
            ui.notify("The note type wasn't recognized, so there's nothing to map.", type="info")
            return
        presets = store.list_presets()
        mapping = dict(common.PREFS.get("note_presets") or {})
        with ui.dialog() as dlg, ui.card().classes("w-[440px] gap-2"):
            ui.label(f"{note['label']} notes").classes("text-lg font-semibold")
            ui.label("Pick the rule preset to clean this kind of note with.").classes("text-sm opacity-70")
            sel = ui.select({"": "(current rules)", **{p: p for p in presets}},
                            value=mapping.get(kind, ""), label="Preset").classes("w-full")
            auto = ui.checkbox("Apply note-type presets automatically",
                               value=bool(common.PREFS.get("note_auto_apply")))
            if not presets:
                ui.label("No presets yet — save one on the Pipeline page.").classes("text-xs text-orange-600")

            async def save() -> None:
                if sel.value:
                    mapping[kind] = sel.value
                else:
                    mapping.pop(kind, None)
                common.PREFS["note_presets"] = mapping
                common.PREFS["note_auto_apply"] = bool(auto.value)
                save_prefs()
                dlg.close()
                await run_clean()

            with ui.row():
                ui.button("Save & clean again", on_click=save).props("unelevated")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def show_tab(index: int) -> None:
        tabs = refs.get("tabs")
        if tabs and index < len(tabs[1]):
            tabs[0].set_value(tabs[1][index])

    # ---- Output tab -----------------------------------------------------------
    def copy_text(fmt: str) -> tuple[str, str] | None:
        """(text, notice) for a "Copy as…" format; None when unavailable."""
        text = CLEAN_STATE.get("result_text") or ""
        result = CLEAN_STATE.get("result")
        if fmt == "epic":
            return to_smartphrase(text), "Copied as plain text that pastes cleanly into Epic"
        if fmt == "markdown":
            return chart_markdown(), "Copied as Markdown"
        if fmt == "stats":
            return text + "\n\n<!-- " + (result.summary() if result else "") + " -->", "Copied with stats"
        if fmt == "trends":
            trends = CLEAN_STATE.get("trends")
            block = trends.to_text() if trends is not None and not trends.empty else ""
            return (f"{block}\n\n{text}" if block else text), (
                "Copied with trends on top" if block else "No trends in this chart — copied the text")
        if fmt == "ai":
            if not any(st.details.get("token_map") for st in (result.stages if result else [])):
                ui.notify("Turn on Reversible tokenization (Pipeline page) so names become [[T1]]-style "
                          "tokens you can restore after the AI replies.", type="warning", multi_line=True)
                return None
            return text, "Tokenized chart copied — use “Restore names in an AI reply” afterwards"
        return text, "Copied to clipboard"

    def copy_as(fmt: str | None = None) -> None:
        if not CLEAN_STATE.get("result_text"):
            ui.notify("Clean a chart first.", type="info")
            return
        fmt = fmt or common.PREFS.get("copy_default") or "text"
        got = copy_text(fmt if fmt in COPY_FORMATS else "text")
        if got:
            copy_to_clipboard(*got)

    def toggle_edit() -> None:
        out = refs.get("output")
        if out is None:
            return
        refs["editing"] = not refs.get("editing")
        if refs["editing"]:
            out.props(remove="readonly")
            ui.notify("Editing the result — copies use your edits.", type="info")
        else:
            commit_edit()
            out.props(add="readonly")

    def commit_edit() -> None:
        """Keep an edited result for copying and learn which lines were deleted."""
        out = refs.get("output")
        if out is None or not refs.get("editing"):
            return
        value = out.value or ""
        previous = CLEAN_STATE.get("result_text") or ""
        if value == previous:
            return
        CLEAN_STATE["result_text"] = value
        try:
            if edit_log.record(previous, value):
                asyncio.get_running_loop().create_task(refresh_inbox())
        except Exception:
            pass  # learning from edits is a bonus; never break editing

    def open_copy_default_dialog() -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[380px] gap-2"):
            ui.label("The Copy button copies…").classes("text-lg font-semibold")
            radio = ui.radio({k: v[0] for k, v in COPY_FORMATS.items()},
                             value=common.PREFS.get("copy_default") or "text")

            def save() -> None:
                common.PREFS["copy_default"] = radio.value
                save_prefs()
                dlg.close()
                render_results()

            with ui.row():
                ui.button("Save", on_click=save).props("unelevated")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def render_output(result, single_pass: bool) -> None:
        timeline = CLEAN_STATE.get("timeline") or []
        if timeline and not single_pass:
            with ui.row().classes("w-full items-center gap-1 flex-wrap").mark("timeline"):
                ui.icon("timeline").classes("opacity-60")
                for n in timeline:
                    label = n.date or f"Note {n.index}"
                    if n.day:
                        label += f" · {n.day}"
                    ui.chip(label, on_click=lambda n=n: jump_to(n.start, n.end)) \
                        .props("dense outline clickable").tooltip(n.title)
        shown_text = CLEAN_STATE.get("result_text") or result.text
        out = ui.textarea("", value=shown_text)
        out.props("outlined readonly input-style='min-height: 380px'").classes("w-full cc-mono cc-out")
        out.mark("result-output")
        out.on("blur", lambda _: commit_edit())
        refs["output"] = out
        refs["editing"] = False
        default = common.PREFS.get("copy_default") or "text"
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            with ui.dropdown_button(f"Copy · {COPY_FORMATS.get(default, COPY_FORMATS['text'])[0]}",
                                    icon="content_copy", split=True, auto_close=True,
                                    on_click=lambda: copy_as()) \
                    .props("unelevated color=primary no-caps").mark("copy-as"):
                for key, (label, icon) in COPY_FORMATS.items():
                    with ui.item(on_click=lambda k=key: copy_as(k)).mark(f"copy-{key}"):
                        with ui.item_section().props("avatar"):
                            ui.icon(icon)
                        with ui.item_section():
                            ui.item_label(label)
                if not single_pass:
                    ui.separator()
                    for tmpl in prompt_templates(load_config(common.CONFIG_PATH)):
                        with ui.item(on_click=lambda n=tmpl["name"]: copy_prompt(n)):
                            with ui.item_section().props("avatar"):
                                ui.icon("smart_toy")
                            with ui.item_section():
                                ui.item_label(f"Prompt: {tmpl['name']}")
                    ui.separator()
                    for i, tmpl in enumerate(note_templates_mod.templates(load_config(common.CONFIG_PATH))):
                        with ui.item(on_click=lambda n=tmpl["name"]: copy_note(n)).mark(f"copy-note-{i}"):
                            with ui.item_section().props("avatar"):
                                ui.icon("article")
                            with ui.item_section():
                                ui.item_label(f"Note: {tmpl['name']}")
                ui.separator()
                ui.item("Change what the main button copies…", on_click=open_copy_default_dialog)
            with ui.dropdown_button("Save", icon="download", auto_close=True).props("flat no-caps"):
                ui.item("Text (.txt)", on_click=download_result)
                ui.item("Word (.docx)", on_click=lambda: ui.download.content(
                    to_docx(CLEAN_STATE["result_text"]), "cleaned_chart.docx"))
                ui.item("Markdown (.md)", on_click=lambda: ui.download.content(
                    chart_markdown().encode("utf-8"), "cleaned_chart.md"))
                if common.PREFS.get("notes_folder"):
                    ui.item("To my notes folder", on_click=save_to_notes_folder)
            ui.button(icon="edit", on_click=toggle_edit).props("flat round").mark("edit-result") \
                .tooltip("Edit the result before copying — copies use your edits, and lines you "
                         "keep deleting become rule suggestions")
            with ui.button(icon="more_horiz").props("flat round").tooltip("More"):
                with ui.menu():
                    ui.menu_item("Restore names in an AI reply…", on_click=open_restore_dialog) \
                        .mark("restore-reply")
                    ui.menu_item("Mark as known good (re-check after rule changes)…",
                                 on_click=open_known_good_dialog).mark("known-good")
                    ui.menu_item("Daily note — compare with a previous chart…",
                                 on_click=open_daily_note_dialog).mark("daily-note")
                    ui.menu_item("Clean again", on_click=run_clean)
        if CLEAN_STATE.get("result_mode") == "expand":
            ambiguous = dict(result.stages[0].details.get("ambiguous") or {})
            if ambiguous:
                with ui.row().classes("items-center gap-2"):
                    ui.label("Not expanded because they have several meanings: "
                             + ", ".join(f"{a} ×{n}" for a, n in sorted(ambiguous.items()))) \
                        .classes("text-sm")
                    ui.button("Choose meanings", icon="rule",
                              on_click=lambda amb=ambiguous: open_meanings_dialog(amb)).props("flat dense")

    def jump_to(start: int, end: int) -> None:
        ui.run_javascript(
            "(function(){const ta=document.querySelector('.cc-out textarea'); if(!ta) return;"
            f"const s={int(start)}, e={int(end)}; ta.focus(); ta.setSelectionRange(s, s);"
            "const lh=parseFloat(getComputedStyle(ta).lineHeight)||16;"
            "ta.scrollTop=(ta.value.slice(0,s).split('\\n').length-1)*lh;"
            "ta.setSelectionRange(s, Math.min(e, s+1));})()")

    def open_restore_dialog() -> None:
        result = CLEAN_STATE.get("result")
        mapping = None
        for st in (result.stages if result else []):
            if st.details.get("token_map"):
                mapping = dict(st.details["token_map"])
        with ui.dialog() as dlg, ui.card().classes("w-[680px] gap-2"):
            ui.label("Restore names in an AI reply").classes("text-lg font-semibold")
            ui.label("Paste what the AI wrote about the tokenized chart. Every [[T1]]-style token is "
                     "replaced with the real value — " + (
                         "using this chart's token map." if mapping else
                         "using the newest saved token map.")).classes("text-sm opacity-70")
            reply = ui.textarea("AI reply").props("outlined autofocus input-style='min-height: 140px'") \
                .classes("w-full cc-mono")
            restored = ui.textarea("With real values").props(
                "outlined readonly input-style='min-height: 140px'").classes("w-full cc-mono")
            note = ui.label("").classes("text-xs opacity-70")

            def restore() -> None:
                out = service_mod.restore(reply.value or "", mapping)
                restored.set_value(out["text"])
                note.set_text(out.get("error") or f"{out['restored']} token(s) restored.")

            with ui.row():
                ui.button("Restore", icon="settings_backup_restore", on_click=restore) \
                    .props("unelevated").mark("restore-run")
                ui.button("Copy", icon="content_copy",
                          on_click=lambda: copy_to_clipboard(restored.value or "")).props("flat")
                ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()

    def open_known_good_dialog() -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[460px] gap-2"):
            ui.label("Mark as known good").classes("text-lg font-semibold")
            ui.label("Keeps this chart and this exact output (encrypted). Settings → Known-good "
                     "charts re-cleans it after you change rules and shows anything that "
                     "came out differently.").classes("text-sm opacity-70")
            label = ui.textarea("Label", value=(CLEAN_STATE.get("note") or {}).get("label", "")) \
                .props("outlined autogrow").classes("w-full")

            def save() -> None:
                try:
                    regression_set.add(CLEAN_STATE.get("input") or "", CLEAN_STATE.get("result_text") or "",
                                       label.value or "", mode=CLEAN_STATE.get("result_mode") or "clean",
                                       preset=common.PREFS.get("last_preset") or "")
                except ValueError as ex:
                    ui.notify(str(ex), type="warning")
                    return
                dlg.close()
                ui.notify("Saved as a known-good chart.", type="positive")

            with ui.row():
                ui.button("Save", icon="verified", on_click=save).props("unelevated").mark("known-good-save")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    # ---- Review tab -----------------------------------------------------------
    def render_review(result, groups: list[dict], single_pass: bool) -> None:
        if result.fact_check is not None and not single_pass:
            render_fact_check(result.fact_check)
        audit = CLEAN_STATE.get("audit")
        flagged: set[int] = set()
        if audit is not None and not audit.skipped:
            flagged = {f.line for f in audit.findings}
            render_audit(audit)
        ui.label("Click struck-through or highlighted text to see which rule changed it — and "
                 "keep it, change it or switch the rule off.").classes("text-xs opacity-70")
        with ui.scroll_area().classes("w-full border rounded h-[460px] cc-panel"):
            diff = ui.html(review_diff_html(CLEAN_STATE["input"], result.text, flagged, groups))
            diff.on("click", lambda e: inspect_change(e.args), js_handler=REVIEW_CLICK_JS)
            diff.mark("review-diff")
        removed = removed_groups(result)
        if removed:
            with ui.expansion(f"Removed ({sum(len(g['items']) for g in removed)})", icon="delete_sweep") \
                    .classes("w-full"):
                render_removed(removed)
        abbr_changes = next((st.details.get("changes") or [] for st in result.stages
                             if st.id == "medical_abbreviations"), [])
        if abbr_changes:
            with ui.expansion(f"Abbreviations ({len(abbr_changes)})", icon="short_text").classes("w-full"):
                render_abbreviation_changes(abbr_changes)
        with ui.expansion("What each stage did", icon="account_tree").classes("w-full"):
            cols = [
                {"name": "stage", "label": "Stage", "field": "stage", "align": "left"},
                {"name": "matches", "label": "Matches", "field": "matches"},
                {"name": "delta", "label": "Δ chars", "field": "delta"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
            ]
            rows = []
            for s in result.stages:
                delta = s.chars_before - s.chars_after
                status = "skipped" if s.skipped else ("error: " + s.error) if s.error else "ok"
                rows.append({"stage": s.label, "matches": s.matches,
                             "delta": f"{delta:+,}", "status": status})
            ui.table(columns=cols, rows=rows, row_key="stage").classes("w-full").props("flat dense")

    def render_audit(audit) -> None:
        total = sum(audit.counts.values())
        if not (total or audit.errors):
            return
        with ui.expansion(f"Possible PHI left — {total} finding(s) to double-check before sharing",
                          icon="privacy_tip").classes("w-full"):
            if audit.errors:
                ui.label("Some checks could not run: " + " | ".join(audit.errors)) \
                    .classes("text-xs text-orange-600")
            by_check: dict[str, list] = {}
            for f in audit.findings:
                by_check.setdefault(f.check, []).append(f)
            for cid, items in by_check.items():
                ui.label(f"{CHECK_LABELS.get(cid, cid)} — {audit.counts.get(cid, 0)}"
                         f" · {CHECK_DESCRIPTIONS.get(cid, '')}").classes("text-sm font-semibold mt-1")
                for f in items[:10]:
                    with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
                        ui.badge(f"line {f.line}").props("outline color=orange")
                        ui.label(f.excerpt).classes("text-xs cc-mono flex-grow")
                        ui.button("Build rule", icon="rule",
                                  on_click=lambda ff=f: start_pending_rule(
                                      ff.suggested_regex, ff.suggested_replacement,
                                      ff.suggested_stage)).props("flat dense")
                if len(items) > 10:
                    ui.label(f"… {len(items) - 10} more of this type").classes("text-xs opacity-60")
            ui.label("Findings are hints, not verdicts — confirm before acting on them.") \
                .classes("text-xs opacity-60 mt-1")

    def inspect_change(key) -> None:
        groups = refs.get("groups") or []
        try:
            g = groups[int(str(key).lstrip("g"))]
        except (ValueError, IndexError):
            return
        if g["kind"] == "abbr":
            with ui.dialog() as dlg, ui.card().classes("w-[460px] gap-2"):
                ui.label("Abbreviation").classes("text-lg font-semibold")
                with ui.row().classes("items-center gap-2"):
                    ui.label(g["term"]).classes("font-medium")
                    ui.label("→")
                    ui.label(g["after"]).classes("font-medium")
                    ui.badge(f"×{g['count']}").props("outline")
                ui.label("From your dictionary" if g["source"] != "custom" else "One of your own rules") \
                    .classes("text-xs opacity-70")
                with ui.row():
                    ui.button("Change", icon="edit",
                              on_click=lambda: (dlg.close(), change_abbreviation(g))).props("flat")
                    ui.button("Don't abbreviate this", icon="block",
                              on_click=lambda: (dlg.close(), asyncio.get_running_loop().create_task(
                                  disable_abbreviation(g)))).props("flat color=negative")
                    ui.button("Close", on_click=dlg.close).props("flat")
            dlg.open()
            return
        verb = "Removed" if g["kind"] == "removal" else "Changed"
        with ui.dialog() as dlg, ui.card().classes("w-[620px] gap-2").mark("change-dialog"):
            ui.label(f"{verb} by {g['stage']}").classes("text-lg font-semibold")
            ui.label(f"Rule: {g['rule'][:300]}").classes("text-xs cc-mono opacity-70 break-all")
            ui.label(f"{g['count']} change(s) in this chart").classes("text-xs opacity-70")
            for before, after in list(zip(g["befores"], g["afters"]))[:6]:
                with ui.row().classes("w-full items-start gap-2 no-wrap"):
                    ui.label(before.strip()[:240]).classes("text-xs cc-mono cc-gone flex-grow break-all")
                    if after:
                        ui.label(f"→ {after[:80]}").classes("text-xs cc-mono")
            with ui.row().classes("gap-1 flex-wrap"):
                if g["sid"] in EXCEPTION_STAGES:
                    ui.button("Never remove this", icon="shield",
                              on_click=lambda: (dlg.close(), asyncio.get_running_loop().create_task(
                                  keep_text(g["sid"], g["befores"][0])))).props("flat")
                if g["sid"] in RULE_LISTS:
                    ui.button("Delete this rule", icon="delete",
                              on_click=lambda: confirm_dialog(
                                  "Delete this rule? A backup of your rules is kept.",
                                  lambda: (dlg.close(), asyncio.get_running_loop().create_task(
                                      delete_rule(g))))).props("flat color=negative")
                ui.button("Edit on Pipeline page", icon="tune",
                          on_click=lambda: ui.navigate.to("/pipeline")).props("flat")
                ui.button("Copy", icon="content_copy",
                          on_click=lambda: copy_to_clipboard(g["befores"][0])).props("flat")
                ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()

    async def delete_rule(g: dict) -> None:
        key = RULE_LISTS[g["sid"]]
        cfg = load_config(common.CONFIG_PATH)
        entries = cfg.get(key) or []
        patterns = [e[0] if isinstance(e, list) else e for e in entries]
        if g["rule"] not in patterns:
            ui.notify("That rule changed since this clean; nothing deleted.", type="warning")
            return
        entries.pop(patterns.index(g["rule"]))
        save_config_with_backup(cfg)
        ui.notify("Rule deleted (a backup of the previous rules was kept).", type="positive")
        await run_clean()

    # ---- Insights tab ---------------------------------------------------------
    def render_insights() -> None:
        trends = CLEAN_STATE.get("trends")
        delta = CLEAN_STATE.get("delta")
        timeline = CLEAN_STATE.get("timeline") or []
        shown = False
        if timeline:
            shown = True
            ui.label(f"{len(timeline)} notes in this chart").classes("text-sm font-semibold")
            for n in timeline:
                with ui.row().classes("w-full items-center gap-2 no-wrap cursor-pointer") \
                        .on("click", lambda n=n: (show_tab(0), jump_to(n.start, n.end))):
                    ui.badge(n.date or f"Note {n.index}").props("outline")
                    if n.day:
                        ui.badge(n.day, color="teal").props("outline")
                    ui.label(n.preview or n.title).classes("text-xs opacity-80 truncate flex-grow")
                    ui.label(f"{n.chars:,} chars").classes("text-xs opacity-50")
        overnight = CLEAN_STATE.get("overnight")
        if overnight is not None and not overnight.empty:
            shown = True
            render_block("Overnight events", "nightlight", overnight.to_text(), "insight-overnight",
                         lambda: [ui.label((e.when + "  " if e.when else "") + e.text)
                                  .classes("text-xs cc-mono") for e in overnight.events])
        devices = CLEAN_STATE.get("devices")
        if devices is not None and devices.devices:
            shown = True
            render_block("Lines, drains & airway", "cable", devices.to_text(), "insight-devices",
                         lambda: render_devices(devices))
        micro = CLEAN_STATE.get("micro")
        if micro is not None and not micro.empty:
            shown = True
            render_block("Antibiotics & cultures", "biotech", micro.to_text(), "insight-micro",
                         lambda: render_micro(micro))
        problems = CLEAN_STATE.get("problems")
        if problems is not None and not problems.empty:
            shown = True
            render_block(f"By problem ({len(problems.problems)})", "account_tree",
                         problems.to_text(), "insight-problems", lambda: render_problems(problems))
        if trends is not None and not trends.empty:
            shown = True
            render_trends(trends)
        if delta is not None and delta.notes_found > 1:
            shown = True
            with ui.expansion("Changes over time (copied-forward text removed)", icon="difference") \
                    .classes("w-full"):
                render_delta(delta)
        if not shown:
            ui.label("Problems, devices, antibiotics, overnight events, trends, a note timeline "
                     "and changes over time appear here when the chart has them.") \
                .classes("text-sm opacity-60")

    def render_block(title: str, icon: str, text: str, marker: str, body) -> None:
        with ui.expansion(title, icon=icon, value=True).classes("w-full").mark(marker):
            body()
            ui.button("Copy", icon="content_copy",
                      on_click=lambda t=text: copy_to_clipboard(t)).props("flat dense")

    def render_devices(report) -> None:
        ui.label(f"Day counts as of {report.reference.strftime('%m/%d/%Y')} (the chart's latest "
                 "date); insertion day = day 1.").classes("text-xs opacity-60")
        for d in report.devices:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                color = "grey" if d.removed else "orange" if d.needs_review else "primary"
                ui.badge(d.name + (f" · {d.site}" if d.site else ""), color=color).props("outline")
                status = ("removed" if d.removed else f"day {d.day}" if d.day is not None
                          else "in place (no date)")
                ui.label(status + (" — still needed?" if d.needs_review else "")) \
                    .classes("text-xs font-semibold w-40")
                ui.label(d.last_line).classes("text-xs cc-mono opacity-70 truncate flex-grow") \
                    .tooltip(d.last_line)

    def render_micro(report) -> None:
        for a in report.antibiotics:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.badge(a.name, color="grey" if a.stopped else "teal").props("outline")
                status = ("stopped" if a.stopped else f"day {a.day}" if a.day is not None else "")
                ui.label(status).classes("text-xs font-semibold w-20")
                ui.label(a.last_line).classes("text-xs cc-mono opacity-70 truncate flex-grow") \
                    .tooltip(a.last_line)
        if report.cultures:
            ui.label("Cultures").classes("text-xs font-semibold mt-1")
            for c in report.cultures:
                positive = (bool(c.organism) or "positive" in c.result.lower()
                            or "grew" in c.result.lower())
                ui.label(c.to_text()).classes(
                    "text-xs cc-mono " + ("text-red-600 font-semibold" if positive else ""))

    def render_problems(report) -> None:
        ui.label("Each problem from the latest Assessment & Plan with the chart lines that relate "
                 "to it — copied verbatim, never reworded.").classes("text-xs opacity-60")
        for p in report.problems:
            with ui.expansion(p.heading[:120] + (f"  ({p.note})" if p.note else ""),
                              caption=f"{len(p.evidence)} related line(s)").classes("w-full"):
                for line in p.plan:
                    ui.label(line).classes("text-xs cc-mono font-semibold")
                for e in p.evidence[:15]:
                    with ui.row().classes("w-full items-start gap-2 no-wrap cursor-pointer") \
                            .on("click", lambda e=e: (show_tab(0), jump_to(e.offset, e.offset + len(e.line)))):
                        if e.note:
                            ui.badge(e.note).props("outline")
                        ui.label(e.line).classes("text-xs cc-mono break-all")
                ui.button("Copy problem", icon="content_copy",
                          on_click=lambda p=p: copy_to_clipboard(p.to_text())).props("flat dense")

    def render_trends(trends) -> None:
        ui.label(f"{'Lab trends' if trends.labs else 'Trends'} across {len(trends.notes)} notes") \
            .classes("text-sm font-semibold")
        ui.label("Shaded band = typical adult reference range; red points are outside it. "
                 "Your lab's ranges may differ.").classes("text-xs opacity-60")
        if trends.labs:
            with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 gap-2 w-full") \
                    .mark("trends-table"):
                for t in trends.labs[:16]:
                    flags = t.flags()
                    with ui.card().classes("p-2 gap-0"):
                        with ui.row().classes("w-full items-center justify-between no-wrap"):
                            ui.label(t.name).classes("font-semibold")
                            ui.label(" → ".join(
                                (v or "—") + (f" ({f})" if f else "") for v, f in zip(t.values, flags))
                                + (f"  {t.direction()}" if t.direction() else "")) \
                                .classes("text-xs cc-mono")
                        ui.echart(sparkline_options(t, trends.notes)).classes("w-full h-16")
            for t in trends.labs[16:]:
                ui.label(t.to_text()).classes("text-xs cc-mono")
        if trends.scores:
            ui.label("Scores").classes("text-sm font-semibold mt-2")
            with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 gap-2 w-full") \
                    .mark("trends-scores"):
                for t in trends.scores:
                    with ui.card().classes("p-2 gap-0"):
                        with ui.row().classes("w-full items-center justify-between no-wrap"):
                            ui.label(t.name).classes("font-semibold")
                            ui.label(" → ".join(v or "—" for v in t.values)
                                     + (f"  {t.direction()}" if t.direction() else "")) \
                                .classes("text-xs cc-mono")
                        if any(n is not None for n in t.numbers()):
                            ui.echart(sparkline_options(t, trends.notes)).classes("w-full h-16")
        rows = trends.med_rows()
        if rows:
            ui.label("Medication changes").classes("text-sm font-semibold mt-2")
            cols = [{"name": "when", "label": "Between", "field": "when", "align": "left"},
                    {"name": "change", "label": "Change", "field": "change", "align": "left"},
                    {"name": "before", "label": "Before", "field": "before", "align": "left"},
                    {"name": "after", "label": "After", "field": "after", "align": "left"}]
            ui.table(columns=cols, rows=[dict(r, id=i) for i, r in enumerate(rows)], row_key="id") \
                .classes("w-full").props("flat dense wrap-cells")
        block = trends.to_text()
        with ui.row().classes("gap-2"):
            ui.button("Copy trends", icon="content_copy",
                      on_click=lambda: copy_to_clipboard(block)).props("flat")
            ui.button("Copy result with trends on top", icon="vertical_align_top",
                      on_click=lambda: copy_as("trends")).props("flat")

    def sparkline_options(t, notes: list[str]) -> dict:
        nums = t.numbers()
        flags = t.flags()
        data = [None if n is None else {"value": n, "itemStyle": {
            "color": "#dc2626" if f else "#2563eb"}} for n, f in zip(nums, flags)]
        series = {"type": "line", "data": data, "connectNulls": True, "symbolSize": 7,
                  "lineStyle": {"width": 2, "color": "#64748b"}}
        rng = REFERENCE_RANGES.get(t.name)
        if rng:
            series["markArea"] = {"silent": True, "itemStyle": {"color": "rgba(34,197,94,0.12)"},
                                  "data": [[{"yAxis": rng[0]}, {"yAxis": rng[1]}]]}
        return {"backgroundColor": "transparent", "animation": False,
                "grid": {"left": 4, "right": 4, "top": 6, "bottom": 4},
                "tooltip": {"trigger": "axis"},
                "xAxis": {"type": "category", "data": list(notes), "show": False},
                "yAxis": {"type": "value", "scale": True, "show": False},
                "series": [series]}

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

    def download_result() -> None:
        name = store.save_export(CLEAN_STATE["result_text"], "cleaned_chart")
        download_file(f"/exports/{name}", name)

    # ---- AI drawer: local summary + ask this chart ------------------------------
    summary_state = {"running": False}
    summary_refs: dict = {}  # panel widgets, repopulated by render_ai
    qa_state = {"running": False}
    qa_refs: dict = {}

    def render_ai() -> None:
        ai_col.clear()
        summary_refs.clear()
        qa_refs.clear()
        result = CLEAN_STATE.get("result")
        with ai_col:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Local AI").classes("text-lg font-semibold")
                ui.button(icon="close", on_click=lambda: toggle_ai(False)).props("flat round dense")
            if not result or CLEAN_STATE.get("result_mode") in ("abbreviations", "expand"):
                ui.label("Do a full clean, then summarize the chart or ask questions about it — "
                         "on this computer, through Ollama.").classes("text-sm opacity-70")
                return
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
                ui.label(f"No local LLM detected at {opts['base_url']} — install Ollama "
                         "(ollama.com) and pull a model, e.g. `ollama pull llama3.1`.") \
                    .classes("text-xs text-orange-600")
            ui.select(model_options, value=model_val or None, label="Model", new_value_mode="add",
                      on_change=lambda e: save_llm_pref("model", e.value)).classes("w-full")

            ui.label("Summary").classes("text-sm font-semibold mt-2")
            preset_key = str(opts["prompt_preset"])
            if preset_key not in PRESET_LABELS:
                preset_key = "clinical"
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.select(dict(PRESET_LABELS), value=preset_key, label="Write",
                          on_change=lambda e: (save_llm_pref("prompt_preset", e.value),
                                               custom_box.set_visibility(e.value == "custom"))
                          ).classes("flex-grow")
                summary_refs["button"] = ui.button("Summarize", icon="psychology",
                                                   on_click=run_summarize).props("unelevated color=primary")
                summary_refs["spinner"] = ui.spinner("dots", size="sm")
                summary_refs["spinner"].set_visibility(False)
            custom_box = ui.textarea("Custom prompt (replaces the preset)",
                                     value=str(opts["custom_prompt"]),
                                     on_change=lambda e: save_llm_pref("custom_prompt", e.value)
                                     ).props("outlined").classes("w-full cc-mono")
            custom_box.set_visibility(preset_key == "custom")
            summary_refs["output"] = ui.textarea("").props(
                "outlined readonly autogrow input-style='min-height: 120px'").classes("w-full cc-mono")
            summary_refs["marked"] = ui.html("").classes("w-full").mark("ai-summary-marked")
            summary_refs["marked"].set_visibility(False)
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                summary_refs["grounding_row"] = ui.row().classes("items-center gap-1 flex-wrap")
                summary_refs["meta"] = ui.label("").classes("text-xs opacity-60")
            ui.button("Copy summary", icon="content_copy",
                      on_click=lambda: copy_to_clipboard(summary_refs["output"].value or "")) \
                .props("flat dense")
            render_summary_output()

            ui.separator().classes("my-2")
            ui.label("Ask this chart").classes("text-sm font-semibold")
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                qa_refs["question"] = ui.input(
                    "Ask about this chart",
                    placeholder="e.g. What are the active antibiotics?").classes("flex-grow")
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

    def toggle_ai(open_: bool | None = None) -> None:
        value = (not ai_drawer.value) if open_ is None else bool(open_)
        ai_drawer.set_value(value)
        common.PREFS["ai_drawer"] = value
        save_prefs()
        sync_layout()

    def sync_layout() -> None:
        """Side by side on wide windows; with the AI drawer open, only on very wide ones."""
        wide = "2xl:grid-cols-2" if ai_drawer.value else "xl:grid-cols-2"
        layout.classes(remove="xl:grid-cols-2 2xl:grid-cols-2", add=wide)

    def save_llm_pref(key: str, value) -> None:
        try:
            cfg = load_config(common.CONFIG_PATH)
            cfg["local_llm"] = {**merge_llm_config(cfg), key: value}
            save_config_with_backup(cfg)
        except Exception:
            pass  # preference saving must never break the panel

    def grounding_badge(score: float, safe: bool, total: int) -> None:
        if not total:
            ui.badge("No checkable facts (no numbers/dates)", color="grey").props("outline")
        elif not safe:
            ui.badge(f"UNGROUNDED — only {score}% of numbers/dates found in chart", color="red")
        elif score >= 100.0:
            ui.badge("Grounded 100%", color="green")
        else:
            ui.badge(f"Grounded {score}%", color="amber")

    def render_summary_output() -> None:
        res = CLEAN_STATE.get("summary")
        if not res or "output" not in summary_refs:
            return
        summary_refs["output"].set_value(res.text)
        summary_refs["meta"].set_text(f"model: {res.model} · {res.duration_ms:,} ms")
        g = res.grounding
        facts = getattr(res, "facts", None)
        marked = summary_refs.get("marked")
        if marked is not None:
            show = bool(facts and facts.unsupported)
            marked.set_content(highlight_unsupported_html(res.text, facts) if show else "")
            marked.set_visibility(show)
        row = summary_refs["grounding_row"]
        row.clear()
        with row:
            grounding_badge(g.grounding_score, g.is_safe, g.total_entities)
            facts_badge(facts)
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
            ui.notify(f"{e} — install Ollama from ollama.com, pull a model "
                      "(e.g. `ollama pull llama3.1`), then retry.", type="warning", multi_line=True)
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

    def render_qa_log() -> None:
        if "log" not in qa_refs:
            return
        log_col = qa_refs["log"]
        log_col.clear()
        turns = CLEAN_STATE.get("qa") or []
        with log_col:
            for t in turns:
                ui.label("Q: " + t["q"]).classes("text-sm font-semibold")
                facts = t.get("facts")
                if facts is not None and facts.unsupported:
                    ui.html(highlight_unsupported_html(t["a"], facts)).classes("w-full")
                else:
                    ui.markdown(t["a"]).classes("w-full")
                g = t["g"]
                with ui.row().classes("items-center gap-1 flex-wrap"):
                    grounding_badge(g["score"], g["safe"], g["total"])
                    facts_badge(facts)
                    ui.button("Copy answer", icon="content_copy",
                              on_click=lambda _, a=t["a"]: copy_to_clipboard(a)).props("flat dense")
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
                return ask_chart(question, chart_text, load_config(common.CONFIG_PATH), history=history)

            res = await run.io_bound(work)
            if (CLEAN_STATE.get("result") is not chart_result
                    or CLEAN_STATE.get("result_text") != chart_text):
                return
            CLEAN_STATE.setdefault("qa", []).append({
                "q": res.question, "a": res.answer,
                "g": {"score": res.grounding.grounding_score, "safe": res.grounding.is_safe,
                      "total": res.grounding.total_entities},
                "facts": getattr(res, "facts", None),
                "model": res.model, "ms": res.duration_ms})
            if q_box:
                q_box.set_value("")
            render_qa_log()
        except LlmUnavailableError as e:
            ui.notify(f"{e} — install Ollama from ollama.com, pull a model "
                      "(e.g. `ollama pull llama3.1`), then retry.", type="warning", multi_line=True)
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

    # ---- input helpers --------------------------------------------------------
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
        hide_selection_bar()
        render_results()

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
        if name:
            apply_preset(name)

    def on_input_change(e) -> None:
        CLEAN_STATE.update(input=e.value)
        state["edited"] = time.monotonic()

    async def auto_tick() -> None:
        if (not common.PREFS.get("auto_clean") or state["running"] or highlight["pending"]):
            return
        if time.monotonic() - state["edited"] < AUTO_CLEAN_PAUSE:
            return  # still typing
        text = input_area.value or ""
        if text.strip() and text != AUTO_LAST["text"]:
            AUTO_LAST["text"] = text
            await run_clean()
        elif not text.strip():
            AUTO_LAST["text"] = None

    # ---- teach from a selection (the selection toolbar) -----------------------
    def invalidate_output() -> None:
        CLEAN_STATE.update(input=input_area.value, result=None, result_text="", audit=None,
                           summary=None, qa=[], result_mode=None)
        AUTO_LAST["text"] = None
        render_results()

    def hide_selection_bar() -> None:
        highlight["selection"] = None
        sel_bar.set_visibility(False)

    def on_highlight(e) -> None:
        if highlight["pending"] or state["running"]:
            return
        selection = e.args
        if not isinstance(selection, dict) or not str(selection.get("text", "")).strip():
            return
        highlight["selection"] = selection
        shown = " ".join(selection["text"].split())
        sel_text.set_text(f"“{shown[:70]}{'…' if len(shown) > 70 else ''}”")
        sel_bar.set_visibility(True)

    def current_selection() -> dict | None:
        selection = highlight.get("selection")
        if not selection:
            ui.notify("Select some text in the chart first.", type="info")
            return None
        try:
            replace_selection(input_area.value or "", selection, "")
        except ValueError as ex:
            ui.notify(str(ex), type="warning")
            hide_selection_bar()
            return None
        return selection

    def sel_remove() -> None:
        selection = current_selection()
        if selection and apply_highlight(selection, ""):
            hide_selection_bar()

    def sel_replace() -> None:
        selection = current_selection()
        if selection:
            open_replace_dialog(selection)

    def sel_abbreviate() -> None:
        selection = current_selection()
        if selection:
            open_abbreviate_dialog(selection)

    def sel_more() -> None:
        selection = current_selection()
        if selection:
            open_learn_dialog(selection["text"])

    async def sel_keep() -> None:
        selection = current_selection()
        if not selection:
            return
        cfg = load_config(common.CONFIG_PATH)
        snippet = selection["text"].strip()[:200]
        for sid in EXCEPTION_STAGES:
            keep = cfg.setdefault("stage_options", {}).setdefault(sid, {}).setdefault("exceptions", [])
            if snippet not in keep:
                keep.append(snippet)
        save_config_with_backup(cfg)
        hide_selection_bar()
        highlight_status.set_text("Saved — rules will never remove text containing this selection.")

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
            if added:
                asyncio.get_running_loop().create_task(report_impact("learned_rules", pair, before))
            return True
        except Exception as ex:
            ui.notify(str(ex), type="negative")
            return False

    async def report_impact(sid: str, entry, taught_on: str) -> None:
        """After a rule is saved: say what it does to the other recent charts."""
        def work():
            return preview_rule(load_config(common.CONFIG_PATH), sid, entry,
                                recent_charts.texts(20), skip_text=taught_on,
                                examples=rule_examples.load())
        try:
            impact = await run.io_bound(work)
        except Exception:
            return
        if impact.charts_checked:
            msg = impact.headline
            if impact.example_failures:
                msg += f" It changes {len(impact.example_failures)} earlier lesson(s) — check My text rules."
            try:
                highlight_status.set_text(highlight_status.text + " " + msg)
            except Exception:
                pass

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
            hide_selection_bar()
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
                hide_selection_bar()
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

    def open_replace_dialog(selection: dict) -> None:
        highlight["pending"] = True
        with ui.dialog() as dlg, ui.card().classes("w-full max-w-xl gap-3"):
            ui.label("Replace highlighted text").classes("text-lg font-semibold")
            ui.label(selection["text"]).classes("whitespace-pre-wrap break-all max-h-40 overflow-auto")
            replacement = ui.textarea("Replace with").props("outlined autofocus").classes("w-full")
            ui.label("Updates this selection now and saves a rule for future full cleans. "
                     "An empty replacement removes the selection.").classes("text-sm opacity-70")

            def save_replacement() -> None:
                if apply_highlight(selection, replacement.value or ""):
                    hide_selection_bar()
                    dlg.close()

            with ui.row():
                ui.button("Replace & remember", on_click=save_replacement).props("unelevated")
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
            repl = ui.textarea("Replace with (used by the last option only)",
                               value="").props("outlined dense autogrow").classes("w-full")
            hit_lbl = ui.label("").classes("text-xs opacity-80")
            impact_lbl = ui.label("").classes("text-xs opacity-80")
            impact_box = ui.column().classes("w-full gap-0")

            def sync_hits() -> None:
                m = mode.value or LEARN_REMOVE_TEXT
                pat = learned_pattern(txt.value, m)
                n = count_matches(pat, input_area.value or "", re.IGNORECASE) if pat else 0
                hit_lbl.set_text(
                    f"Preview: matches {n} time(s) in your current chart" if n >= 0
                    else "Preview: pattern problem — adjust the highlighted text")

            async def check_impact() -> None:
                m = mode.value or LEARN_REMOVE_TEXT
                pat = learned_pattern((txt.value or "").replace("\r\n", "\n").strip(), m)
                if not pat:
                    return
                pair = [pat, (repl.value or "").replace("\\", "\\\\") if m == LEARN_REPLACE else ""]
                impact_lbl.set_text("Checking your recent charts…")

                def work():
                    return preview_rule(load_config(common.CONFIG_PATH), "learned_rules", pair,
                                        recent_charts.texts(20), skip_text=input_area.value or "",
                                        examples=rule_examples.load())
                try:
                    impact = await run.io_bound(work)
                except Exception as ex:
                    impact_lbl.set_text(f"Could not check: {ex}")
                    return
                impact_lbl.set_text(impact.headline)
                impact_box.clear()
                with impact_box:
                    for smp in impact.samples[:5]:
                        ui.label(f"− {smp['before'][:160]}" + (f"   → {smp['after'][:80]}" if smp["after"] else "")) \
                            .classes("text-xs cc-mono opacity-80")
                    if impact.example_failures:
                        ui.label(f"⚠ Changes {len(impact.example_failures)} earlier lesson(s).") \
                            .classes("text-xs text-orange-600")

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
                hide_selection_bar()
                verb = "replace" if m == LEARN_REPLACE else "remove"
                ui.notify(f"Rule saved — matches will be {verb}d every time you Clean "
                          "(manage them on the Pipeline page → Learned rules).", type="positive")

            with ui.row().classes("gap-2"):
                ui.button("Save rule", icon="school", on_click=save_learned) \
                    .props("unelevated color=primary")
                ui.button("Check my recent charts", icon="travel_explore", on_click=check_impact) \
                    .props("flat")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

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
        """Details behind the trust badge: which clinical values went, and where."""
        if not (report.losses or report.meaning or report.introduced or report.implausible):
            return
        status = report.status
        icon, color = {"ok": ("verified", "green"), "review": ("rule", "orange"),
                       "alert": ("report", "red")}[status]
        titles = {"unexpected": "Lost by a stage that should only reformat",
                  "rule": "Removed by a removal rule",
                  "by_design": "Dropped by a section or summary setting"}
        with ui.expansion(report.headline(), icon=icon, value=status == "alert") \
                .classes(f"w-full text-{color}"):
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
            if report.meaning:
                ui.label("Lines whose meaning changed (a negation dropped, or a side switched)") \
                    .classes("text-sm font-semibold mt-1")
                for m in report.meaning[:20]:
                    with ui.column().classes("w-full gap-0 border-l-4 pl-2").mark("meaning-change"):
                        ui.label(m.message).classes("text-xs font-semibold text-red-600"
                                                    if m.category == "unexpected" else "text-xs font-semibold")
                        ui.label("before: " + m.before).classes("text-xs cc-mono break-all")
                        ui.label("after:  " + m.after).classes("text-xs cc-mono break-all")
            if report.introduced:
                ui.label("Values in the output that weren't in the chart") \
                    .classes("text-sm font-semibold mt-1")
                for x in report.introduced[:20]:
                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                        ui.badge(x.display).props(f"outline color={'red' if x.category == 'unexpected' else 'grey'}")
                        ui.label(f"{x.stage_label}: {x.lines[0][:240] if x.lines else ''}") \
                            .classes("text-xs cc-mono flex-grow break-all")
            if report.implausible:
                ui.label("Values that look impossible").classes("text-sm font-semibold mt-1")
                for x in report.implausible[:20]:
                    new = x.key() not in report.implausible_in_source
                    with ui.row().classes("w-full items-start gap-2 no-wrap").mark("implausible-value"):
                        ui.badge("after cleaning" if new else "in the chart",
                                 color="red" if new else "orange").props("outline")
                        ui.label(f"{x.message} — {x.line[:200]}") \
                            .classes("text-xs cc-mono flex-grow break-all")

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

    def copy_note(name: str) -> None:
        try:
            text = note_templates_mod.render(name, CLEAN_STATE.get("result_text") or "",
                                             load_config(common.CONFIG_PATH))
        except Exception as ex:
            ui.notify(f"Could not fill the note template: {ex}", type="negative")
            return
        copy_to_clipboard(text, f"“{name}” note copied")

    def open_daily_note_dialog() -> None:
        """Pick a previous chart (same bed tag first) and show today's daily update."""
        if not CLEAN_STATE.get("result_text") or CLEAN_STATE.get("result_mode") not in (None, "clean"):
            ui.notify("Do a full clean first.", type="info")
            return
        tag = CLEAN_STATE.get("tag") or ""
        today_input = CLEAN_STATE.get("input") or ""
        try:
            records = [r for r in recent_charts.load(30) if r["text"] != today_input]
        except Exception:
            records = []
        # same bed first (newest first within each group: load() is newest-first)
        records.sort(key=lambda r: not (tag and (r.get("tag") or "").casefold() == tag.casefold()))
        with ui.dialog() as dlg, ui.card().classes("w-[760px] max-w-full gap-2").mark("daily-note-dialog"):
            ui.label("Daily note").classes("text-lg font-semibold")
            if not records:
                ui.label("No previous charts stored yet — recent charts are kept (encrypted) after "
                         "each clean. Clean yesterday's chart with the same bed tag first.") \
                    .classes("text-sm opacity-70")
                ui.button("Close", on_click=dlg.close).props("flat")
                dlg.open()
                return
            options = {}
            for i, r in enumerate(records):
                first = next((ln.strip() for ln in r["text"].splitlines() if ln.strip()), "")[:60]
                options[i] = f"{r.get('ts', '')[:16].replace('T', ' ')}" + \
                    (f" · {r['tag']}" if r.get("tag") else "") + f" · {first}"
            pick = ui.select(options, value=0, label="Compare with").classes("w-full")
            out = ui.textarea("").props("outlined readonly input-style='min-height: 320px'") \
                .classes("w-full cc-mono").mark("daily-note-output")

            async def build_note() -> None:
                rec = records[pick.value or 0]
                today_text = CLEAN_STATE["result_text"]

                def work():
                    cfg = load_config(common.CONFIG_PATH)
                    previous = Pipeline(cfg, custom_dir=common.CUSTOM_DIR).run(
                        rec["text"], wrap=False, fact_check=False).text
                    return build_daily_note(service_mod._unwrap(today_text), previous, cfg).to_text()

                try:
                    out.set_value(await run.io_bound(work))
                except Exception as ex:
                    report_error("Daily note failed", ex)

            with ui.row().classes("gap-2"):
                ui.button("Build", icon="today", on_click=build_note).props("unelevated color=primary") \
                    .mark("daily-note-build")
                ui.button("Copy", icon="content_copy",
                          on_click=lambda: copy_to_clipboard(out.value or "")).props("flat")
                ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()

    def copy_prompt(name: str) -> None:
        try:
            text = render_prompt(name, CLEAN_STATE.get("result_text") or "", load_config(common.CONFIG_PATH))
        except Exception as ex:
            ui.notify(f"Could not build the prompt: {ex}", type="negative")
            return
        copy_to_clipboard(text, f"“{name}” prompt copied — paste it into your AI tool")

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
                ui.label(g["term"]).classes("font-medium min-w-[200px]")
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

    # ---- rule inbox -------------------------------------------------------------
    async def refresh_inbox() -> None:
        def work():
            return rule_inbox.suggestions(load_config(common.CONFIG_PATH), recent_charts.texts(20))
        try:
            items = await run.io_bound(work)
        except Exception:
            items = []
        inbox["items"] = items
        try:
            inbox_btn.set_text(f"Suggestions ({len(items)})" if items else "Suggestions")
            inbox_btn.props(f"color={'amber-9' if items else 'grey'}")
        except Exception:
            pass  # the page may be gone

    def open_inbox() -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[760px] gap-2").mark("inbox-dialog"):
            ui.label("Rule suggestions").classes("text-lg font-semibold")
            ui.label("From your recent charts (kept encrypted on this computer — see Settings → "
                     "Stored chart data). Lines with clinical values are never suggested for removal.") \
                .classes("text-xs opacity-70")
            body = ui.column().classes("w-full gap-2")

            def draw() -> None:
                body.clear()
                with body:
                    if not inbox["items"]:
                        n = recent_charts.count()
                        ui.label("Nothing to suggest right now." if n >= rule_inbox.MIN_CHARTS else
                                 f"Suggestions start after {rule_inbox.MIN_CHARTS} cleaned charts "
                                 f"({n} so far).").classes("text-sm")
                    for item in list(inbox["items"]):
                        with ui.card().classes("w-full p-3 gap-1"):
                            ui.label(item.title).classes("text-sm font-semibold break-all")
                            ui.label(item.detail).classes("text-sm")
                            for smp in item.samples[:3]:
                                ui.label(smp[:200]).classes("text-xs cc-mono opacity-70 break-all")
                            short = None
                            impact = ui.label("").classes("text-xs opacity-80")
                            with ui.row().classes("items-center gap-2"):
                                if item.kind == "abbreviation":
                                    short = ui.input("Abbreviation", value=item.suggestion) \
                                        .props("dense outlined").classes("w-32")
                                ui.button("Accept", icon="check",
                                          on_click=lambda i=item, s=short: accept(i, s)) \
                                    .props("unelevated dense")
                                if item.kind == "remove_line":
                                    ui.button("Check impact", icon="travel_explore",
                                              on_click=lambda i=item, lbl=impact: check(i, lbl)) \
                                        .props("flat dense")
                                ui.button("Dismiss", icon="close",
                                          on_click=lambda i=item: dismiss(i)).props("flat dense color=grey")

            async def check(item, label) -> None:
                def work():
                    return preview_rule(load_config(common.CONFIG_PATH), "metadata_lines",
                                        item.pattern, recent_charts.texts(20))
                impact = await run.io_bound(work)
                label.set_text(impact.headline.replace("Would also change", "Would change"))

            def accept(item, short) -> None:
                if item.kind == "abbreviation":
                    value = ((short.value if short else "") or item.suggestion).strip()

                    def persist(acknowledged: bool) -> None:
                        cfg = rule_inbox.accept(load_config(common.CONFIG_PATH), item,
                                                replacement=value, acknowledged=acknowledged)
                        save_config_with_backup(cfg)
                        done(item, f"“{item.phrase}” → “{value}” added to your abbreviations.")

                    save_abbreviation_with_safety(item.phrase, value, load_config(common.CONFIG_PATH), persist)
                    return
                try:
                    save_config_with_backup(rule_inbox.accept(load_config(common.CONFIG_PATH), item))
                except Exception as ex:
                    ui.notify(str(ex), type="negative")
                    return
                done(item, "Rule added — lines like this are removed on every clean.")

            def dismiss(item) -> None:
                rule_inbox.dismiss(item.id)
                if item.kind == "abbreviation":
                    try:
                        save_config_with_backup(rule_inbox.reject_phrase(
                            load_config(common.CONFIG_PATH), item.phrase))
                    except Exception:
                        pass
                done(item, "")

            def done(item, message: str) -> None:
                inbox["items"] = [i for i in inbox["items"] if i.id != item.id]
                if message:
                    ui.notify(message, type="positive")
                inbox_btn.set_text(f"Suggestions ({len(inbox['items'])})" if inbox["items"] else "Suggestions")
                draw()

            draw()
            ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()

    # ---- first run ------------------------------------------------------------
    async def try_sample() -> None:
        finish_onboarding()
        load_sample()
        await run_clean()

    def finish_onboarding() -> None:
        common.PREFS["onboarded"] = True
        save_prefs()
        try:
            onboarding.set_visibility(False)
        except Exception:
            pass

    # ---- UI ------------------------------------------------------------------
    def on_mode_change(e) -> None:
        CLEAN_STATE.update(mode=e.value, result=None, result_text="", audit=None,
                           summary=None, qa=[], result_mode=None)
        AUTO_LAST["text"] = None
        render_results()
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

    commands = [
        {"label": "Clean the chart", "icon": "auto_fix_high", "group": "Clean", "run": run_clean},
        {"label": "Paste from clipboard", "icon": "content_paste", "group": "Clean", "run": paste_clipboard},
        {"label": "Load the sample chart", "icon": "science", "group": "Clean", "run": load_sample},
        {"label": "Clear the chart", "icon": "delete_sweep", "group": "Clean", "run": clear_all},
        {"label": "Toggle the AI panel", "icon": "psychology", "group": "Clean", "run": lambda: toggle_ai()},
        {"label": "Show rule suggestions", "icon": "lightbulb", "group": "Clean", "run": open_inbox},
        {"label": "Restore names in an AI reply", "icon": "settings_backup_restore", "group": "Clean",
         "run": open_restore_dialog},
        *[{"label": f"Copy as {label}", "icon": icon, "group": "Copy", "run": lambda k=key: copy_as(k)}
          for key, (label, icon) in COPY_FORMATS.items()],
        {"label": "Review changes", "icon": "rule", "group": "Clean", "run": lambda: show_tab(1)},
        {"label": "Show insights (trends, timeline)", "icon": "insights", "group": "Clean",
         "run": lambda: show_tab(2)},
    ]
    shortcuts = [
        ("mod+enter", "Clean the chart", run_clean),
        ("mod+shift+c", "Copy the result (main Copy format)", lambda: copy_as()),
        ("alt+1", "Output tab", lambda: show_tab(0)),
        ("alt+2", "Review tab", lambda: show_tab(1)),
        ("alt+3", "Insights tab", lambda: show_tab(2)),
        ("alt+a", "Open or close the AI panel", lambda: toggle_ai()),
    ]

    with ui.right_drawer(value=bool(common.PREFS.get("ai_drawer")), fixed=False) \
            .props("width=440 bordered").classes("cc-panel p-4") as ai_drawer:
        ai_col = ui.column().classes("w-full gap-2").mark("ai-panel")

    def header_actions() -> None:
        ui.button(icon="psychology", on_click=lambda: toggle_ai()) \
            .props("flat round color=white aria-label='Local AI panel'").tooltip("Local AI panel (Alt+A)")

    with shell("Clean a chart", "clean", wide=True, commands=commands, shortcuts=shortcuts,
               actions=header_actions):
        errs, _warns = validate_config(load_config(common.CONFIG_PATH))
        if errs:
            with ui.card().classes("w-full border-red-400"):
                ui.label("Current config has problems — fix them on the Pipeline page").classes("font-semibold text-red-600")
                for e in errs[:5]:
                    ui.label(f"• {e}").classes("text-xs text-red-500")

        with ui.card().classes("w-full gap-2 bg-blue-50 dark:bg-slate-900").mark("onboarding") as onboarding:
            ui.label("New here? Four steps").classes("font-semibold")
            with ui.row().classes("w-full gap-6 flex-wrap text-sm"):
                ui.label("① Paste or drop a chart on the left")
                ui.label(f"② Clean ({MOD}+Enter)")
                ui.label("③ Review — click any struck-out text to see the rule")
                ui.label("④ Copy as… Epic-safe, Markdown or an AI prompt")
            with ui.row().classes("gap-2"):
                ui.button("Try it on the sample chart", icon="science", on_click=try_sample) \
                    .props("unelevated no-caps")
                ui.button("Got it", on_click=finish_onboarding).props("flat no-caps")
        onboarding.set_visibility(not common.PREFS.get("onboarded"))

        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            mode_sel = ui.toggle({"clean": "Full clean", "abbreviations": "Abbreviations only",
                                  "expand": "Expand abbreviations"},
                                 value=CLEAN_STATE.get("mode", "clean"),
                                 on_change=on_mode_change)
            presets = store.list_presets()
            preset_sel = ui.select(
                options={**{p: f"📦 {p}" for p in presets}, "": "(config.json — current rules)"},
                value=common.PREFS.get("last_preset") if common.PREFS.get("last_preset") in presets else "",
                label="Rule preset",
            ).props("dense outlined").classes("w-60")
            ui.space()
            inbox_btn = ui.button("Suggestions", icon="lightbulb", on_click=open_inbox) \
                .props("flat no-caps color=grey").mark("inbox")
            ui.button("AI panel", icon="psychology", on_click=lambda: toggle_ai()).props("flat no-caps")
        mode_note = ui.label("").classes("text-sm opacity-70 -mt-2")

        layout = ui.element("div").classes("grid grid-cols-1 gap-6 w-full items-start")
        with layout:
            # ---- left: the chart ----
            with ui.column().classes("w-full gap-2 min-w-0"):
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    clean_btn = ui.button("Clean", icon="auto_fix_high", on_click=run_clean)
                    clean_btn.mark("run-clean")
                    clean_btn.props("unelevated color=primary no-caps").tooltip(f"{MOD}+Enter")
                    spinner = ui.spinner("dots", size="lg")
                    spinner.set_visibility(False)
                    ui.button("Paste", icon="content_paste", on_click=paste_clipboard).props("outline no-caps")
                    ui.button("Sample", icon="science", on_click=load_sample).props("flat no-caps") \
                        .tooltip("Load the sample chart")
                    ui.button("Clear", icon="delete_sweep", on_click=clear_all).props("flat no-caps")
                    ui.space()
                    ui.switch("Auto-clean", value=bool(common.PREFS.get("auto_clean")),
                              on_change=lambda e: (common.PREFS.update(auto_clean=e.value), save_prefs())) \
                        .tooltip(f"Clean {AUTO_CLEAN_PAUSE:g} s after you stop typing")

                with ui.row().classes("w-full items-center gap-1 flex-wrap rounded-lg px-2 py-1 "
                                      "bg-amber-50 dark:bg-slate-800").mark("selection-bar") as sel_bar:
                    ui.icon("highlight").classes("text-amber-700")
                    sel_text = ui.label("").classes("text-xs cc-mono max-w-[220px] truncate")
                    ui.button("Remove", icon="backspace", on_click=sel_remove) \
                        .props("flat dense no-caps").mark("sel-remove") \
                        .tooltip("Remove this selection now and on every future clean")
                    ui.button("Replace…", icon="find_replace", on_click=sel_replace) \
                        .props("flat dense no-caps").mark("sel-replace")
                    ui.button("Abbreviate…", icon="short_text", on_click=sel_abbreviate) \
                        .props("flat dense no-caps").mark("sel-abbreviate")
                    ui.button("Never remove", icon="shield", on_click=sel_keep) \
                        .props("flat dense no-caps").mark("sel-keep") \
                        .tooltip("Rules will never remove text containing this")
                    ui.button("More…", icon="school", on_click=sel_more) \
                        .props("flat dense no-caps").mark("sel-more") \
                        .tooltip("Advanced: remove whole lines, edit the text, check your recent charts")
                    with ui.button(icon="tune").props("flat dense round").tooltip("Matching options"):
                        with ui.menu().classes("p-2"):
                            word_check = ui.checkbox("Whole words only", value=True)
                            case_check = ui.checkbox("Match case", value=False)
                    ui.button(icon="close", on_click=hide_selection_bar).props("flat dense round")
                sel_bar.set_visibility(False)
                with ui.row().classes("w-full items-center gap-2 -mt-1"):
                    highlight_status = ui.label("Select text in the chart to remove, replace or "
                                                "abbreviate it — and remember that for next time.") \
                        .classes("text-xs opacity-70 flex-grow").props("role=status aria-live=polite")
                    undo_btn = ui.button("Undo last highlight", icon="undo", on_click=undo_highlight) \
                        .props("flat dense no-caps")
                    undo_btn.disable()

                if HAS_EX4:
                    # ex4nicegui gives the textarea a reactive value signal; the plain
                    # NiceGUI element underneath keeps every existing code path intact.
                    _rx_input = rxui.textarea(
                        "Chart text (paste an Epic export, drop a file, or load the sample)",
                        value=CLEAN_STATE["input"], on_change=on_input_change)
                    input_area = _rx_input.element
                else:
                    input_area = ui.textarea("Chart text (paste an Epic export, drop a file, or load the sample)",
                                             value=CLEAN_STATE["input"], on_change=on_input_change)
                input_area.props("outlined input-style='min-height: 420px'") \
                    .classes("w-full cc-mono cc-learn-src")
                input_area.mark("highlight-source")
                for event_name in ("mouseup", "keyup", "touchend"):
                    input_area.on(event_name, on_highlight, js_handler=SELECTION_HANDLER)

                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.label().bind_text_from(input_area, "value", lambda t:
                        f"{len(t or ''):,} chars · {len((t or '').split()):,} words").classes("text-xs opacity-60")
                    tag_input = ui.input("Bed tag", value=CLEAN_STATE.get("tag") or "",
                                         autocomplete=recent_charts.tags(),
                                         on_change=lambda e: CLEAN_STATE.update(
                                             tag=recent_charts.clean_tag(e.value))) \
                        .props("dense outlined clearable").classes("w-32").mark("bed-tag")
                    tag_input.tooltip("e.g. G20-1 — kept with this chart (encrypted) so the daily "
                                      "note and trends follow the patient, not the paste")
                    ui.space()
                    ui.upload(on_upload=handle_upload, multiple=True, auto_upload=True,
                              label="Drop .txt / .docx / .pdf") \
                        .props("accept=.txt,.md,.docx,.pdf,text/plain flat bordered dense hide-upload-btn") \
                        .classes("w-[240px]").style("max-height: 72px")
                    ui.button("Batch", icon="layers", on_click=lambda: ui.navigate.to("/batch")) \
                        .props("flat dense no-caps").tooltip("Many files? Use the Batch page")

            # ---- right: the result ----
            results_col = ui.column().classes("w-full gap-2 min-w-0").mark("results")

        preset_sel.on_value_change(on_preset_change)
        sync_mode_controls()
        sync_layout()
        ui.timer(0.5, auto_tick)
        ui.timer(0.3, refresh_inbox, once=True)
        render_results()


# ===========================================================================
# PAGE: Batch
# ===========================================================================
