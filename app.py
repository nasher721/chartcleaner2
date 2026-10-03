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
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import threading
import traceback
import webbrowser
import zipfile
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
from chartcleaner.abbreviation_editor import save_with_safety as save_abbreviation_with_safety
from chartcleaner.abbreviation_editor import show_issues as show_abbreviation_issues
from chartcleaner.abbreviation_safety import check as abbreviation_check
from chartcleaner.abbreviations import abbreviate
from chartcleaner.abbreviations import expand as expand_abbreviations
from chartcleaner.abbreviations import lookup as lookup_abbreviation
from chartcleaner.abbreviations import meanings as abbreviation_meanings
from chartcleaner.abbreviations import normalize_settings as normalize_abbreviation_settings
from chartcleaner.abbreviations import preview as abbreviation_preview
from chartcleaner.abbreviations import suggest as suggest_abbreviation
from chartcleaner.abbreviations import with_custom as abbreviation_with_custom
from chartcleaner.highlight_rules import SELECTION_HANDLER, make_rule, replace_selection
from chartcleaner import watcher as watcher_mod
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE, PENDING_RULE, PIPE_TEST
from chartcleaner.benchmark import generate as generate_benchmark
from chartcleaner.evaluate import evaluate as evaluate_samples
from chartcleaner.evaluate import load_last_evaluation, save_evaluation
from chartcleaner.local_llm import LocalLlmClient
from chartcleaner.chart_qa import QaTurn, ask_chart
from chartcleaner.batch import run_batch as run_batch_files
from chartcleaner.delta_engine import extract_note_deltas
from chartcleaner import api as local_api
from chartcleaner import rule_examples
from chartcleaner.clipboard_watcher import ClipboardWatcher
from chartcleaner.exporters import save_to_vault, to_docx, to_markdown, to_smartphrase
from chartcleaner.prompt_templates import render as render_prompt
from chartcleaner.prompt_templates import templates as prompt_templates
from chartcleaner.rule_health import report as rule_health_report
from chartcleaner.note_type import LABELS as NOTE_TYPE_LABELS
from chartcleaner.note_type import detect as detect_note_type
from chartcleaner.summarizer import (
    LlmUnavailableError,
    NoModelError,
    merge_llm_config,
    summarize,
)

from chartcleaner.update import UpdateClient
from chartcleaner.release_identity import MANIFEST_URL, MACOS_PUBLISHER, WINDOWS_PUBLISHER

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
SERVER_PORT: int | None = None

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
    ("/batch", "layers", "Batch"),
    ("/rules", "highlight", "My text rules"),
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


def _update_client():
    if not MANIFEST_URL:
        return None
    publisher = MACOS_PUBLISHER if sys.platform == "darwin" else WINDOWS_PUBLISHER
    return UpdateClient(str(__version__), MANIFEST_URL,
                       trusted_publisher_identity=publisher)


async def _check_for_updates(*, automatic: bool, force: bool = False, status_label=None):
    """Run metadata-only update checking off the NiceGUI event loop."""
    if not getattr(sys, "frozen", False):
        status = {"state": "source", "code": "source_mode"}
        PREFS.update(update_status="Source checkout — updates are available in installed builds",
                     update_status_code=status["code"])
        save_prefs()
        if status_label:
            status_label.set_text(PREFS["update_status"])
        return None, status
    client = _update_client()
    if client is None:
        status = {"state": "unavailable", "code": "update_unavailable"}
        PREFS.update(update_status="Update checks unavailable", update_status_code=status["code"])
        save_prefs()
        if status_label:
            status_label.set_text(PREFS["update_status"])
        return None, status
    previous_check = PREFS.get("update_last_checked")
    manifest, status = await run.io_bound(
        lambda: client.check(automatic=automatic,
                             last_checked=previous_check if automatic else None,
                             force=force))
    if status.state == "throttled":
        return manifest, status
    PREFS["update_last_checked"] = status.checked_at or time.time()
    text = {
        "current": "Up to date",
        "update_available": f"Update available: v{status.version}",
        "error": f"Check failed ({status.code}); retry manually",
    }.get(status.state, "Update status unavailable")
    PREFS.update(update_status=text, update_status_code=status.code)
    save_prefs()
    if status_label:
        status_label.set_text(text)
    return manifest, status


async def _automatic_update_check() -> None:
    if PREFS.get("update_auto_check", True):
        await _check_for_updates(automatic=True)


