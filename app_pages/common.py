"""Shared state, imports and helpers for every page.

Names that get rebound — ``CONFIG_PATH``, ``PREFS``, ``CUSTOM_DIR`` (tests point them
at temp files) and ``updates.SERVER_PORT`` — are never star-imported: page modules
read them as ``common.CONFIG_PATH`` so a patch here reaches every page.
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
from chartcleaner import secure_store, store
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


# Everything public or private except the rebindable names (see the docstring).
_LIVE = {'CONFIG_PATH', 'PREFS', 'SERVER_PORT', 'CUSTOM_DIR'}
__all__ = [n for n in list(globals())
           if n not in _LIVE and (n == "__version__" or not n.startswith("__"))]
