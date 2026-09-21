#!/usr/bin/env python3
"""Chart Cleaner — local desktop app (runs in your browser, stays on your machine).

Pages:
  /          Clean      — paste or drop charts, live diff, per-run stats, batch folder
  /pipeline  Pipeline   — edit/reorder/enable every cleaning rule, presets, import/export
  /stats     Statistics — history dashboard: how much text was cleaned, by what
  /scripts   Scripts    — create and edit custom Python cleaning rules
  /settings  Settings   — output options, NLP status, data management

Run with:  python app.py            (or the platform launcher / double-click helper)
           python app.py --help     for options
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import html as html_mod
import json
import os
import re
import socket
import subprocess
import sys
import time
import threading
import traceback
import webbrowser
from contextlib import contextmanager
from importlib.util import find_spec
from pathlib import Path

from nicegui import app, run, ui

from chartcleaner import __version__
from chartcleaner import store
from chartcleaner.audit import (
    CHECK_DESCRIPTIONS,
    CHECK_LABELS,
    ensure_audit_section,
    get_audit_config,
    run_audit,
    suggestion_for_signature,
)
from chartcleaner.engine import (
    BUILTIN_STAGE_IDS,
    CUSTOM_RULE_TEMPLATE,
    STAGE_LABELS,
    DEFAULT_WHITESPACE,
    ConfigError,
    Pipeline,
    CleanContext,
    _load_custom_module,
    list_custom_rules,
    load_config,
    load_default_config,
    save_config,
    validate_config,
)
from chartcleaner.ingest import IngestError, converters_status, load_file, supported_extensions
from chartcleaner import rulepacks
from chartcleaner import tokens as tokens_mod
from chartcleaner import watcher as watcher_mod
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE, PENDING_RULE, PIPE_TEST
from chartcleaner.benchmark import generate as generate_benchmark
from chartcleaner.evaluate import evaluate as evaluate_samples
from chartcleaner.evaluate import load_last_evaluation, save_evaluation
from chartcleaner.local_llm import LocalLlmClient
from chartcleaner.summarizer import (
    LlmUnavailableError,
    NoModelError,
    merge_llm_config,
    summarize,
)

store.ensure_dirs()
store.seed_frozen_assets()

if getattr(sys, "frozen", False):
    # Windowed bundles have no console; capture output into data/app.log
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _log = open(store.DATA_DIR / "app.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = _log
    sys.stderr = _log

BASE_DIR = store.BASE_DIR
CONFIG_PATH = store.CONFIG_PATH
CUSTOM_DIR = store.CUSTOM_RULES_DIR

PREFS = store.load_prefs()
PIPE_TEST["text"] = (BASE_DIR / "sample_chart.txt").read_text(encoding="utf-8") \
    if (BASE_DIR / "sample_chart.txt").exists() else ""

# Folder watcher singleton + mirrored config (created by the Settings page)
WATCHER: dict = {"obj": None}
WATCHER_CONFIG: dict = store.load_watch_config()

# Optional reactive UI helpers (ex4nicegui); the app works fine without it.
try:
    from ex4nicegui.reactive import rxui
    HAS_EX4 = True
except Exception:  # pragma: no cover - optional dependency
    rxui = None
    HAS_EX4 = False


def start_pending_rule(pattern: str, replacement: str | None, stage: str) -> None:
    PENDING_RULE.clear()
    PENDING_RULE.update(pattern=pattern, replacement=replacement, stage=stage)
    ui.navigate.to("/pipeline")

NAV = [
    ("/", "cleaning_services", "Clean"),
    ("/pipeline", "tune", "Pipeline & Rules"),
    ("/stats", "insights", "Statistics"),
    ("/scripts", "code", "Custom Scripts"),
    ("/settings", "settings", "Settings"),
]

CSS = """
<style>
.cc-mono textarea, .cc-mono input { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important; }
.cc-diff td { vertical-align: top; padding: 0 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              font-size: 12px; white-space: pre-wrap; word-break: break-word; }
.cc-del { background: rgba(255, 60, 60, 0.13); }
.cc-ins { background: rgba(40, 200, 90, 0.15); }
.cc-rep { background: rgba(255, 190, 40, 0.18); }
.cc-flag { background: rgba(245, 158, 11, 0.16); box-shadow: inset 3px 0 0 #f59e0b; }
.cc-diff-left { border-right: 1px solid rgba(128,128,128,0.35); }
</style>
"""


def save_prefs() -> None:
    store.save_prefs(PREFS)


def esc(s: str) -> str:
    return html_mod.escape(s)


def save_config_with_backup(cfg: dict) -> None:
    """Save config.json and drop a timestamped copy into data/backups."""
    save_config(cfg, CONFIG_PATH)
    try:
        store.rotate_config_backup()
    except Exception:
        pass  # backups must never block a save


def report_error(title: str, exc: Exception) -> None:
    """Show a traceback dialog and append the same text to data/app.log."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        store.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (store.DATA_DIR / "app.log").open("a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n{tb}\n")
    except OSError:
        pass
    with ui.dialog() as dlg, ui.card().classes("w-[560px]"):
        ui.label(title).classes("font-semibold text-red-600")
        with ui.scroll_area().classes("w-full max-h-64 border rounded bg-grey-1 dark:bg-grey-10 p-2"):
            ui.html(f"<pre style='white-space:pre-wrap;font-size:11px'>{esc(tb)}</pre>")
        with ui.row():
            ui.button("Copy traceback", icon="content_copy",
                      on_click=lambda: copy_to_clipboard(tb, "Traceback copied")).props("flat")
            ui.button("Close", on_click=dlg.close).props("flat")
    dlg.open()


def count_matches(pattern: str, text: str, flags: int) -> int:
    try:
        return len(re.findall(pattern, text, flags=flags)) if text else 0
    except re.error:
        return -1


# ---------------------------------------------------------------------------
# small helpers shared by pages
# ---------------------------------------------------------------------------

def open_folder(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        ui.notify(f"Could not open folder: {e}", type="warning")


def download_file(url: str, filename: str) -> None:
    ui.run_javascript(
        f'const a = document.createElement("a"); a.href="{url}"; '
        f'a.download="{filename}"; document.body.appendChild(a); a.click(); a.remove();'
    )


def copy_to_clipboard(text: str, note: str = "Copied to clipboard") -> None:
    ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text)});")
    ui.notify(note, type="positive")


def stat_chip(container, label: str, value: str, color: str = "primary") -> None:
    with container:
        with ui.card().classes("py-3 px-5 items-center min-w-[130px]"):
            ui.label(value).classes(f"text-xl font-bold text-{color}")
            ui.label(label).classes("text-xs opacity-70")


def diff_html(before: str, after: str, flag_lines: set[int] | None = None) -> str:
    if len(before) + len(after) > 600_000:
        return "<p>Diff too large to display (use a smaller input).</p>"
    flagged = flag_lines or set()
    a_lines, b_lines = before.splitlines(), after.splitlines()
    sm = difflib.SequenceMatcher(a=a_lines, b=b_lines, autojunk=False)
    rows: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                jl = j1 + k
                cls = " cc-flag" if (jl + 1) in flagged else ""
                rows.append(f"<tr><td class='cc-diff-left'>{esc(a_lines[i1 + k])}</td>"
                            f"<td class='{cls.strip()}'>{esc(b_lines[jl])}</td></tr>")
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(f"<tr><td class='cc-diff-left cc-del'>{esc(a_lines[k])}</td><td></td></tr>")
        elif tag == "insert":
            for k in range(j1, j2):
                cls = "cc-ins cc-flag" if (k + 1) in flagged else "cc-ins"
                rows.append(f"<tr><td class='cc-diff-left'></td><td class='{cls}'>{esc(b_lines[k])}</td></tr>")
        else:  # replace
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                left = esc(a_lines[i1 + k]) if i1 + k < i2 else ""
                right = esc(b_lines[j1 + k]) if j1 + k < j2 else ""
                jl = j1 + k
                cls = "cc-ins cc-flag" if (jl + 1) in flagged else "cc-ins"
                rows.append(f"<tr><td class='cc-diff-left cc-del'>{left}</td>"
                            f"<td class='{cls}'>{right}</td></tr>")
    return ("<table class='cc-diff' style='width:100%; border-collapse:collapse'>"
            + "".join(rows) + "</table>")


@contextmanager
def shell(title: str, active: str):
    ui.add_head_html(CSS)
    dark = ui.dark_mode(bool(PREFS.get("dark", True)))

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("health_and_safety").classes("text-2xl")
            ui.label("Chart Cleaner").classes("text-xl font-bold cursor-pointer").on("click", lambda: ui.navigate.to("/"))
            ui.badge(f"v{__version__}", color="blue-grey").props("outline")
        ui.switch("Dark", value=dark.value,
                  on_change=lambda e: (dark.set_value(e.value), PREFS.update(dark=e.value), save_prefs()))

    with ui.left_drawer(fixed=True, value=True).classes("bg-grey-1 dark:bg-grey-10"):
        ui.label("Menu").classes("text-xs uppercase opacity-60 ml-2")
        for path, icon, label in NAV:
            btn = ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p))
            btn.props("flat align=left no-caps").classes("w-full")
            if path == active:
                btn.props("color=primary").classes("font-semibold bg-blue-1 dark:bg-blue-9")

    with ui.footer().classes("bg-transparent text-xs opacity-60"):
        ui.label("Runs entirely on this computer — 127.0.0.1 only. Not a guarantee of "
                 "de-identification; review output before sharing.")

    with ui.column().classes("w-full max-w-[1150px] mx-auto p-6 gap-4"):
        ui.label(title).classes("text-2xl font-semibold")
        yield


def confirm_dialog(message: str, action) -> None:
    with ui.dialog() as dlg, ui.card():
        ui.label(message)
        with ui.row():
            ui.button("Confirm", color="negative", on_click=lambda: (dlg.close(), action()))
            ui.button("Cancel", on_click=dlg.close).props("flat")
    dlg.open()


# ===========================================================================
# PAGE: Clean
# ===========================================================================