async def _after_server_ready() -> None:
    """Wait for the loopback route before acknowledging a companion handoff."""
    for _ in range(300):
        if SERVER_PORT and await run.io_bound(_is_chart_cleaner, SERVER_PORT):
            if getattr(sys, "frozen", False):
                from chartcleaner.updater import write_health_marker
                write_health_marker(str(__version__))
            asyncio.create_task(_automatic_update_check())
            return
        await asyncio.sleep(0.1)


async def _update_startup() -> None:
    asyncio.create_task(_after_server_ready())


async def _stage_and_handoff(manifest, status, notify) -> None:
    if manifest is None or status.state != "update_available":
        notify("No verified update is ready.", type="warning")
        return
    if not getattr(sys, "frozen", False):
        notify("Updates are unavailable in source mode.", type="warning")
        return
    archive = None
    try:
        from chartcleaner import updater
        install = updater.default_install_path()
        staging = updater.staging_root()
        client = _update_client()
        archive, _extracted = await run.io_bound(
            lambda: client.stage(manifest.platforms[status.platform], staging,
                                 rollback_size=updater.installation_size(install)))
        await run.io_bound(updater.handoff_update, os.getpid(), install, archive,
                           str(manifest.version))
    except Exception:
        if archive is not None and archive.parent.parent == staging and archive.parent.name.startswith("update-"):
            await run.io_bound(shutil.rmtree, archive.parent, True)
        notify("Update could not be staged; retry from Settings.", type="negative")
        return
    notify("Update verified. Restarting Chart Cleaner…", type="positive")
    await asyncio.sleep(0.2)
    os._exit(0)


def esc(s: str) -> str:
    return html_mod.escape(s)


def remember_rule_example(pair: list[str], text: str, cfg: dict) -> None:
    """Keep what a new learned rule was taught to do (see Settings → Check my learned rules)."""
    try:
        rule_examples.record(pair, text, cfg)
    except Exception:
        pass  # an example is a safety net; never block saving the rule itself


def save_config_with_backup(cfg: dict) -> None:
    """Validate, keep the previous config, then atomically save changes."""
    errors, _ = validate_config(cfg)
    if errors:
        raise ConfigError(errors[0])
    try:
        store.rotate_config_backup()
    except Exception:
        pass  # backups must never block a save
    save_config(cfg, CONFIG_PATH)


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
# learned rules (highlight text on the Clean page → remembered rule)
# ---------------------------------------------------------------------------

LEARN_REMOVE_TEXT = "remove_text"
LEARN_REMOVE_LINES = "remove_lines"
LEARN_REPLACE = "replace"


def learned_pattern(text: str, mode: str) -> str:
    """Turn highlighted text into a regex for the learned_rules stage.

    remove_text: literal match anywhere (multi-line safe).
    remove_lines: only match when whole line(s) equal the text, and swallow
    the line break so no blank line is left behind.
    replace: literal match anywhere, paired with a replacement string.
    """
    text = (text or "").replace("\r\n", "\n").strip("\n")
    parts = [re.escape(line) for line in text.split("\n")]
    if mode == LEARN_REMOVE_LINES:
        return ("(?im)^[ \\t]*"
                + r"[ \t]*\r?\n[ \t]*".join(parts)
                + r"[ \t]*\n?")
    return r"\n".join(parts)


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


async def read_upload(event) -> tuple[str, bytes]:
    """Read uploads on both supported NiceGUI event formats."""
    if hasattr(event, "file"):
        return event.file.name, await event.file.read()
    return event.name, event.content.read()


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
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round aria-label='Toggle menu'")
            ui.icon("health_and_safety").classes("text-2xl")
            ui.label("Chart Cleaner").classes("text-xl font-bold cursor-pointer").on("click", lambda: ui.navigate.to("/"))
            ui.badge(f"v{__version__}", color="blue-grey").props("outline")
        ui.switch("Dark", value=dark.value,
                  on_change=lambda e: (dark.set_value(e.value), PREFS.update(dark=e.value), save_prefs()))

    with ui.left_drawer(fixed=True, value=None).classes("bg-grey-1 dark:bg-grey-10") as drawer:
        ui.label("Menu").classes("text-xs uppercase opacity-60 ml-2")
        for path, icon, label in NAV:
            btn = ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p))
            btn.props("flat align=left no-caps").classes("w-full")
            if path == ("/" if active == "clean" else "/" + active.strip("/")):
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
                cfg = load_config(CONFIG_PATH)
                if mode != "clean":
                    return Pipeline(cfg, mode=mode).run(text, track_changes=True), None
                found = detect_note_type(text, cfg)
                note_info.update(label=found.label, preset=None)
                preset = (PREFS.get("note_presets") or {}).get(found.note_type or "")
                if PREFS.get("note_auto_apply") and preset and preset in store.list_presets():
                    # This run only: applying a preset for real would overwrite config.json.
                    cfg = store.load_preset(preset)
                    note_info["preset"] = preset
                # Tracked changes stay in memory for the inspect tabs; history drops them.
                result = Pipeline(cfg, custom_dir=CUSTOM_DIR).run(text, track_changes=True)
                return result, run_audit(result.text, cfg)

            def delta_work(cleaned: str):
                try:
                    return extract_note_deltas(cleaned)
                except Exception:
                    return None  # the delta view is a bonus; never fail a clean over it

            result, audit = await run.io_bound(work)
            delta = await run.io_bound(delta_work, result.text) if mode == "clean" else None
            CLEAN_STATE.update(input=text, result_text=result.text, result=result, audit=audit,
                               summary=None, qa=[], result_mode=mode, delta=delta, note=note_info)
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
                delta = CLEAN_STATE.get("delta")
                t_delta = (ui.tab("Changes over time")
                           if delta is not None and delta.notes_found > 1 else None)
            with ui.tab_panels(tabs, value=t_result).classes("w-full"):
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
                        if PREFS.get("notes_folder"):
                            ui.button("Save to notes folder", icon="note_add",
                                      on_click=save_to_notes_folder).props("flat")
                        ui.button("Copy result + stats", icon="data_object",
                                  on_click=lambda: copy_to_clipboard(
                                      result.text + "\n\n<!-- " + result.summary() + " -->")).props("flat")
                        with ui.dropdown_button("Copy as prompt", icon="smart_toy", auto_close=True) \
                                .props("flat"):
                            for tmpl in prompt_templates(load_config(CONFIG_PATH)):
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
                    opts = merge_llm_config(load_config(CONFIG_PATH))
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
                return summarize(chart_text, load_config(CONFIG_PATH))

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
                                 load_config(CONFIG_PATH), history=history)

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

    def on_key(e) -> None:
        try:
            mods = set(e.modifiers or [])
            if e.key == "Enter" and mods & {"Control", "Meta"}:
                asyncio.get_running_loop().create_task(run_clean())
        except Exception:
            pass

    async def auto_tick() -> None:
        if (not PREFS.get("auto_clean") or state["running"]
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
            cfg = load_config(CONFIG_PATH)
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
            cfg = load_config(CONFIG_PATH)
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
        cfg = load_config(CONFIG_PATH)
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
        cfg = load_config(CONFIG_PATH)
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

                save_abbreviation_with_safety(term, abbr, load_config(CONFIG_PATH), persist)

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
            cfg = load_config(CONFIG_PATH)
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
                    cfg = load_config(CONFIG_PATH)
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
        cfg = load_config(CONFIG_PATH)
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
            path = save_to_vault(chart_markdown(), PREFS["notes_folder"])
        except Exception as ex:
            ui.notify(f"Could not save: {ex}", type="negative")
            return
        ui.notify(f"Saved {path.name} to your notes folder.", type="positive")

    def copy_prompt(name: str) -> None:
        try:
            text = render_prompt(name, CLEAN_STATE.get("result_text") or "", load_config(CONFIG_PATH))
        except Exception as ex:
            ui.notify(f"Could not build the prompt: {ex}", type="negative")
            return
        copy_to_clipboard(text, f"“{name}” prompt copied — paste it into your AI tool")

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
        cfg = load_config(CONFIG_PATH)
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
        cfg = load_config(CONFIG_PATH)
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
                    cfg = abbreviation_with_custom(load_config(CONFIG_PATH), group["term"], value,
                                                   acknowledged=acknowledged)
                    save_config_with_backup(cfg)
                    dlg.close()
                    await run_clean()

                save_abbreviation_with_safety(
                    group["term"], value, load_config(CONFIG_PATH),
                    lambda ack: asyncio.get_running_loop().create_task(persist(ack)))

            with ui.row():
                ui.button("Save & clean again", on_click=save).props("unelevated")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def open_meanings_dialog(ambiguous: dict[str, int]) -> None:
        keep = "(leave as written)"
        cfg = load_config(CONFIG_PATH)
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
                cfg = load_config(CONFIG_PATH)
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
        errs, _warns = validate_config(load_config(CONFIG_PATH))
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
            value=PREFS.get("last_preset") if PREFS.get("last_preset") in presets else "",
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
            ui.switch("Auto-clean as I type", value=bool(PREFS.get("auto_clean")),
                      on_change=lambda e: (PREFS.update(auto_clean=e.value), save_prefs()))
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

@ui.page("/batch")
def batch_page():
    """Clean many charts at once — upload files or point at a folder."""
    state = {"running": False}
    pending: list[Path] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="cc_batch_"))
    out_ref: dict = {"dir": None, "zip": None}

    # ---- handlers (defined before the UI that references them) -------------
    def set_running(flag: bool) -> None:
        state["running"] = flag
        run_btn.set_enabled(not flag)
        spinner.set_visibility(flag)

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

    async def run_now() -> None:
        if state["running"] or not pending:
            return
        set_running(True)
        try:
            cfg = load_config(CONFIG_PATH)
            files = list(pending)[:500]

            def work():
                return run_batch_files(files, cfg, custom_dir=CUSTOM_DIR, delta=bool(delta_switch.value))

            results = await run.io_bound(work)
            out_dir = store.EXPORTS_DIR / f"batch_{time.strftime('%Y%m%d_%H%M%S')}"
            out_dir.mkdir(parents=True, exist_ok=True)
            ok = [r for r in results if r.status == "ok"]
            for r in ok:
                (out_dir / f"{Path(r.name).stem}_cleaned.txt").write_text(
                    r.cleaned, encoding="utf-8")
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
                    zf.writestr(f"{Path(r.name).stem}_cleaned.txt", r.cleaned)
            out_ref.update(dir=out_dir, zip=zip_name)

            results_col.clear()
            with results_col:
                cols = [
                    {"name": "file", "label": "File", "field": "file", "align": "left"},
                    {"name": "chars", "label": "Characters", "field": "chars", "align": "left"},
                    {"name": "reduction", "label": "Reduction", "field": "reduction"},
                    {"name": "phi", "label": "PHI redacted", "field": "phi"},
                    {"name": "findings", "label": "Audit findings", "field": "findings"},
                    {"name": "facts", "label": "Clinical facts", "field": "facts", "align": "left"},
                    {"name": "ms", "label": "Elapsed", "field": "ms", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                ]
                rows = []
                for r in results:
                    if r.status == "ok":
                        rows.append({"file": r.name,
                                     "chars": f"{r.chars_before:,} → {r.chars_after:,}",
                                     "reduction": f"{r.reduction:+.1f}%",
                                     "phi": r.phi_total, "findings": r.findings,
                                     "facts": ("✓ kept" if r.facts_status == "ok" else
                                               ("⚠ " if r.facts_status == "review" else "✕ ")
                                               + r.facts) if r.facts_status else "—",
                                     "ms": f"{r.elapsed_ms:,} ms", "status": "ok"})
                    else:
                        rows.append({"file": r.name, "chars": "—", "reduction": "—",
                                     "phi": "—", "findings": "—", "facts": "—",
                                     "ms": f"{r.elapsed_ms:,} ms",
                                     "status": f"error: {r.error}"})
                ui.table(columns=cols, rows=rows, row_key="file",
                         pagination=20).classes("w-full").props("flat dense")
                if ok:
                    with ui.row().classes("gap-2 flex-wrap items-center"):
                        ui.button("Download all (.zip)", icon="archive",
                                  on_click=download_zip).props("unelevated color=primary")
                        ui.button("Open exports folder", icon="folder",
                                  on_click=lambda: open_folder(out_dir)).props("flat")
                    for r in ok:
                        out_name = f"{Path(r.name).stem}_cleaned.txt"
                        with ui.row().classes("w-full items-center gap-2"):
                            ui.icon("description").classes("opacity-60")
                            ui.label(out_name).classes("text-xs cc-mono flex-grow")
                            ui.button("Download .txt", icon="download",
                                      on_click=lambda _, n=out_name: download_file(
                                          f"/exports/{out_dir.name}/{n}", n)
                                      ).props("flat dense")
                failed = [r for r in results if r.status == "error"]
                if failed:
                    ui.label(f"{len(failed)} file(s) failed: "
                             + "; ".join(f"{r.name} — {r.error}" for r in failed[:5])) \
                        .classes("text-xs text-red-600")
                ui.label("Cleaned copies also live in data/exports; run history is on the "
                         "Statistics page.").classes("text-xs opacity-60")
            ui.notify(f"Processed {len(results)} file(s) — {len(ok)} ok.",
                      type="positive")
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
            spinner = ui.spinner("dots", size="lg")
            spinner.set_visibility(False)
        results_col = ui.column().classes("w-full gap-3")


# ===========================================================================
# PAGE: Pipeline & Rules
# ===========================================================================

def _abbreviation_preview_text() -> str:
    """Chart used for abbreviation previews: the Clean page input, else the sample."""
    text = CLEAN_STATE.get("input") or ""
    if text.strip():
        return text
    try:
        return (BASE_DIR / "sample_chart.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


@ui.page("/rules")
def text_rules_page():
    from chartcleaner.abbreviation_editor import render as render_abbreviations
    from chartcleaner.learned_editor import render_learned_editor
    from chartcleaner.rule_sharing import render_rule_sharing

    def load_current() -> dict:
        return load_config(CONFIG_PATH)

    with shell("My text rules", "/rules"):
        ui.label("Manage what you remove, replace and abbreviate. Changes save immediately.") \
            .classes("text-sm opacity-70")
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

        tabs.on_value_change(lambda e: render_tab(e.value))
        render_tab("Remove & replace")
        ui.button("Full settings backup", icon="backup",
                  on_click=lambda: ui.navigate.to("/settings")).props("flat")

STAGE_EDITORS = {
    "metadata_lines": ("regex_lines", "emr_line_metadata"),
    "boilerplate": ("regex_lines", "boilerplate"),
    "phi_patterns": ("regex_pairs", "epic_phi_patterns"),
    "clinical_identifiers": ("clinical_identifiers", "clinical_identifiers"),
    "literal_replacements": ("regex_pairs", "literal_replacements"),
    "medical_abbreviations": ("abbreviations", None),
    "learned_rules": ("regex_pairs", "learned_rules"),
    "headers": ("headers", "clinical_headers"),
    "duplicate_notes": ("dedup_notes", "duplicate_note_detection"),
    "fuzzy_dedup": ("fuzzy", "fuzzy_dedup"),
    "nlp_redaction": ("nlp", None),
    "tokenize_phi": ("tokenize", "tokenization"),
    "unicode_normalize": ("unicode", "unicode_normalize"),
    "timestamps": ("timestamps", "timestamp_removal"),
    "hospital_day": ("form", "hospital_day"),
    "imaging_impression": ("form", "imaging_impression"),
    "lab_compaction": ("form", "lab_compaction"),
    "med_normalize": ("form", "med_normalize"),
    "vitals_summary": ("form", "vitals_summary"),
    "sections": ("sections", "section_filter"),
    "whitespace": ("whitespace", "whitespace"),
    "caps_normalize": ("caps", "caps_normalize"),
    "bullets": ("bullets", "bullets"),
    "line_length": ("line_length", "line_length"),
}

# Stages whose deletions the Clean page lists under "Removed" for review.
REVIEWABLE_REMOVAL_STAGES = ("metadata_lines", "boilerplate", "learned_rules")
# Regex stages that honor stage_options.<sid>.exceptions ("Never remove this").
EXCEPTION_STAGES = ("metadata_lines", "boilerplate", "learned_rules", "literal_replacements")

# Declarative option forms for the opt-in condensing stages:
# (option, label, control, choices) — control is switch | text | select | multi | dates.
STAGE_FORMS: dict[str, list[tuple]] = {
    "hospital_day": [
        ("enabled", "Label dates with hospital day (HD#) and post-op day (POD#)", "switch", None),
        ("admit_date", 'Admission date ("auto" finds "Admission Date:" in the chart, or YYYY-MM-DD)',
         "text", None),
        ("surgery_dates", "Surgery dates for POD# (YYYY-MM-DD, comma-separated)", "dates", None),
        ("style", "Style", "select", {"append": "Keep the date: 10/02/2026 (HD#3)",
                                      "replace": "Replace the date: HD#3"}),
    ],
    "lab_compaction": [
        ("enabled", "Condense lab tables into one line per panel", "switch", None),
        ("style", "Style", "select", {"line": "One line per panel (BMP: Na 138, K 4.1, …)",
                                      "fishbone": "Fishbone diagrams for BMP and CBC"}),
        ("latest_only", "Show only the latest value from multi-day grids", "switch", None),
        ("keep_reference_ranges", "Keep reference ranges [135 - 145]", "switch", None),
    ],
    "med_normalize": [
        ("enabled", "Clean up medication lists (drug, dose, route, frequency)", "switch", None),
    ],
    "vitals_summary": [
        ("enabled", "Summarize vitals flowsheets and intake/output", "switch", None),
    ],
    "imaging_impression": [
        ("enabled", "Keep only the Impression of radiology reports", "switch", None),
        ("keep", "Also keep", "multi", {"findings": "Findings", "indication": "Indication / history",
                                        "technique": "Technique", "comparison": "Comparison"}),
    ],
}

STAGE_DESCRIPTIONS = {
    "metadata_lines": "Whole lines to delete (author/pager/version lines, Epic chrome). Case-insensitive regex per line.",
    "boilerplate": "Blocks to delete anywhere (disclaimers, empty SmartSections). Dot matches newlines.",
    "tokenize_phi": "Reversible tokenization: swaps structured PHI for [[T1]]-style codes and saves the value→token map (Settings → Token maps). Runs before redaction, so enable it instead of — not on top of — the PHI patterns you want tokenized.",
    "phi_patterns": "Regex → replacement pairs for structured PHI (MRN, DOB, phone lines).",
    "clinical_identifiers": "Off by default. Algorithmically recognizes and redacts verified National Provider IDs (NPI via Luhn checksum), DEA numbers (checksum verified), and UDI medical device barcodes.",
    "nlp_redaction": "Presidio NLP redaction: entity types, replacements, confidence threshold and allow-list.",
    "literal_replacements": "Regex → replacement pairs for abbreviations and text fixes.",
    "medical_abbreviations": "Shortens full medical terms using the bundled CSV dictionary. "
                             "Matches whole terms, longest phrases first, without cascading replacements.",
    "learned_rules": "Rules you taught the app by highlighting text on the Clean page (remove text, remove whole lines, or replace). Each row is [regex, replacement]; an empty replacement removes the match. New rules can also be added here by hand.",
    "unicode_normalize": "Off by default. Turn any of these on to replace curly quotes, en/em dashes, non-breaking spaces, zero-width characters, ellipses and ligatures with plain equivalents — great before LLM use.",
    "hospital_day": "Off by default. Adds hospital day (HD#1 = admission day) and post-op day (POD#0 = surgery day) next to each date, so timelines read at a glance. Runs before timestamp removal.",
    "lab_compaction": "Off by default. Turns lab result tables and Recent Labs grids into one line per panel (BMP, CBC, LFT, Coags) with H/L flags, or fishbone diagrams. A table is only rewritten when every line is a recognized lab; anything else stays as written.",
    "med_normalize": "Off by default. Inside medication sections, reduces each line to drug, dose, route and frequency: drops brand names, dispense/refill counts, dates and provider names, and marks held or discontinued meds. Never produces Do Not Use abbreviations.",
    "vitals_summary": "Off by default. Turns vitals flowsheets (several readings per row) into one line of ranges and last values, and Intake/Output totals into one line. Single-reading vitals lines are left alone.",
    "imaging_impression": "Off by default. In radiology reports (FINDINGS followed by IMPRESSION), removes findings, technique, comparison and indication, keeping the title and the impression. Reports without an impression are left alone.",
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
    "learned_rules": re.IGNORECASE,
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
                             "literal_replacements": "Literal replacements",
                             "learned_rules": "Learned rules (your highlight-taught rules)"},
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
                           "learned_rules": "learned_rules",
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
        resolved = [s.id for s in Pipeline(draft, custom_dir=CUSTOM_DIR).stages]
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

    def render_form(key: str) -> None:
        o = draft.setdefault(key, {})
        for opt, label, control, choices in STAGE_FORMS[key]:
            if control == "switch":
                ui.switch(label, value=bool(o.get(opt)),
                          on_change=lambda e, k=opt: o.update({k: e.value}))
            elif control == "text":
                ui.input(label, value=str(o.get(opt, "auto") or ""),
                         on_change=lambda e, k=opt: o.update({k: e.value.strip()})) \
                    .props("outlined dense").classes("w-full")
            elif control == "dates":
                ui.input(label, value=", ".join(o.get(opt) or []),
                         on_change=lambda e, k=opt: o.update(
                             {k: [d.strip() for d in e.value.split(",") if d.strip()]})) \
                    .props("outlined dense").classes("w-full")
            elif control == "select":
                ui.select(choices, label=label, value=o.get(opt) or next(iter(choices)),
                          on_change=lambda e, k=opt: o.update({k: e.value})).classes("w-full")
            elif control == "multi":
                ui.select(choices, label=label, multiple=True, value=list(o.get(opt) or []),
                          on_change=lambda e, k=opt: o.update({k: list(e.value or [])})) \
                    .props("use-chips").classes("w-full")

    def render_stage_editor(sid: str) -> None:
        kind, key = STAGE_EDITORS[sid]

        if kind == "form":
            render_form(key)
            return

        if kind == "abbreviations":
            ui.label("Your abbreviation dictionary is also used by Abbreviations only on the Clean page. "
                     "Add, edit or disable terms in My text rules.") \
                .classes("text-sm opacity-70")
            ui.button("Edit abbreviations", icon="edit",
                      on_click=lambda: ui.navigate.to("/rules")).props("flat")

        elif kind in ("regex_lines", "regex_pairs"):
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
            elif sid in ("phi_patterns", "literal_replacements", "learned_rules") and isinstance(obj, list):
                n = sum(max(0, count_matches(p[0], text, STAGE_FLAGS[sid]))
                        for p in obj if isinstance(p, list) and len(p) == 2)
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
            elif sid == "headers" and isinstance(obj, list):
                n = sum(max(0, count_matches(rf"^\s*({h})\s*:?\s*$", text, re.IGNORECASE | re.MULTILINE))
                        for h in obj if isinstance(h, str))
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
            elif sid == "medical_abbreviations":
                _, n, _ = abbreviate(text, draft)
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
        test_results.set_text("Match counts → " + " · ".join(counts) if counts else "Nothing to test.")

    def export_rules() -> None:
        name = store.save_export(json.dumps(draft, indent=4, ensure_ascii=False), "chart_rules")
        download_file(f"/exports/{name}", "chart_rules.json")
        ui.notify("Rules exported (check your downloads).", type="positive")

    async def import_rules(e) -> None:
        try:
            _, data = await read_upload(e)
            cfg = json.loads(data.decode("utf-8-sig"))
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

def render_rule_health(runs: list[dict]) -> None:
    """Statistics → Rule health: rules that never match or touch too much."""
    try:
        rows = rule_health_report(load_config(CONFIG_PATH), runs)
    except Exception:
        return
    flagged = [r for r in rows if r["status"] in ("never matched", "very broad")]
    title = (f"Rule health — {len(flagged)} rule(s) to review" if flagged
             else "Rule health — no problems found")
    with ui.expansion(title, icon="health_and_safety").classes("w-full"):
        ui.label("From your recent runs: rules that never match are probably dead weight; rules "
                 "that touch over 30% of a chart's lines may be removing real content.") \
            .classes("text-xs opacity-70")
        holder = ui.column().classes("w-full gap-1")

        def remove(row: dict) -> None:
            cfg = load_config(CONFIG_PATH)
            entries = cfg.get(row["key"]) or []
            current = [e[0] if isinstance(e, list) else e for e in entries]
            if row["pattern"] not in current:
                ui.notify("That rule changed since this report; refresh the page.", type="warning")
                return
            entries.pop(current.index(row["pattern"]))
            save_config_with_backup(cfg)
            ui.notify("Rule removed (a backup of the previous rules was kept).", type="positive")
            draw()

        def draw() -> None:
            holder.clear()
            current = rule_health_report(load_config(CONFIG_PATH), runs)
            with holder:
                for row in [r for r in current if r["status"] != "not enough runs yet"][:60]:
                    with ui.row().classes("w-full items-center gap-2 no-wrap border-b pb-1"):
                        color = {"very broad": "orange", "never matched": "grey"}.get(row["status"], "green")
                        ui.badge(row["status"], color=color)
                        ui.label(STAGE_LABELS.get(row["stage"], row["stage"])).classes("text-xs w-40")
                        ui.label(row["pattern"][:90]).classes("text-xs cc-mono flex-grow break-all")
                        ui.label(f"{row['hits']} hits / {row['runs']} runs").classes("text-xs w-32")
                        if row["status"] == "never matched":
                            ui.button("Remove", icon="delete",
                                      on_click=lambda r=row: confirm_dialog(
                                          f"Remove this {STAGE_LABELS.get(r['stage'], r['stage'])} rule?",
                                          lambda: remove(r))).props("flat dense color=negative")
                if not current or all(r["status"] == "not enough runs yet" for r in current):
                    ui.label("Not enough runs yet — clean a few more charts.").classes("text-sm")

        draw()


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

        render_rule_health(runs)

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

        with ui.card().classes("w-full gap-2"):
            ui.label("Updates").classes("font-semibold")
            ui.label(f"Current version: v{__version__}").classes("text-sm")
            update_status = ui.label(str(PREFS.get("update_status", "Not checked"))).classes("text-sm opacity-70")
            last_checked = PREFS.get("update_last_checked")
            ui.label("Last check: " + (time.strftime("%Y-%m-%d %H:%M", time.localtime(last_checked))
                                       if last_checked else "never")).classes("text-xs opacity-60")

            def toggle_auto(e) -> None:
                PREFS["update_auto_check"] = bool(e.value)
                save_prefs()

            ui.switch("Check for updates automatically (at most once every 24 hours)",
                      value=bool(PREFS.get("update_auto_check", True)), on_change=toggle_auto)

            async def manual_check() -> None:
                check_btn.set_enabled(False)
                try:
                    manifest, status = await _check_for_updates(
                        automatic=False, force=True, status_label=update_status)
                    if manifest is not None and status.state == "update_available":
                        confirm_dialog(
                            f"Download and install Chart Cleaner v{manifest.version}?",
                            lambda: asyncio.create_task(_stage_and_handoff(
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
            port = SERVER_PORT or 8765
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
            cw_prefs = dict(PREFS.get("clipboard_watcher") or {"enabled": False, "action": "auto"})
            cw_status = ui.label("").classes("text-sm")

            def cw_save() -> None:
                PREFS["clipboard_watcher"] = dict(cw_prefs)
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
            ui.input("Folder path", value=str(PREFS.get("notes_folder") or ""),
                     placeholder="~/Documents/Obsidian/Charts",
                     on_change=lambda e: (PREFS.update(notes_folder=e.value.strip()), save_prefs())) \
                .classes("w-full cc-mono")

        # ---- prompt templates ---------------------------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Prompt templates").classes("font-semibold")
            ui.label("Used by “Copy as prompt” on the Clean page (and --prompt on the command line). "
                     "Placeholders: {chart} the cleaned chart, {delta} what changed since the last "
                     "note, {date} today. A template with a built-in's name replaces it.") \
                .classes("text-xs opacity-60 -mt-1")
            tmpl_box = ui.column().classes("w-full gap-2")

            def save_templates(items: list[dict]) -> None:
                cfg = load_config(CONFIG_PATH)
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
                mine = list(load_config(CONFIG_PATH).get("prompt_templates") or [])
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

        # ---- note types → presets ---------------------------------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Note types").classes("font-semibold")
            ui.label("The Clean page recognizes the kind of note (discharge summary, H&P, progress, "
                     "consult, nursing, operative, radiology). Pick a preset for a type and turn on "
                     "auto-apply to clean that type with it. Only that clean uses the preset; your "
                     "saved rules are not changed.").classes("text-xs opacity-60 -mt-1")
            preset_names = {"": "(current rules)", **{n: n for n in store.list_presets()}}
            mapping = dict(PREFS.get("note_presets") or {})

            def set_mapping(kind: str, value: str) -> None:
                if value:
                    mapping[kind] = value
                else:
                    mapping.pop(kind, None)
                PREFS["note_presets"] = dict(mapping)
                save_prefs()

            with ui.grid(columns=2).classes("w-full gap-2"):
                for kind, label in NOTE_TYPE_LABELS.items():
                    ui.select(preset_names, label=label,
                              value=mapping.get(kind) if mapping.get(kind) in preset_names else "",
                              on_change=lambda e, k=kind: set_mapping(k, e.value or "")).classes("w-full")
            ui.switch("Auto-apply the preset for the detected note type",
                      value=bool(PREFS.get("note_auto_apply")),
                      on_change=lambda e: (PREFS.update(note_auto_apply=e.value), save_prefs()))

        # ---- learned-rule examples (regression check) -----------------------------
        with ui.card().classes("w-full gap-2"):
            ui.label("Check my learned rules").classes("font-semibold")
            ui.label("Every rule you teach by highlighting remembers what it was taught to do. "
                     "This replays those examples through your current rules and lists any whose "
                     "result changed, for example because a later rule undoes an earlier one.") \
                .classes("text-xs opacity-60 -mt-1")
            examples_out = ui.column().classes("w-full gap-1")

            def check_examples() -> None:
                report = rule_examples.check(load_config(CONFIG_PATH))
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
                        PREFS.clear()
                        PREFS.update(store.load_prefs())
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
local_api.register(app)  # /api/v1/* — loopback + token only (see chartcleaner/api.py)


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


CLIPBOARD_WATCHER: dict = {"watcher": None}


def clipboard_watcher(start: bool | None = None):
    """The app's clipboard watcher; start=True/False turns it on or off."""
    watcher = CLIPBOARD_WATCHER["watcher"]
    if watcher is None:
        import pyperclip
        prefs = PREFS.get("clipboard_watcher") or {}
        watcher = ClipboardWatcher(paste=pyperclip.paste, copy=pyperclip.copy,
                                   action=prefs.get("action", "auto"))
        CLIPBOARD_WATCHER["watcher"] = watcher
    if start is True:
        watcher.start()
    elif start is False:
        watcher.stop()
    return watcher


def _clipboard_startup() -> None:
    if os.environ.get("NICEGUI_USER_SIMULATION"):
        return  # tests never watch the real clipboard
    if (PREFS.get("clipboard_watcher") or {}).get("enabled"):
        try:
            clipboard_watcher(start=True)
        except Exception:
            pass  # no clipboard on this system: the Settings card says so


app.on_startup(_warm_nlp_engines)
app.on_startup(_update_startup)
app.on_startup(_clipboard_startup)


def main():
    global SERVER_PORT
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

    SERVER_PORT = port
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