@ui.page("/")
async def clean_page():
    state = {"running": False}

    # ---- handlers (defined before the UI that references them) -------------
    def set_running(flag: bool) -> None:
        state["running"] = flag
        clean_btn.set_enabled(not flag)
        spinner.set_visibility(flag)

    async def do_clean_core(text: str, source: str) -> None:
        set_running(True)
        try:
            def work():
                cfg = load_config(CONFIG_PATH)
                result = Pipeline(cfg, custom_dir=CUSTOM_DIR).run(text)
                return result, run_audit(result.text, cfg)

            result, audit = await run.io_bound(work)
            CLEAN_STATE.update(input=text, result_text=result.text, result=result, audit=audit,
                               summary=None)  # previous summary refers to the old cleaned text
            AUTO_LAST["text"] = text
            store.append_run(result.to_history_dict(source))
            # persist reversible-token maps produced by the tokenize stage
            try:
                for st in result.stages:
                    tmap = st.details.get("token_map")
                    if tmap:
                        store.save_token_map(tmap, source)
            except Exception:
                pass  # map saving must never break a run
            try:
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
            phi = result.phi_counts()
            chips = ui.row().classes("gap-3 flex-wrap items-stretch")
            stat_chip(chips, "characters", f"{result.chars_before:,} → {result.chars_after:,}")
            stat_chip(chips, "reduction", f"{result.reduction:+.1f}%",
                      "green" if result.reduction >= 0 else "orange")
            stat_chip(chips, "words", f"{result.words_before:,} → {result.words_after:,}", "indigo")
            stat_chip(chips, "PHI redacted", str(sum(phi.values())), "red")
            stat_chip(chips, "elapsed", f"{result.duration_ms:.0f} ms", "blue-grey")

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

            with ui.tabs() as tabs:
                t_result = ui.tab("Result")
                t_diff = ui.tab("Side-by-side diff")
                t_stages = ui.tab("What each stage did")
            with ui.tab_panels(tabs, value=t_result).classes("w-full"):
                with ui.tab_panel(t_result):
                    out = ui.textarea("", value=result.text)
                    out.props("outlined readonly input-style='min-height: 240px'").classes("w-full cc-mono")
                    with ui.row().classes("gap-2"):
                        ui.button("Copy result", icon="content_copy",
                                  on_click=lambda: copy_to_clipboard(result.text)).props("unelevated color=primary")
                        ui.button("Download .txt", icon="download", on_click=download_result)
                        ui.button("Copy result + stats", icon="data_object",
                                  on_click=lambda: copy_to_clipboard(
                                      result.text + "\n\n<!-- " + result.summary() + " -->")).props("flat")
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
            # ---- local AI summary (on-device via Ollama) ----
            summary_refs.clear()
            with ui.expansion("Local AI summary", icon="psychology").classes("w-full"):
                try:
                    opts = merge_llm_config(load_config(CONFIG_PATH))
                except Exception:
                    opts = merge_llm_config({})
                try:
                    probe = LocalLlmClient(str(opts["base_url"]), timeout=2.0)
                    listed = probe.list_models() if probe.is_available() else []
                except Exception:
                    listed = []
                model_val = str(opts["model"] or (listed[0] if listed else ""))
                if not listed:
                    ui.label(
                        f"No local LLM detected at {opts['base_url']} — install Ollama "
                        "(ollama.com) and pull a model, e.g. `ollama pull llama3.1`."
                    ).classes("text-xs text-orange-600")
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.select(listed or ([model_val] if model_val else []),
                              value=model_val, label="Model", new_value_mode="add",
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
            cfg = load_config(CONFIG_PATH)
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
        summary_state["running"] = True
        sum_btn = summary_refs.get("button")
        sum_spin = summary_refs.get("spinner")
        if sum_btn:
            sum_btn.set_enabled(False)
        if sum_spin:
            sum_spin.set_visibility(True)
        try:
            def work():
                return summarize(CLEAN_STATE["result_text"], load_config(CONFIG_PATH))

            CLEAN_STATE["summary"] = await run.io_bound(work)
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
        CLEAN_STATE.update(input="", result=None, result_text="", summary=None)
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
            data = e.content.read()
        except Exception as ex:
            ui.notify(f"Could not read {e.name}: {ex}", type="negative")
            return
        suffix = Path(e.name).suffix.lower()
        if suffix not in supported_extensions():
            ui.notify(f"Unsupported file type '{suffix}'", type="negative")
            return

        def work() -> tuple[str, str, list[str]]:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(data)
                tmp_path = Path(tf.name)
            try:
                ing = load_file(tmp_path, load_config(CONFIG_PATH))
                return ing.text, ing.engine, ing.warnings
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        try:
            text, engine, warns = await run.io_bound(work)
        except IngestError as ex:
            ui.notify(f"Could not ingest {e.name}: {ex}", type="negative")
            return
        except Exception as ex:
            report_error(f"Ingesting {e.name} failed", ex)
            return
        if CLEAN_STATE["input"].strip():
            CLEAN_STATE["input"] += "\n\n===== " + e.name + " =====\n" + text
        else:
            CLEAN_STATE["input"] = text
        input_area.set_value(CLEAN_STATE["input"])
        note = f"Loaded {e.name} via {engine}"
        if warns:
            note += " — " + " | ".join(warns[:2])
        ui.notify(note, type="positive")

    async def on_preset_change(e) -> None:
        name = e.value
        PREFS.update(last_preset=name)
        save_prefs()
        if not name:
            return
        try:
            cfg = store.load_preset(name)
            perrs, _ = validate_config(cfg)
            if perrs:
                ui.notify("Preset has invalid rules: " + "; ".join(perrs[:3]), type="negative")
                return
            save_config(cfg, CONFIG_PATH)
            ui.notify(f"Preset '{name}' applied.", type="positive")
        except Exception as ex:
            ui.notify(f"Could not apply preset: {ex}", type="negative")

    async def process_batch() -> None:
        folder = Path(folder_input.value or "").expanduser()
        if not folder.is_dir():
            ui.notify("Folder not found.", type="negative")
            return
        exts = set(supported_extensions())
        files = sorted(f for f in folder.iterdir()
                       if f.is_file() and f.suffix.lower() in exts)
        if not files:
            ui.notify(f"No supported files ({', '.join(sorted(exts))}) in that folder.", type="warning")
            return
        batch_btn.set_enabled(False)
        try:
            cfg = load_config(CONFIG_PATH)
            out_dir = store.EXPORTS_DIR / f"batch_{time.strftime('%Y%m%d_%H%M%S')}"
            out_dir.mkdir(parents=True, exist_ok=True)

            def work():
                pipe = Pipeline(cfg, custom_dir=CUSTOM_DIR)
                out = []
                for f in files[:500]:
                    try:
                        ing = load_file(f, cfg)
                    except IngestError as ex:
                        out.append({"file": f.name, "before": 0, "after": 0,
                                    "reduction": f"skipped: {ex}"})
                        continue
                    r = pipe.run(ing.text)
                    out_name = f"{f.stem}_cleaned.txt"
                    (out_dir / out_name).write_text(r.text, encoding="utf-8")
                    store.append_run(r.to_history_dict(f"batch:{folder.name}"))
                    out.append({"file": f.name, "before": len(ing.text), "after": len(r.text),
                                "reduction": f"{r.reduction:+.1f}%"})
                return out

            rows = await run.io_bound(work)
            batch_table_holder.clear()
            with batch_table_holder:
                cols = [
                    {"name": "file", "label": "File", "field": "file", "align": "left"},
                    {"name": "before", "label": "Chars before", "field": "before"},
                    {"name": "after", "label": "Chars after", "field": "after"},
                    {"name": "reduction", "label": "Reduction", "field": "reduction"},
                ]
                ui.table(columns=cols, rows=rows, row_key="file", pagination=15) \
                    .classes("w-full").props("flat dense")
                ui.button("Open exports folder", icon="folder",
                          on_click=lambda: open_folder(out_dir)).props("flat")
            ui.notify(f"Processed {len(rows)} file(s).", type="positive")
        except ConfigError as e:
            ui.notify(str(e), type="negative")
        except Exception as e:
            report_error("Batch processing failed", e)
        finally:
            batch_btn.set_enabled(True)

    def on_key(e) -> None:
        try:
            mods = set(e.modifiers or [])
            if e.key == "Enter" and mods & {"Control", "Meta"}:
                asyncio.get_running_loop().create_task(run_clean())
        except Exception:
            pass

    async def auto_tick() -> None:
        if not PREFS.get("auto_clean") or state["running"]:
            return
        text = input_area.value or ""
        if text.strip() and text != AUTO_LAST["text"]:
            AUTO_LAST["text"] = text
            await do_clean_core(text, "editor")
        elif not text.strip():
            AUTO_LAST["text"] = None

    # ---- UI ------------------------------------------------------------------
    with shell("Clean a chart", "clean"):
        errs, _warns = validate_config(load_config(CONFIG_PATH))
        if errs:
            with ui.card().classes("w-full border-red-400"):
                ui.label("Current config has problems — fix them on the Pipeline page").classes("font-semibold text-red-600")
                for e in errs[:5]:
                    ui.label(f"• {e}").classes("text-xs text-red-500")

        presets = store.list_presets()
        preset_sel = ui.select(
            options={**{p: f"📦 {p}" for p in presets}, "": "(config.json — current rules)"},
            value=PREFS.get("last_preset") if PREFS.get("last_preset") in presets else "",
            label="Rule preset",
        ).classes("w-60")

        clean_btn = ui.button("Clean", icon="auto_fix_high", on_click=run_clean)
        clean_btn.props("unelevated color=primary")
        spinner = ui.spinner("dots", size="lg")
        spinner.set_visibility(False)
        ui.button("Paste from clipboard", icon="content_paste", on_click=paste_clipboard)
        ui.button("Clear", icon="delete_sweep", on_click=clear_all).props("flat")
        ui.switch("Auto-clean as I type", value=bool(PREFS.get("auto_clean")),
                  on_change=lambda e: (PREFS.update(auto_clean=e.value), save_prefs()))
        ui.label("Tip: Ctrl/⌘+Enter cleans.").classes("text-xs opacity-60 ml-auto")

        if HAS_EX4:
            # ex4nicegui gives the textarea a reactive value signal; the plain
            # NiceGUI element underneath keeps every existing code path intact.
            _rx_input = rxui.textarea(
                "Chart text (paste an Epic export, drop a file, or load the sample)",
                value=CLEAN_STATE["input"],
                on_change=lambda e: CLEAN_STATE.update(input=e.value))
            input_area = _rx_input.element
            input_area.props("outlined input-style='min-height: 220px'").classes("w-full cc-mono")
            rxui.label(lambda: (lambda t: f"{len(t):,} chars · {len(t.split()):,} words")(
                _rx_input.value or "")).classes("text-xs opacity-60")
        else:
            input_area = ui.textarea("Chart text (paste an Epic export, drop a file, or load the sample)",
                                     value=CLEAN_STATE["input"],
                                     on_change=lambda e: CLEAN_STATE.update(input=e.value))
            input_area.props("outlined input-style='min-height: 220px'").classes("w-full cc-mono")
            ui.label("").classes("text-xs opacity-60")

        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            ui.upload(on_upload=handle_upload, multiple=True, auto_upload=True) \
                .props("accept=.txt,.md,.docx,.pdf,text/plain flat").classes("max-w-xs")
            ui.button("Load sample chart", icon="science", on_click=load_sample).props("flat")

        results_col = ui.column().classes("w-full gap-3")

        with ui.expansion("Batch folder (.txt/.md/.docx/.pdf files)", icon="folder_open").classes("w-full"):
            ui.label("Clean every .txt in a folder on this computer. Outputs are saved to a timestamped "
                     "folder inside data/exports, downloadable below.").classes("text-xs opacity-70")
            with ui.row().classes("w-full items-center gap-2"):
                folder_input = ui.input("Folder path", placeholder="/path/to/charts").classes("w-96 cc-mono")
                batch_btn = ui.button("Process folder", icon="layers", on_click=process_batch).props("outline")
            batch_table_holder = ui.column().classes("w-full")

        preset_sel.on_value_change(on_preset_change)
        ui.keyboard(on_key=on_key)
        ui.timer(1.2, auto_tick)

        if CLEAN_STATE.get("result"):
            render_results()


# ===========================================================================
# PAGE: Pipeline & Rules
# ===========================================================================

STAGE_EDITORS = {
    "metadata_lines": ("regex_lines", "emr_line_metadata"),
    "boilerplate": ("regex_lines", "boilerplate"),
    "phi_patterns": ("regex_pairs", "epic_phi_patterns"),
    "clinical_identifiers": ("clinical_identifiers", "clinical_identifiers"),
    "literal_replacements": ("regex_pairs", "literal_replacements"),
    "headers": ("headers", "clinical_headers"),
    "duplicate_notes": ("dedup_notes", "duplicate_note_detection"),
    "fuzzy_dedup": ("fuzzy", "fuzzy_dedup"),
    "nlp_redaction": ("nlp", None),
    "tokenize_phi": ("tokenize", "tokenization"),
    "unicode_normalize": ("unicode", "unicode_normalize"),
    "timestamps": ("timestamps", "timestamp_removal"),
    "sections": ("sections", "section_filter"),
    "whitespace": ("whitespace", "whitespace"),
    "caps_normalize": ("caps", "caps_normalize"),
    "bullets": ("bullets", "bullets"),
    "line_length": ("line_length", "line_length"),
}

STAGE_DESCRIPTIONS = {
    "metadata_lines": "Whole lines to delete (author/pager/version lines, Epic chrome). Case-insensitive regex per line.",
    "boilerplate": "Blocks to delete anywhere (disclaimers, empty SmartSections). Dot matches newlines.",
    "tokenize_phi": "Reversible tokenization: swaps structured PHI for [[T1]]-style codes and saves the value→token map (Settings → Token maps). Runs before redaction, so enable it instead of — not on top of — the PHI patterns you want tokenized.",
    "phi_patterns": "Regex → replacement pairs for structured PHI (MRN, DOB, phone lines).",
    "clinical_identifiers": "Off by default. Algorithmically recognizes and redacts verified National Provider IDs (NPI via Luhn checksum), DEA numbers (checksum verified), and UDI medical device barcodes.",
    "nlp_redaction": "Presidio NLP redaction: entity types, replacements, confidence threshold and allow-list.",
    "literal_replacements": "Regex → replacement pairs for abbreviations and text fixes.",
    "unicode_normalize": "Off by default. Turn any of these on to replace curly quotes, en/em dashes, non-breaking spaces, zero-width characters, ellipses and ligatures with plain equivalents — great before LLM use.",
    "timestamps": "Off by default. Removes dates (ISO, US, 'Mar 5, 2024') and optionally bare clock times, replacing them with configurable text.",
    "sections": "Off by default. Drop only the listed sections, or keep only the listed ones. Section boundaries come from your header list unless you supply boundary headers.",
    "whitespace": "Trims trailing spaces and collapses 3+ blank lines by default; seven further switches (CRLF, leading indent, tabs, double spaces, edge trim…).",
    "duplicate_notes": "Folds near-duplicate Epic note blocks (same note pasted twice) by comparing bodies.",
    "fuzzy_dedup": "Collapses paragraphs that are nearly identical (copy-forwarded text).",
    "headers": "Lines matching one of these names become Markdown headings. Choose the heading level, bold style, colon retention, or restrict to ALL-CAPS lines. Optionally let medspaCy detect section titles instead.",
    "caps_normalize": "Off by default. Rewrites long ALL-CAPS lines into sentence case, preserving chosen acronyms.",
    "bullets": "Normalizes •, * and - bullet prefixes to a marker you choose. Optionally converts numbered lists and drops empty bullets.",
    "line_length": "Off by default. Truncates or wraps lines longer than a set width (wrap keeps indentation).",
}

STAGE_FLAGS = {
    "metadata_lines": re.IGNORECASE | re.MULTILINE,
    "boilerplate": re.IGNORECASE | re.MULTILINE | re.DOTALL,
    "phi_patterns": re.IGNORECASE,
    "literal_replacements": re.IGNORECASE,
}


@ui.page("/pipeline")
def pipeline_page():
    try:
        draft = load_config(CONFIG_PATH)
    except ConfigError as e:
        def restore_defaults() -> None:
            try:
                save_config_with_backup(load_default_config())
                ui.notify("Factory default rules restored.", type="positive")
                ui.navigate.to("/pipeline")
            except Exception as ex:
                ui.notify(str(ex), type="negative")

        with shell("Pipeline & Rules", "pipeline"):
            ui.label(f"Cannot load config: {e}").classes("text-red-600")
            ui.button("Restore factory defaults", on_click=restore_defaults).props("outline")
        return

    customs = list_custom_rules(CUSTOM_DIR)

    # ---- audit draft rules + suggestions (defined before the UI) -----------

    def render_pending_card() -> None:
        pending_holder.clear()
        if not PENDING_RULE:
            return
        with pending_holder:
            with ui.card().classes("w-full border-amber-400 gap-2"):
                ui.label("Draft rule from an audit finding — review, then add it to a stage") \
                    .classes("font-semibold text-amber-600")
                pat_in = ui.input("Regex pattern", value=PENDING_RULE.get("pattern", "")) \
                    .props("outlined dense").classes("w-full cc-mono")
                repl_in = None
                if PENDING_RULE.get("replacement") is not None:
                    repl_in = ui.input("Replace with", value=PENDING_RULE["replacement"]) \
                        .props("outlined dense").classes("w-full")
                target_sel = ui.select(
                    options={"phi_patterns": "Structured PHI patterns (regex → replacement)",
                             "emr_line_metadata": "EMR line metadata (delete whole lines)",
                             "literal_replacements": "Literal replacements"},
                    value=PENDING_RULE.get("stage") or "phi_patterns",
                    label="Add to stage",
                ).classes("w-full max-w-[460px]")
                count_lbl = ui.label("").classes("text-xs opacity-80")

                def sync_count() -> None:
                    flags = re.IGNORECASE
                    if target_sel.value == "emr_line_metadata":
                        flags |= re.MULTILINE
                    n = count_matches(pat_in.value, PIPE_TEST["text"], flags)
                    count_lbl.set_text(f"{n} hit(s) in the current test text" if n >= 0 else "bad regex")

                pat_in.on_value_change(lambda e: sync_count())
                target_sel.on_value_change(lambda e: sync_count())
                sync_count()

                def add_to_stage() -> None:
                    key = {"phi_patterns": "epic_phi_patterns",
                           "literal_replacements": "literal_replacements",
                           "emr_line_metadata": "emr_line_metadata"}[target_sel.value]
                    if key == "emr_line_metadata":
                        draft.setdefault(key, []).append(pat_in.value)
                    else:
                        draft.setdefault(key, []).append(
                            [pat_in.value, repl_in.value if repl_in is not None else ""])
                    PENDING_RULE.clear()
                    render_pending_card()
                    refresh_stages()
                    ui.notify(f"Pattern added to “{target_sel.label or target_sel.value}” — "
                              "check the hit counts, then Save changes.", type="positive")

                with ui.row().classes("gap-2"):
                    ui.button("Add to stage", icon="add", on_click=add_to_stage) \
                        .props("unelevated color=primary")
                    ui.button("Discard", icon="close",
                              on_click=lambda: (PENDING_RULE.clear(), render_pending_card())) \
                        .props("flat")

    def render_suggestions() -> None:
        suggestions_holder.clear()
        try:
            suggestions = store.get_suggestions()
        except Exception:
            suggestions = []
        if not suggestions:
            return
        with suggestions_holder:
            ui.label("Suggestions from your recent runs").classes("font-semibold")
            ui.label("Findings that keep surviving run after run. Adopting opens a draft rule "
                     "below — nothing is saved until you confirm.").classes("text-xs opacity-60 -mt-2")
            for s in suggestions[:6]:
                payload = suggestion_for_signature(s["signature"])
                with ui.card().classes("w-full gap-1"):
                    ui.label(f"{payload['title']} — survived {s['runs']} recent run(s)").classes("font-medium")
                    ui.label(payload["detail"]).classes("text-xs opacity-70 -mt-2")
                    if payload["regex"]:
                        ui.label(payload["regex"]).classes("text-xs cc-mono opacity-80")
                    with ui.row().classes("gap-2"):
                        ui.button("Adopt", icon="rule",
                                  on_click=lambda p=payload: (
                                      PENDING_RULE.clear(),
                                      PENDING_RULE.update(pattern=p["regex"], replacement=p["replacement"],
                                                          stage=p["stage"]),
                                      render_pending_card(),
                                  )).props("outline dense")
                        ui.button("Dismiss forever", icon="block",
                                  on_click=lambda sig=s["signature"]: dismiss_suggestion_clicked(sig)) \
                            .props("flat dense")

    def dismiss_suggestion_clicked(sig: str) -> None:
        try:
            store.dismiss_suggestion(sig)
        except Exception as e:
            ui.notify(f"Could not record dismissal: {e}", type="warning")
        render_suggestions()

    def render_audit_card() -> None:
        audit_holder.clear()
        with audit_holder:
            section = ensure_audit_section(draft)
            checks = section.get("checks", {})
            ui.switch("Run the post-run review after every clean",
                      value=bool(section.get("enabled", True)),
                      on_change=lambda e: section.update(enabled=e.value))
            ui.label("Checks scan the cleaned output for leftovers. They never change the result — "
                     "findings appear in a review strip on the Clean page and feed rule suggestions.") \
                .classes("text-xs opacity-60")
            with ui.grid().classes("grid-cols-2 gap-2 w-full"):
                for cid in CHECK_LABELS:
                    c = checks.get(cid, {})
                    ui.switch(CHECK_LABELS[cid], value=bool(c.get("enabled", True)),
                              on_change=lambda e, k=cid: checks.setdefault(k, {}).update(enabled=e.value)) \
                        .tooltip(CHECK_DESCRIPTIONS.get(cid, ""))
            ld = checks.setdefault("long_digits", {})
            ui.number("min digits for long-number check", value=int(ld.get("min_digits", 6)),
                      min=4, max=20, format="%.0f",
                      on_change=lambda e: ld.update(min_digits=int(e.value or 6))).classes("w-72")

            ui.label(CHECK_DESCRIPTIONS["residual_chrome"] +
                     " Extra patterns below (an empty list uses the built-in defaults).") \
                .classes("text-xs opacity-60 mt-1")
            patterns = checks.setdefault("residual_chrome", {}).setdefault("patterns", [])

            def chrome_row(pos: int, pattern: str) -> None:
                with ui.row().classes("w-full items-center gap-2"):
                    p_in = ui.input(value=pattern).props("outlined dense").classes("flex-grow cc-mono")
                    count_lbl = ui.label("").classes("text-xs opacity-70 w-24")

                    def sync() -> None:
                        try:
                            patterns[pos] = p_in.value
                        except IndexError:
                            return
                        n = count_matches(p_in.value, PIPE_TEST["text"], re.IGNORECASE | re.MULTILINE)
                        count_lbl.set_text(f"{n} hits" if n >= 0 else "bad regex")

                    p_in.on_value_change(lambda e: sync())
                    sync()

                    def remove() -> None:
                        if 0 <= pos < len(patterns):
                            del patterns[pos]
                        render_audit_card()

                    ui.button(icon="delete", on_click=remove).props("flat dense round color=grey")

            for i, p in enumerate(list(patterns)):
                if isinstance(p, str):
                    chrome_row(i, p)

            def add_chrome_pattern() -> None:
                patterns.append("")
                render_audit_card()

            ui.button("Add chrome pattern", icon="add", on_click=add_chrome_pattern).props("outline dense")

    def resolve_order() -> list[str]:
        order = draft.get("stage_order")
        if not isinstance(order, list):
            order = []
        known_custom = {f"custom:{c['name']}" for c in customs}
        resolved = [s for s in order if s in STAGE_EDITORS or s in known_custom]
        for s in BUILTIN_STAGE_IDS:
            if s not in resolved:
                resolved.append(s)
        for s in sorted(known_custom):
            if s not in resolved:
                resolved.append(s)
        draft["stage_order"] = resolved
        return resolved

    def stage_enabled(sid: str) -> bool:
        if sid.startswith("custom:"):
            return bool((draft.get("custom_rules") or {}).get(sid.split(":", 1)[1], {}).get("enabled", True))
        if sid == "nlp_redaction":
            return bool((draft.get("nlp_redaction") or {}).get("enabled", True))
        if sid == "tokenize_phi":
            return bool((draft.get("tokenization") or {}).get("enabled", False))
        if sid == "duplicate_notes":
            return bool((draft.get("duplicate_note_detection") or {}).get("enabled", True))
        if sid == "fuzzy_dedup":
            return bool((draft.get("fuzzy_dedup") or {}).get("enabled", True))
        so = (draft.get("stage_options") or {}).get(sid)
        if isinstance(so, dict) and "enabled" in so:
            return bool(so["enabled"])
        return True

    def move_stage(sid: str, delta: int) -> None:
        lst = draft["stage_order"]
        i = lst.index(sid)
        j = i + delta
        if 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]
            refresh_stages()

    def set_stage_enabled(sid: str, flag: bool) -> None:
        if sid.startswith("custom:"):
            draft.setdefault("custom_rules", {}).setdefault(sid.split(":", 1)[1], {})["enabled"] = flag
        elif sid == "nlp_redaction":
            draft.setdefault("nlp_redaction", {})["enabled"] = flag
        elif sid == "tokenize_phi":
            draft.setdefault("tokenization", {})["enabled"] = flag
        elif sid == "duplicate_notes":
            draft.setdefault("duplicate_note_detection", {})["enabled"] = flag
        elif sid == "fuzzy_dedup":
            draft.setdefault("fuzzy_dedup", {})["enabled"] = flag
        else:
            draft.setdefault("stage_options", {}).setdefault(sid, {})["enabled"] = flag
        refresh_stages()

    def refresh_stages() -> None:
        stages_container.clear()
        with stages_container:
            render_stages()

    def render_stages() -> None:
        total = len(draft["stage_order"])
        for idx, sid in enumerate(draft["stage_order"]):
            is_custom = sid.startswith("custom:")
            label = STAGE_LABELS.get(sid) or (sid.split(":", 1)[1] if sid.startswith("custom:") else sid)
            with ui.expansion(f"{idx + 1}. {label}", icon="code" if is_custom else "rule").classes("w-full"):
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.switch("Enabled", value=stage_enabled(sid),
                              on_change=lambda e, s=sid: set_stage_enabled(s, e.value))
                    ui.button(icon="arrow_upward", on_click=lambda s=sid: move_stage(s, -1)) \
                        .props("flat dense round").set_enabled(idx > 0)
                    ui.button(icon="arrow_downward", on_click=lambda s=sid: move_stage(s, +1)) \
                        .props("flat dense round").set_enabled(idx < total - 1)
                    if is_custom:
                        ui.button("Edit script", icon="edit", on_click=lambda: ui.navigate.to("/scripts")).props("flat")
                    else:
                        ui.label(STAGE_DESCRIPTIONS.get(sid, "")).classes("text-xs opacity-60 flex-grow")
                if stage_enabled(sid) and not is_custom:
                    render_stage_editor(sid)

    def list_set(lst: list, pos: int, value) -> None:
        if 0 <= pos < len(lst):
            lst[pos] = value

    def list_del(lst: list, pos: int) -> None:
        if 0 <= pos < len(lst):
            del lst[pos]

    def pattern_row(lst: list, pos: int, pattern: str, replacement: str | None = None,
                    flags: int = re.IGNORECASE, sid: str | None = None) -> None:
        with ui.row().classes("w-full items-center gap-2"):
            p_in = ui.input(value=pattern).props("outlined dense").classes("flex-grow cc-mono")
            r_in = None
            if replacement is not None:
                r_in = ui.input(value=replacement).props("outlined dense label='→ replace with'") \
                    .classes("flex-grow")
            case_sw = None
            if sid:
                case_sw = ui.switch("Aa", value=bool(((draft.get("stage_options") or {})
                                                      .get(sid, {}) or {}).get("case_sensitive", False))) \
                    .tooltip("Case sensitive matching for this stage")
            count_lbl = ui.label("").classes("text-xs opacity-70 w-24")

            def current_flags() -> int:
                if sid and case_sw is not None and case_sw.value:
                    return flags & ~re.IGNORECASE
                return flags

            def sync() -> None:
                try:
                    if replacement is not None:
                        lst[pos] = [p_in.value, r_in.value]
                    else:
                        lst[pos] = p_in.value
                except IndexError:
                    return
                n = count_matches(p_in.value, PIPE_TEST["text"], current_flags())
                count_lbl.set_text(f"{n} hits" if n >= 0 else "bad regex")

            p_in.on_value_change(lambda e: sync())
            if r_in is not None:
                r_in.on_value_change(lambda e: sync())
            if case_sw is not None:
                def set_case(e) -> None:
                    draft.setdefault("stage_options", {}).setdefault(sid, {})["case_sensitive"] = e.value
                    sync()
                case_sw.on_value_change(set_case)
            sync()

            def remove() -> None:
                list_del(lst, pos)
                refresh_stages()

            ui.button(icon="delete", on_click=remove).props("flat dense round color=grey")

    def render_nlp_editor() -> None:
        try:
            import presidio_analyzer  # noqa: F401
            nlp_ok = True
        except Exception:
            nlp_ok = False
        if not nlp_ok:
            ui.label("Presidio / spaCy model is not available in this environment — this stage will be "
                     "skipped. Run install.sh (macOS) or install.bat (Windows) to enable it.") \
                .classes("text-orange-600 text-xs")

        n = draft.setdefault("nlp_redaction", {})
        ents = n.setdefault("entities", {
            "PERSON": "[REDACTED_NAME]",
            "PHONE_NUMBER": "[REDACTED_PHONE]",
            "EMAIL_ADDRESS": "[REDACTED_EMAIL]",
        })
        with ui.grid().classes("grid-cols-2 gap-2 w-full"):
            for etype in list(ents):
                with ui.row().classes("items-center gap-1 flex-nowrap w-full"):
                    ui.switch(etype, value=True,
                              on_change=lambda e, k=etype: (ents.pop(k, None) if not e.value else None,
                                                            refresh_stages()))
                    ui.input(value=ents[etype],
                             on_change=lambda e, k=etype: ents.update({k: e.value})) \
                        .props("outlined dense").classes("flex-grow")
        new_ent = {"v": ""}

        def add_entity() -> None:
            k = (new_ent["v"] or "").strip().upper()
            if k:
                ents.setdefault(k, "[REDACTED]")
                refresh_stages()
            new_ent["v"] = ""

        with ui.row().classes("items-center gap-2"):
            ui.input("Add entity type (e.g. LOCATION, US_SSN)",
                     on_change=lambda e: new_ent.update(v=e.value)) \
                .props("outlined dense").classes("w-80")
            ui.button("Add", on_click=add_entity).props("outline dense")
        ui.number("Minimum confidence (0–1, empty = Presidio default)",
                  value=n.get("score_threshold"), min=0, max=1, step=0.05,
                  on_change=lambda e: n.update(score_threshold=e.value)).classes("w-96")

        allow = draft.setdefault("nlp_allow_list", [])
        ui.label("Allow-list (words NLP must never redact, e.g. drug names that look like people)") \
            .classes("text-xs opacity-70")
        with ui.row().classes("flex-wrap gap-1 items-center"):
            for w in list(allow):
                ui.badge(w).props("outline")
                ui.button(icon="close",
                          on_click=lambda ww=w: (allow.remove(ww) if ww in allow else None,
                                                 refresh_stages())).props("flat dense round")
            add_word = {"v": ""}

            def add_allow() -> None:
                if add_word["v"].strip():
                    allow.append(add_word["v"].strip().lower())
                    refresh_stages()
                add_word["v"] = ""

            w_in = ui.input("add word + Enter", on_change=lambda e: add_word.update(v=e.value)) \
                .props("outlined dense").classes("w-44")
            w_in.on("keydown.enter.prevent", add_allow)

    def render_stage_editor(sid: str) -> None:
        kind, key = STAGE_EDITORS[sid]

        if kind in ("regex_lines", "regex_pairs"):
            lst = draft.setdefault(key, [])
            flags = STAGE_FLAGS.get(sid, re.IGNORECASE)
            is_pairs = kind == "regex_pairs"

            def add_row() -> None:
                lst.append(["", ""] if is_pairs else "")
                refresh_stages()

            for i, item in enumerate(list(lst)):
                if is_pairs and isinstance(item, list) and len(item) == 2:
                    pattern_row(lst, i, pattern=item[0], replacement=item[1], flags=flags, sid=sid)
                elif not is_pairs and isinstance(item, str):
                    pattern_row(lst, i, pattern=item, flags=flags, sid=sid)
                else:
                    ui.label(f"⚠ malformed entry #{i}").classes("text-red-500 text-xs")
            ui.button("Add " + ("pair" if is_pairs else "pattern"), icon="add", on_click=add_row) \
                .props("outline dense")

        elif kind == "tokenize":
            t = draft.setdefault("tokenization", {})
            ui.label("Swaps structured PHI (the patterns below in 'Structured PHI patterns', "
                     "plus identity-label values) for reversible [[T1]]-style codes. Same value "
                     "⇒ same token, so repeated names stay consistent. Value→token maps are saved "
                     "under data/tokens and can restore the text later (Settings → Token maps, or "
                     "clean-chart --untoken).").classes("text-xs opacity-70")
            ui.input("Token prefix", value=str(t.get("prefix") or "T"),
                     on_change=lambda e: t.update(prefix=(e.value or "T").strip())) \
                .props("outlined dense").classes("w-40")
            ui.label("Tokens are issued before redaction runs — while this stage is on, the "
                     "pattern stages will simply find nothing left to replace.").classes("text-xs opacity-60")

        elif kind == "headers":
            lst = draft.setdefault(key, [])
            try:
                from chartcleaner.nlp_medspacy import medspacy_available
                ms_ok = medspacy_available()
            except Exception:
                ms_ok = False
            eng = draft.get("headers_engine", "regex")
            eng_sel = ui.select(options={"regex": "Regex header list", "medspacy": "medspaCy sectionizer"},
                                value=eng if (ms_ok or eng == "regex") else "regex",
                                label="Header detection engine").classes("w-72")
            eng_sel.on_value_change(lambda e: draft.update(headers_engine=e.value))
            if not ms_ok:
                ui.label("medspaCy is not installed — choosing its engine will fall back to this "
                         "regex list. pip install medspacy to enable.").classes("text-xs opacity-60")

            ho = draft.setdefault("header_options", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full mt-2"):
                ui.select(options={1: "H1 (# Header)", 2: "H2 (## Header)", 3: "H3 (### Header)",
                                   4: "H4 (#### Header)", 5: "H5 (##### Header)", 6: "H6 (###### Header)"},
                          value=int(ho.get("level") or 2), label="Heading level",
                          on_change=lambda e: ho.update(level=int(e.value))).classes("w-full")
                ui.switch("Bold style (**Header**)", value=bool(ho.get("bold")),
                          on_change=lambda e: ho.update(bold=e.value))
                ui.switch("Keep trailing colon", value=bool(ho.get("keep_colon")),
                          on_change=lambda e: ho.update(keep_colon=e.value))
                ui.switch("Only ALL-CAPS lines", value=bool(ho.get("uppercase_only")),
                          on_change=lambda e: ho.update(uppercase_only=e.value))

            def add_header() -> None:
                lst.append("New Header")
                refresh_stages()

            for i, h in enumerate(list(lst)):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=h, on_change=lambda e, p=i: list_set(lst, p, e.value)) \
                        .props("outlined dense").classes("flex-grow")
                    ui.button(icon="delete",
                              on_click=lambda p=i: (list_del(lst, p), refresh_stages())) \
                        .props("flat dense round color=grey")
            ui.button("Add header", icon="add", on_click=add_header).props("outline dense")

        elif kind == "whitespace":
            o = draft.setdefault("whitespace", {})
            ui.label("Trailing-space trim, blank-line collapse and final trim are on by default "
                     "(the long-standing behavior); everything else is opt-in.") \
                .classes("text-xs opacity-70")
            with ui.grid().classes("grid-cols-2 gap-2 w-full"):
                for opt, desc in (("crlf_to_lf", "Convert CRLF / CR line endings to LF"),
                                  ("trim_trailing", "Trim trailing spaces/tabs per line"),
                                  ("collapse_blank_lines", "Collapse 3+ blank lines to one blank line"),
                                  ("collapse_spaces", "Collapse runs of 2+ spaces to one space"),
                                  ("strip_leading", "Strip leading indentation on every line"),
                                  ("normalize_tabs", "Convert tabs to four spaces"),
                                  ("final_trim", "Trim blank lines at start/end of the chart")):
                    ui.switch(desc, value=bool(o.get(opt, DEFAULT_WHITESPACE[opt])),
                              on_change=lambda e, k=opt: o.update({k: e.value}))

        elif kind == "bullets":
            o = draft.setdefault("bullets", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full"):
                ui.select(options={"- ": "- (dash)", "• ": "• (bullet)", "* ": "* (asterisk)", "": "(strip marker)"},
                          value=str(o.get("style") if o.get("style") is not None else "- "),
                          label="Replacement marker",
                          on_change=lambda e: o.update(style=e.value)).classes("w-full")
                ui.input("Extra bullet glyphs (e.g. ›,»)",
                         value="".join(o.get("extra_glyphs") or []),
                         on_change=lambda e: o.update(extra_glyphs=list(e.value or ""))) \
                    .props("outlined dense").classes("w-full")
                ui.switch("Normalize numbered lists (1. / 1) → marker)",
                          value=bool(o.get("normalize_numbered")),
                          on_change=lambda e: o.update(normalize_numbered=e.value))
                ui.switch("Drop empty bullet lines", value=bool(o.get("skip_empty")),
                          on_change=lambda e: o.update(skip_empty=e.value))

        elif kind == "unicode":
            o = draft.setdefault("unicode_normalize", {})
            ui.label("All off by default — flip on what your charts need. Runs before the "
                     "regex stages, so patterns can assume plain characters.") \
                .classes("text-xs opacity-70")
            with ui.grid().classes("grid-cols-2 gap-2 w-full"):
                for opt, desc in (("quotes", "Curly quotes → straight (\u201c \u201d \u2019 …)"),
                                  ("dashes", "En/em dashes & minus → hyphen (– — −)"),
                                  ("nbsp", "Non-breaking spaces → normal spaces"),
                                  ("zero_width", "Strip zero-width characters & BOM"),
                                  ("ellipsis", "… → ..."),
                                  ("ligatures", "Ligatures → letters (ﬁ → fi)")):
                    ui.switch(desc, value=bool(o.get(opt)),
                              on_change=lambda e, k=opt: o.update({k: e.value}))

        elif kind == "timestamps":
            o = draft.setdefault("timestamp_removal", {})
            ui.switch("Remove dates/times", value=bool(o.get("enabled")),
                      on_change=lambda e: o.update(enabled=e.value))
            ui.input("Replace with (empty = delete)", value=str(o.get("replacement") or ""),
                     on_change=lambda e: o.update(replacement=e.value)) \
                .props("outlined dense").classes("w-full")
            with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                ui.switch("Include built-in date patterns (ISO, US, 'Mar 5, 2024')",
                          value=bool(o.get("builtin_patterns", True)),
                          on_change=lambda e: o.update(builtin_patterns=e.value))
                ui.switch("Also remove clock times (12:30, 9:45 pm)",
                          value=bool(o.get("remove_clock_times")),
                          on_change=lambda e: o.update(remove_clock_times=e.value))
            ui.label("Extra patterns (one regex per row, run after the built-ins):") \
                .classes("text-xs opacity-70 mt-1")
            pats = o.setdefault("extra_patterns", [])

            for i, p in enumerate(list(pats)):
                if not isinstance(p, str):
                    continue
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=p, on_change=lambda e, k=i: list_set(pats, k, e.value)) \
                        .props("outlined dense").classes("flex-grow cc-mono")
                    ui.button(icon="delete",
                              on_click=lambda k=i: (list_del(pats, k), refresh_stages())) \
                        .props("flat dense round color=grey")

            def add_ts_pattern() -> None:
                pats.append("")
                refresh_stages()

            ui.button("Add pattern", icon="add", on_click=add_ts_pattern).props("outline dense")

        elif kind == "sections":
            o = draft.setdefault("section_filter", {})
            ui.select(options={"off": "Off", "drop": "Drop listed sections",
                               "keep": "Keep only listed sections"},
                      value=str(o.get("mode") or "off"), label="Mode",
                      on_change=lambda e: o.update(mode=e.value)).classes("w-64")
            ui.label("Section header names, one per row:") \
                .classes("text-xs opacity-70 mt-1")
            names = o.setdefault("sections", [])
            for i, h in enumerate(list(names)):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=h, on_change=lambda e, k=i: list_set(names, k, e.value)) \
                        .props("outlined dense").classes("flex-grow")
                    ui.button(icon="delete",
                              on_click=lambda k=i: (list_del(names, k), refresh_stages())) \
                        .props("flat dense round color=grey")

            def add_section() -> None:
                names.append("")
                refresh_stages()

            ui.button("Add section name", icon="add", on_click=add_section).props("outline dense")

            ui.label("Section boundaries: text between one boundary header and the next is one "
                     "section. Leave empty to reuse the Header stage's header list. 'Keep text "
                     "before the first section' preserves the preamble.") \
                .classes("text-xs opacity-70 mt-2")
            ui.switch("Keep text before the first section", value=bool(o.get("keep_preamble", True)),
                      on_change=lambda e: o.update(keep_preamble=e.value))
            bounds = o.setdefault("boundary_headers", [])
            for i, h in enumerate(list(bounds)):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=h, on_change=lambda e, k=i: list_set(bounds, k, e.value)) \
                        .props("outlined dense").classes("flex-grow")
                    ui.button(icon="delete",
                              on_click=lambda k=i: (list_del(bounds, k), refresh_stages())) \
                        .props("flat dense round color=grey")

            def add_boundary() -> None:
                bounds.append("")
                refresh_stages()

            ui.button("Add boundary header", icon="add", on_click=add_boundary).props("outline dense")

        elif kind == "caps":
            o = draft.setdefault("caps_normalize", {})
            ui.select(options={"off": "Off", "sentence": "Rewrite to sentence case"},
                      value=str(o.get("mode") or "off"), label="Mode",
                      on_change=lambda e: o.update(mode=e.value)).classes("w-64")
            ui.number("Minimum line length (shorter ALL-CAPS lines like headings are kept)",
                      value=int(o.get("min_chars") or 40), min=1, format="%.0f",
                      on_change=lambda e: o.update(min_chars=int(e.value or 40))).classes("w-96")
            ui.input("Acronyms to preserve (comma-separated, e.g. MRI, ICU, SAH)",
                     value=", ".join(o.get("preserve_words") or []),
                     on_change=lambda e: o.update(
                         preserve_words=[w.strip() for w in (e.value or "").split(",") if w.strip()])) \
                .props("outlined dense").classes("w-full")

        elif kind == "line_length":
            o = draft.setdefault("line_length", {})
            ui.select(options={"off": "Off", "truncate": "Truncate long lines",
                               "wrap": "Wrap long lines"},
                      value=str(o.get("mode") or "off"), label="Mode",
                      on_change=lambda e: o.update(mode=e.value)).classes("w-64")
            ui.number("Max characters per line", value=int(o.get("max_chars") or 200),
                      min=10, format="%.0f",
                      on_change=lambda e: o.update(max_chars=int(e.value or 200))).classes("w-72")
            ui.input("Truncation marker", value=str(o.get("marker") or "…"),
                     on_change=lambda e: o.update(marker=e.value)).props("outlined dense").classes("w-40")

        elif kind == "dedup_notes":
            d = draft.setdefault("duplicate_note_detection", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full"):
                ui.number("min body chars", value=d.get("min_body_chars", 400), format="%.0f",
                          on_change=lambda e: d.update(min_body_chars=int(e.value or 400)))
                ui.number("similarity threshold %", value=d.get("similarity_threshold", 90),
                          min=50, max=100, format="%.0f",
                          on_change=lambda e: d.update(similarity_threshold=int(e.value or 90)))
            ui.input("split pattern (regex)", value=d.get("split_pattern", ""),
                     on_change=lambda e: d.update(split_pattern=e.value)) \
                .props("outlined dense").classes("w-full cc-mono")

        elif kind == "fuzzy":
            f = draft.setdefault("fuzzy_dedup", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full"):
                ui.number("similarity threshold %", value=f.get("threshold", 95),
                          min=50, max=100, format="%.0f",
                          on_change=lambda e: f.update(threshold=int(e.value or 95)))
                ui.number("min paragraph chars", value=f.get("min_chars", 100), format="%.0f",
                          on_change=lambda e: f.update(min_chars=int(e.value or 100)))

        elif kind == "clinical_identifiers":
            ci = draft.setdefault("clinical_identifiers", {})
            ui.label(
                "Detects verified clinical identifiers using algorithmic checksums (Luhn-24 for NPI, "
                "checksum digit for DEA) and FDA UDI device barcode specifications."
            ).classes("text-xs opacity-70 mb-2")
            with ui.row().classes("gap-4 items-center"):
                ui.switch("Redact NPI numbers", value=bool(ci.get("redact_npi", True)),
                          on_change=lambda e: ci.update(redact_npi=e.value))
                ui.switch("Redact DEA numbers", value=bool(ci.get("redact_dea", True)),
                          on_change=lambda e: ci.update(redact_dea=e.value))
                ui.switch("Redact UDI device codes", value=bool(ci.get("redact_udi", True)),
                          on_change=lambda e: ci.update(redact_udi=e.value))
            ui.input("Custom replacement (leave empty for [REDACTED_<TYPE>])",
                     value=str(ci.get("replacement") or ""),
                     on_change=lambda e: ci.update(replacement=e.value if e.value else None)).classes("w-96")

        elif kind == "nlp":
            render_nlp_editor()

        else:
            ui.label("This stage has no parameters.").classes("text-xs opacity-60")

    # ---- toolbar / io actions --------------------------------------------------

    def save_all() -> None:
        errs, warns = validate_config(draft)
        if errs:
            ui.notify("Cannot save — " + str(len(errs)) + " problem(s). First: " + errs[0], type="negative")
            return
        try:
            save_config_with_backup(draft)
        except Exception as e:
            ui.notify(f"Save failed: {e}", type="negative")
            return
        save_btn.set_text("Save changes")
        msg = "Saved. " + (f"Warnings: {'; '.join(warns[:2])}" if warns else "")
        ui.notify(msg, type="positive")
        ui.navigate.to("/pipeline")

    def restore_defaults() -> None:
        try:
            save_config_with_backup(load_default_config())
            ui.notify("Factory default rules restored.", type="positive")
            ui.navigate.to("/pipeline")
        except Exception as e:
            ui.notify(str(e), type="negative")

    def run_tests() -> None:
        text = PIPE_TEST["text"]
        counts: list[str] = []
        for sid in draft["stage_order"]:
            ed = STAGE_EDITORS.get(sid)
            if not ed:
                continue
            _kind, key = ed
            obj = draft.get(key)
            if sid in ("metadata_lines", "boilerplate") and isinstance(obj, list):
                n = sum(max(0, count_matches(p, text, STAGE_FLAGS[sid])) for p in obj if isinstance(p, str))
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
            elif sid in ("phi_patterns", "literal_replacements") and isinstance(obj, list):
                n = sum(max(0, count_matches(p[0], text, STAGE_FLAGS[sid]))
                        for p in obj if isinstance(p, list) and len(p) == 2)
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
            elif sid == "headers" and isinstance(obj, list):
                n = sum(max(0, count_matches(rf"^\s*({h})\s*:?\s*$", text, re.IGNORECASE | re.MULTILINE))
                        for h in obj if isinstance(h, str))
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
        test_results.set_text("Match counts → " + " · ".join(counts) if counts else "Nothing to test.")

    def export_rules() -> None:
        name = store.save_export(json.dumps(draft, indent=4, ensure_ascii=False), "chart_rules")
        download_file(f"/exports/{name}", "chart_rules.json")
        ui.notify("Rules exported (check your downloads).", type="positive")

    async def import_rules(e) -> None:
        try:
            cfg = json.loads(e.content.read().decode("utf-8"))
        except Exception as ex:
            ui.notify(f"Invalid JSON: {ex}", type="negative")
            return
        errs, _ = validate_config(cfg)
        if errs:
            ui.notify("Imported rules have problems: " + errs[0], type="negative")
            return
        draft.clear()
        draft.update(cfg)
        resolve_order()
        save_all()

    def new_preset_dialog() -> None:
        with ui.dialog() as dlg, ui.card():
            ui.label("Save the current rules as a preset:")
            name_in = ui.input("Preset name", placeholder="e.g. Neuro-ICU strict")
            err = ui.label("").classes("text-red-500 text-xs")

            def do_save() -> None:
                name = (name_in.value or "").strip()
                if not name:
                    err.set_text("Name required.")
                    return
                store.save_preset(name, draft)
                dlg.close()
                ui.notify(f"Preset '{name}' saved.", type="positive")
                ui.navigate.to("/pipeline")

            with ui.row():
                ui.button("Save preset", on_click=do_save).props("unelevated color=primary")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def delete_preset_clicked() -> None:
        name = preset_dd.value
        if not name:
            ui.notify("Pick a preset first.", type="warning")
            return
        store.delete_preset(name)
        ui.notify(f"Preset '{name}' deleted.", type="positive")
        ui.navigate.to("/pipeline")

    def on_preset_load(e) -> None:
        if not e.value:
            return
        try:
            cfg = store.load_preset(e.value)
            perrs, _ = validate_config(cfg)
            if perrs:
                ui.notify("Preset invalid: " + perrs[0], type="negative")
                return
            draft.clear()
            draft.update(cfg)
            resolve_order()
            save_all()
        except Exception as ex:
            ui.notify(str(ex), type="negative")

    def render_packs() -> None:
        packs_holder.clear()
        try:
            packs = rulepacks.list_packs()
        except Exception:
            packs = []
        with packs_holder:
            if not packs:
                ui.label("No packs found.").classes("text-xs opacity-60")
            for p in packs:
                with ui.card().classes("w-full gap-1"):
                    ui.label(f"{p['name']} — {p['rules']} rule entries").classes("font-medium")
                    ui.label(p["description"]).classes("text-xs opacity-70 -mt-2")
                    if p["source"]:
                        ui.label(f"Source: {p['source']}").classes("text-xs opacity-50 -mt-1")

                    def install(name=p["name"]) -> None:
                        _n, msg = rulepacks.install_pack(name)
                        ui.notify(msg, type="positive")

                    def apply(name=p["name"]) -> None:
                        def do_it() -> None:
                            ok, msg = rulepacks.apply_pack(name)
                            ui.notify(msg, type="positive" if ok else "negative")
                            if ok:
                                ui.navigate.to("/pipeline")
                        confirm_dialog(f"Replace the current rules with pack '{name}'? "
                                       "(a config backup is kept)", do_it)

                    with ui.row().classes("gap-2"):
                        ui.button("Install as preset", icon="save_as", on_click=install) \
                            .props("outline dense")
                        ui.button("Apply now", icon="bolt", on_click=apply) \
                            .props("outline dense color=orange")

    # ---- UI ------------------------------------------------------------------
    resolve_order()

    with shell("Pipeline & Rules", "pipeline"):
        ui.label("Every cleaning rule lives here: enable, disable, edit, reorder — plus presets and "
                 "import/export. Changes apply after you save.").classes("opacity-70 -mt-2 text-sm")

        pending_holder = ui.column().classes("w-full")
        with pending_holder:
            render_pending_card()

        suggestions_holder = ui.column().classes("w-full")
        with suggestions_holder:
            render_suggestions()

        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            save_btn = ui.button("Save changes", icon="save", on_click=save_all)
            save_btn.props("unelevated color=primary")
            ui.button("Revert", icon="undo", on_click=lambda: ui.navigate.to("/pipeline")).props("flat")
            ui.button("Test patterns on sample text", icon="science", on_click=run_tests).props("outline")
            ui.separator().props("vertical")
            preset_dd = ui.select(options={p: p for p in store.list_presets()}, value=None,
                                  label="Presets").classes("w-44")
            ui.button(icon="save_as", on_click=new_preset_dialog).props("flat").tooltip("Save current rules as a preset")
            ui.button(icon="delete", on_click=delete_preset_clicked).props("flat").tooltip("Delete selected preset")
            ui.button(icon="file_download", on_click=export_rules).props("flat").tooltip("Export rules as JSON")
            ui.upload(on_upload=import_rules, auto_upload=True) \
                .props("accept=.json,application/json flat").classes("max-w-[210px]")
            ui.button("Factory defaults", icon="restart_alt", on_click=restore_defaults).props("flat")

        with ui.expansion("Test text (used by the per-pattern match counters)", icon="science").classes("w-full"):
            ui.textarea("Sample chart to test patterns against", value=PIPE_TEST["text"],
                        on_change=lambda e: PIPE_TEST.update(text=e.value)) \
                .props("outlined input-style='min-height: 120px'").classes("w-full cc-mono")
            test_results = ui.label("").classes("text-xs opacity-80")

        with ui.expansion("Review checks (post-run audit)", icon="fact_check").classes("w-full"):
            audit_holder = ui.column().classes("w-full gap-2")
            with audit_holder:
                render_audit_card()

        with ui.expansion("Rule packs (curated, read-only rule sets)", icon="inventory_2").classes("w-full"):
            ui.label("Complete rule sets shipped with the app — install one as a preset, or apply it "
                     "directly (your current rules are backed up first).").classes("text-xs opacity-60")
            packs_holder = ui.column().classes("w-full gap-2")
            with packs_holder:
                render_packs()

        stages_container = ui.column().classes("w-full gap-1")

        preset_dd.on_value_change(on_preset_load)
        refresh_stages()


# ===========================================================================
# PAGE: Statistics
# ===========================================================================

@ui.page("/stats")
def stats_page():
    runs = store.load_runs()
    s = store.summarize(runs)

    with shell("Statistics — how much has been cleaned", "stats"):
        if not runs:
            with ui.card().classes("w-full items-center py-10"):
                ui.icon("insights").classes("text-5xl opacity-30")
                ui.label("No runs recorded yet.").classes("text-lg")
                ui.label("Clean a chart on the Clean page — every run is tracked here, "
                         "including per-stage detail.").classes("opacity-60 text-sm")
            return

        cards = ui.row().classes("gap-3 flex-wrap")
        stat_chip(cards, "total runs", f"{s['runs']:,}")
        stat_chip(cards, "characters removed", f"{s['chars_removed']:,}", "green")
        stat_chip(cards, "avg reduction", f"{s['avg_reduction']:+.1f}%")
        stat_chip(cards, "PHI items redacted", f"{s['phi']:,}", "red")
        stat_chip(cards, "words removed", f"{max(0, s['words_before'] - s['words_after']):,}", "indigo")
        stat_chip(cards, "avg time / run", f"{s['avg_duration_ms']:.0f} ms", "blue-grey")

        if s["days"]:
            days = s["days"]
            ui.echart({
                "backgroundColor": "transparent",
                "tooltip": {"trigger": "axis"},
                "legend": {"data": ["Runs", "Characters removed"]},
                "grid": {"left": 64, "right": 28, "top": 44, "bottom": 32},
                "xAxis": {"type": "category", "data": days},
                "yAxis": [
                    {"type": "value", "name": "Runs", "minInterval": 1},
                    {"type": "value", "name": "Chars", "splitLine": {"show": False}},
                ],
                "series": [
                    {"name": "Runs", "type": "bar", "data": [s["by_day"][d]["runs"] for d in days],
                     "itemStyle": {"color": "#5c6bc0"}},
                    {"name": "Characters removed", "type": "line", "yAxisIndex": 1, "smooth": True,
                     "data": [s["by_day"][d]["chars_removed"] for d in days],
                     "areaStyle": {"opacity": 0.15}, "itemStyle": {"color": "#66bb6a"}},
                ],
            }).classes("w-full h-72")

        with ui.row().classes("w-full gap-4 flex-wrap"):
            if s["top_stages"]:
                labels = [k for k, _ in s["top_stages"]]
                vals = [v for _, v in s["top_stages"]]
                ui.echart({
                    "backgroundColor": "transparent",
                    "title": {"text": "Which stages clean the most", "textStyle": {"fontSize": 14}},
                    "tooltip": {},
                    "grid": {"left": 210, "right": 40, "top": 36, "bottom": 24},
                    "xAxis": {"type": "value"},
                    "yAxis": {"type": "category", "data": labels[::-1],
                              "axisLabel": {"width": 190, "overflow": "truncate"}},
                    "series": [{"type": "bar", "data": vals[::-1], "itemStyle": {"color": "#26a69a"}}],
                }).classes("flex-grow min-w-[430px] h-72")
            if s["phi_by_type"]:
                ui.echart({
                    "backgroundColor": "transparent",
                    "title": {"text": "PHI redactions by type", "textStyle": {"fontSize": 14}},
                    "tooltip": {"trigger": "item"},
                    "legend": {"orient": "vertical", "right": 0, "top": "middle", "textStyle": {"fontSize": 11}},
                    "series": [{"type": "pie", "radius": ["35%", "70%"], "center": ["40%", "55%"],
                                "data": [{"name": k, "value": v} for k, v in s["phi_by_type"].items()]}],
                }).classes("flex-grow min-w-[380px] h-72")

        cols = [
            {"name": "ts", "label": "When", "field": "ts", "align": "left", "sortable": True},
            {"name": "source", "label": "Source", "field": "source", "align": "left"},
            {"name": "before", "label": "Chars before", "field": "chars_before", "sortable": True},
            {"name": "after", "label": "Chars after", "field": "chars_after"},
            {"name": "reduction", "label": "Reduction", "field": "reduction", "sortable": True},
            {"name": "ms", "label": "Time (ms)", "field": "duration_ms"},
        ]
        rows = [{
            "ts": r.get("ts", ""), "source": r.get("source", ""), "chars_before": r.get("chars_before", 0),
            "chars_after": r.get("chars_after", 0), "reduction": f"{r.get('reduction', 0):+.1f}%",
            "duration_ms": r.get("duration_ms", 0),
        } for r in reversed(runs[-200:])]
        ui.table(columns=cols, rows=rows, row_key="ts", pagination=10).classes("w-full").props("flat")

        with ui.row().classes("gap-2"):
            ui.button("Refresh", icon="refresh", on_click=lambda: ui.navigate.to("/stats")).props("flat")
            ui.button("Open data folder", icon="folder",
                      on_click=lambda: open_folder(store.DATA_DIR)).props("flat")
            ui.button("Clear history", icon="delete_forever",
                      on_click=lambda: confirm_dialog("Delete the entire cleaning history?", store.clear_runs)) \
                .props("flat color=negative")

        # ---- evaluation: how well does the current rule set actually clean? ----
        eval_state: dict = {"running": False}

        with ui.expansion("Evaluation — recall report card", icon="verified").classes("w-full"):
            ui.label("Generates synthetic charts with known PHI, runs your current rules on them, "
                     "and reports how many PHI items were actually removed. A real exam, not a vibe.") \
                .classes("text-xs opacity-70")
            eval_holder = ui.column().classes("w-full gap-2")
            with eval_holder:
                last = load_last_evaluation()
                if last:
                    ui.label(f"Last run: {last.get('ts', '')} — recall {last.get('recall', 0)}% "
                             f"over {last.get('samples', 0)} sample chart(s).") \
                        .classes("text-sm opacity-80")

            n_sel = ui.number("Charts to generate", value=25, min=5, max=200, format="%.0f").classes("w-44")
            eval_btn = ui.button("Run evaluation", icon="play_arrow")

            async def run_eval() -> None:
                if eval_state["running"]:
                    return
                eval_state["running"] = True
                eval_btn.set_enabled(False)

                def work():
                    cfg = load_config(CONFIG_PATH)
                    samples = generate_benchmark(n=int(n_sel.value or 25))
                    rep = evaluate_samples(cfg, samples, custom_dir=CUSTOM_DIR)
                    save_evaluation(rep)
                    return rep

                try:
                    rep = await run.io_bound(work)
                    eval_holder.clear()
                    with eval_holder:
                        stat_chip_row = ui.row().classes("gap-3 flex-wrap")
                        stat_chip(stat_chip_row, "overall recall", f"{rep['recall']}%",
                                  "green" if rep["recall"] >= 90 else "orange")
                        stat_chip(stat_chip_row, "safety F2-score", f"{rep.get('f2', 0.0)}%", "purple")
                        cp_rate = rep.get("clinical_preservation", {}).get("preservation_rate", 100.0)
                        stat_chip(stat_chip_row, "clinical terms kept", f"{cp_rate}%", "teal")
                        eq_ratio = rep.get("fairness", {}).get("disparate_impact_ratio", 1.0)
                        stat_chip(stat_chip_row, "demographic equity", f"{eq_ratio}x", "blue")
                        stat_chip(stat_chip_row, "PHI items caught",
                                  f"{rep['caught']}/{rep['items']}", "indigo")
                        for t, b in list(rep["by_type"].items())[:9]:
                            stat_chip(stat_chip_row, t, f"{b['recall']}%",
                                      "green" if b["recall"] >= 90 else "red")

                        demog = rep.get("demographics", {})
                        if demog:
                            ui.label("Demographic Fairness & Cohort Breakdown:").classes("text-sm font-semibold mt-2")
                            cohort_row = ui.row().classes("gap-2 flex-wrap")
                            for cname, cstat in sorted(demog.items()):
                                stat_chip(cohort_row, cname.replace("_", " ").title(), f"{cstat.get('recall', 0)}%",
                                          "green" if cstat.get("recall", 0) >= 85 else "orange")

                        if rep["missed"]:
                            ui.label("Missed items (tighten these rules — see the packs on the "
                                     "Pipeline page):").classes("text-sm font-semibold mt-1")
                            for m in rep["missed"][:12]:
                                ui.label(f"• [{m['type']}] {m['value']}  ({m['sample']})") \
                                    .classes("text-xs cc-mono opacity-80")
                        ui.label("Synthetic charts are approximations — a high recall is encouraging, "
                                 "not a guarantee.").classes("text-xs opacity-60")
                    ui.notify("Evaluation complete.", type="positive")
                except Exception as e:
                    report_error("Evaluation failed", e)
                finally:
                    eval_state["running"] = False
                    eval_btn.set_enabled(True)

            eval_btn.on("click", lambda: asyncio.get_running_loop().create_task(run_eval()))


# ===========================================================================
# PAGE: Custom Scripts
# ===========================================================================

@ui.page("/scripts")
def scripts_page():
    sel = {"name": None}
    list_holder: dict = {}
    editor_holder: dict = {}

    def build_list() -> None:
        metas = list_custom_rules(CUSTOM_DIR)
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

    def build_editor() -> None:
        if not sel["name"]:
            ui.label("Select a script, or create a new one.").classes("opacity-60")
            return
        path = CUSTOM_DIR / f"{sel['name']}.py"
        if not path.exists():
            ui.label("File no longer exists.").classes("opacity-60")
            return
        content = {"text": path.read_text(encoding="utf-8")}
        metas = {m["name"]: m for m in list_custom_rules(CUSTOM_DIR)}
        meta = metas.get(sel["name"], {})

        ui.label(f"{sel['name']}.py").classes("font-mono font-semibold")
        if meta.get("description"):
            ui.label(meta["description"]).classes("text-xs opacity-70 -mt-2")
        if meta.get("error"):
            ui.label(f"⚠ Load error: {meta['error']}").classes("text-xs text-red-500")
        if meta.get("placeholder"):
            ui.label("ℹ Placeholder script — skipped by the pipeline until you set PLACEHOLDER = False.") \
                .classes("text-xs opacity-70")

        editor = ui.textarea(value=content["text"], on_change=lambda e: content.update(text=e.value))
        editor.props("outlined input-style='min-height: 400px'").classes("w-full cc-mono")
        status = ui.label("").classes("text-xs")

        def save_script() -> None:
            code = content["text"]
            try:
                compile(code, str(path), "exec")
            except SyntaxError as ex:
                status.set_text(f"✗ Syntax error: line {ex.lineno}: {ex.msg}").classes("text-red-500 text-xs")
                return
            path.write_text(code, encoding="utf-8")
            m2 = {mm["name"]: mm for mm in list_custom_rules(CUSTOM_DIR)}.get(sel["name"], {})
            if m2.get("error"):
                status.set_text(f"✗ Saved, but the script has problems: {m2['error']}") \
                    .classes("text-red-500 text-xs")
            elif m2.get("placeholder"):
                status.set_text("✓ Saved. Still a placeholder (PLACEHOLDER = True).")
            else:
                status.set_text("✓ Saved and loadable.").classes("text-green-600 text-xs")
                ui.notify("Script saved.", type="positive")

        def test_script() -> None:
            def work() -> str:
                try:
                    mod = _load_custom_module(path)
                    clean = getattr(mod, "clean", None)
                    if not callable(clean):
                        return "✗ No clean(text, ctx) function."
                    ctx = CleanContext(load_config(CONFIG_PATH))
                    sample = PIPE_TEST["text"] or ("Patient John Doe, MRN 123456, phone (555) 010-2030.\n\n"
                                                  "Follow-up imaging plan tomorrow morning at nine.")
                    out = clean(sample, ctx)
                    return (f"✓ OK — {len(sample):,} → {len(out):,} chars. "
                            f"counters={ctx.counters} logs={ctx.logs[:3]}\n\nOUTPUT PREVIEW:\n{out[:800]}")
                except Exception as ex:
                    return f"✗ {type(ex).__name__}: {ex}"

            async def go() -> None:
                status.set_text("Testing…")
                msg = await run.io_bound(work)
                status.set_text(msg)

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
                target = CUSTOM_DIR / f"{name}.py"
                if target.exists():
                    err.set_text("A file with that name already exists.")
                    return
                target.write_text(CUSTOM_RULE_TEMPLATE, encoding="utf-8")
                dlg.close()
                ui.notify(f"Created {name}.py — a placeholder until you set PLACEHOLDER = False.",
                          type="positive")
                select_file(name)

            with ui.row():
                ui.button("Create", on_click=create).props("unelevated color=primary")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    with shell("Custom Scripts — rules limited only by Python", "scripts"):
        ui.label("Each .py file in custom_rules/ becomes a pipeline stage you can enable, disable and "
                 "reorder on the Pipeline page. Implement clean(text, ctx) and the chart is yours to "
                 "transform however you like.").classes("opacity-70 -mt-2 text-sm")

        with ui.card().classes("w-full border-orange-300"):
            ui.label("⚠ Scripts run with the full privileges of your user account on this machine "
                     "(that is what makes them limitless). Only add scripts you wrote or reviewed.") \
                .classes("text-xs text-orange-600")

        with ui.row().classes("w-full gap-4 items-stretch"):
            with ui.column().classes("w-64 shrink-0 gap-1 p-2 border rounded"):
                ui.label("Rule files").classes("font-semibold")
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

@ui.page("/settings")
def settings_page():
    def _restore_backup(path: str) -> None:
        ok, msg = store.restore_config_backup(path)
        ui.notify(msg, type="positive" if ok else "negative")
        if ok:
            ui.navigate.to("/settings")

    with shell("Settings", "settings"):
        draft = dict(load_config(CONFIG_PATH))

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
                            load_config(CONFIG_PATH), custom_dir=CUSTOM_DIR)
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
            ui.label(f"Config: {CONFIG_PATH}").classes("text-xs font-mono opacity-70")
            ui.label(f"History: {store.STATS_FILE}").classes("text-xs font-mono opacity-70")
            ui.label(f"Custom scripts: {CUSTOM_DIR}").classes("text-xs font-mono opacity-70")
            ui.label(f"Presets: {store.PRESETS_DIR}").classes("text-xs font-mono opacity-70")
            with ui.row().classes("gap-2 flex-wrap"):
                ui.button("Open data folder", icon="folder",
                          on_click=lambda: open_folder(store.DATA_DIR)).props("flat")
                ui.button("Open custom rules folder", icon="folder_open",
                          on_click=lambda: open_folder(CUSTOM_DIR)).props("flat")
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
                current_rules = store._rule_count(load_config(CONFIG_PATH))
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

        with ui.card().classes("w-full gap-1"):
            ui.label("About").classes("font-semibold")
            ui.markdown(
                f"**Chart Cleaner v{__version__}** — cleans Epic-style EMR exports for safer LLM sharing.\n\n"
                "- Everything runs locally; the app listens only on 127.0.0.1 and makes no network calls.\n"
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


# ---------------------------------------------------------------------------
# offline/privacy: strip any external font CDN links from served HTML
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _strip_external_fonts(request, call_next):
    response = await call_next(request)
    try:
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            body = getattr(response, "body", None)
            if body is not None:
                cleaned = re.sub(rb"<link[^>]*fonts\.g(?:oogleapis|static)\.com[^>]*>\s*", b"", body)
                if cleaned != body:
                    response.body = cleaned
                    response.headers["content-length"] = str(len(cleaned))
    except Exception:
        pass
    return response


app.add_static_files("/exports", str(store.EXPORTS_DIR))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _free_port(start: int) -> int:
    """First port uvicorn can actually bind. A connect probe is not enough:
    a socket may be bound without listening (e.g. an outbound connection's
    local port), which passes connect_ex but fails uvicorn's bind."""
    for p in range(start, start + 50):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_chart_cleaner(port: int) -> bool:
    """True when something that answers like Chart Cleaner listens on this port."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5) as r:
            return "Chart Cleaner" in r.read(8192).decode("utf-8", "ignore")
    except Exception:
        return False


def _warm_nlp_engines() -> None:
    """Kick off a best-effort background warmup of the Presidio/spaCy engines.

    The engines are cached module-level in chartcleaner.stages; pre-building
    them at startup means the first Clean click skips the model-load tax.
    The signature must match what run_nlp() passes so the cache is reused.
    """
    def _work() -> None:
        try:
            if os.environ.get("NICEGUI_USER_SIMULATION"):
                return  # test harness: never pay model-load time
            cfg = load_config(CONFIG_PATH)
            if not bool((cfg.get("nlp_redaction") or {}).get("enabled", True)):
                return
            from chartcleaner.stages import _PRESIDIO_CACHE, DEFAULT_NLP_ENTITIES

            ncfg = cfg.get("nlp_redaction") or {}
            entities = dict(ncfg.get("entities") or DEFAULT_NLP_ENTITIES)
            allow = frozenset(t.lower() for t in (cfg.get("nlp_allow_list") or []))
            _PRESIDIO_CACHE.get_engines(tuple(sorted(entities.items())), allow, ncfg.get("score_threshold"))
        except Exception:
            pass  # warmup is best-effort; the Clean page surfaces real NLP errors

    threading.Thread(target=_work, daemon=True, name="nlp-warmup").start()


app.on_startup(_warm_nlp_engines)


def main():
    if os.environ.get("NICEGUI_USER_SIMULATION"):
        # Test harness (nicegui.testing): pages are registered at import; ui.run
        # is intercepted, but it must still be called so run config is marked.
        ui.run(title="Chart Cleaner")
        return

    if getattr(sys, "frozen", False):
        # macOS .app launches can inject multiprocessing bootstrap args
        # (--keep-parent / resource_tracker -c ...); strip them before argparse.
        clean: list[str] = [sys.argv[0]]
        skip = False
        for a in sys.argv[1:]:
            if skip:
                skip = False
                continue
            if a in ("--keep-parent",):
                continue
            if a == "-B" or a == "-S" or a == "-I":
                continue
            if a == "-c":
                skip = True
                continue
            clean.append(a)
        sys.argv = clean

    ap = argparse.ArgumentParser(description="Chart Cleaner desktop app (local web UI).")
    ap.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1 — keep it local).")
    ap.add_argument("--port", type=int, default=0, help="Port (default: first free port from 8765).")
    ap.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser.")
    ap.add_argument("--reload", action="store_true", help="Dev mode: auto-reload on code changes.")
    args = ap.parse_args()

    requested = args.port or 8765
    port = requested
    if _port_busy(port):
        if _is_chart_cleaner(port):
            url = f"http://127.0.0.1:{port}"
            print(f"Chart Cleaner is already running at {url} — opening it instead of starting a second copy.")
            if not args.no_browser:
                webbrowser.open(url)
            return
        port = _free_port(port + 1)
        print(f"Port {requested} is used by another program — using {port} instead.")

    ui.run(
        host=args.host,
        port=port,
        title="Chart Cleaner",
        reload=args.reload,
        show=not args.no_browser,
        favicon="🩺",
    )


if __name__ == "__main__":
    main()
