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
.cc-panel { background: #f5f5f5; }
.body--dark .cc-panel { background: #1d1d1d; }
.cc-click { cursor: pointer; border-radius: 3px; }
.cc-gone { text-decoration: line-through; text-decoration-color: rgba(220,38,38,.7); background: rgba(255,60,60,.18); }
.cc-abbr { background: rgba(99,102,241,.18); border-bottom: 1px dotted #6366f1; }
.cc-click:hover { outline: 2px solid #f59e0b; }
.cc-spark td { padding: 2px 8px; }
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
        with ui.scroll_area().classes("w-full max-h-64 border rounded cc-panel p-2"):
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


REVIEW_CLICK_JS = ("(e) => { const t = e.target.closest('[data-cc]'); "
                   "if (t) emit(t.dataset.cc); }")


def _wrap_fragments(line: str, fragments: list[tuple[str, str, str]]) -> str:
    """Escape ``line`` and wrap each (text, key, css) fragment's first occurrence."""
    spans: list[tuple[int, int, str, str]] = []
    for frag, key, css in sorted(fragments, key=lambda f: -len(f[0])):
        if not frag:
            continue
        if css == "cc-abbr":
            m = re.search(r"(?<![\w])" + re.escape(frag) + r"(?![\w])", line)
            pos = m.start() if m else -1
        else:
            pos = line.find(frag)
        while pos >= 0 and any(a < pos + len(frag) and pos < b for a, b, _k, _c in spans):
            pos = line.find(frag, pos + 1)
        if pos >= 0:
            spans.append((pos, pos + len(frag), key, css))
    out, cursor = [], 0
    for a, b, key, css in sorted(spans):
        out.append(esc(line[cursor:a]))
        out.append(f"<span class='{css} cc-click' data-cc='{key}' title='Click for details'>"
                   f"{esc(line[a:b])}</span>")
        cursor = b
    out.append(esc(line[cursor:]))
    return "".join(out)


def review_diff_html(before: str, after: str, flag_lines: set[int] | None,
                     groups: list[dict]) -> str:
    """Side-by-side diff where every tracked change is clickable.

    ``groups`` (built by the Clean page) are ``{"kind": "removal"|"replace"|"abbr",
    "before", "after", ...}``; removed/replaced text on the left and applied
    abbreviations on the right carry ``data-cc="g<index>"``.
    """
    if len(before) + len(after) > 600_000:
        return "<p>Diff too large to display (use a smaller input).</p>"
    flagged = flag_lines or set()
    left_frags: list[tuple[str, str, str]] = []
    right_frags: list[tuple[str, str, str]] = []
    for i, g in enumerate(groups):
        if g["kind"] == "abbr":
            right_frags.append((g["after"], f"g{i}", "cc-abbr"))
            continue
        for b in g.get("befores", [g.get("before", "")]):
            for piece in str(b).split("\n"):
                piece = piece.strip()
                if len(piece) >= 2:
                    left_frags.append((piece, f"g{i}", "cc-gone"))

    def left(line: str) -> str:
        frags = [f for f in left_frags if f[0] in line]
        return _wrap_fragments(line, frags) if frags else esc(line)

    def right(line: str) -> str:
        frags = [f for f in right_frags if f[0] in line]
        return _wrap_fragments(line, frags) if frags else esc(line)

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
                rows.append(f"<tr><td class='cc-diff-left cc-del'>{left(a_lines[k])}</td><td></td></tr>")
        elif tag == "insert":
            for k in range(j1, j2):
                cls = "cc-ins cc-flag" if (k + 1) in flagged else "cc-ins"
                rows.append(f"<tr><td class='cc-diff-left'></td><td class='{cls}'>{right(b_lines[k])}</td></tr>")
        else:
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                lt = left(a_lines[i1 + k]) if i1 + k < i2 else ""
                rt = right(b_lines[j1 + k]) if j1 + k < j2 else ""
                jl = j1 + k
                cls = "cc-ins cc-flag" if (jl + 1) in flagged else "cc-ins"
                rows.append(f"<tr><td class='cc-diff-left cc-del'>{lt}</td><td class='{cls}'>{rt}</td></tr>")
    return ("<table class='cc-diff' style='width:100%; border-collapse:collapse'>"
            + "".join(rows) + "</table>")


# ---------------------------------------------------------------------------
# keyboard shortcuts and the command palette (⌘K / Ctrl+K)
# ---------------------------------------------------------------------------

MOD = "⌘" if sys.platform == "darwin" else "Ctrl"
# Shortcuts every page has; pages add their own through shell(shortcuts=...).
GLOBAL_SHORTCUTS = [("mod+k", "Open the command palette"),
                    ("mod+/", "Show keyboard shortcuts")]


def shortcut_label(combo: str) -> str:
    parts = combo.split("+")
    names = {"mod": MOD, "shift": "Shift", "alt": "Alt", "enter": "Enter", "/": "/"}
    return "+".join(names.get(p.lower(), p.upper()) for p in parts)


def shortcut_matches(combo: str, e) -> bool:
    """True when key event ``e`` is ``combo`` (e.g. "mod+shift+c", "alt+1")."""
    parts = [p.lower() for p in combo.split("+")]
    key = parts[-1]
    mods = e.modifiers
    want_mod = "mod" in parts
    if want_mod != bool(mods.ctrl or mods.meta):
        return False
    if ("shift" in parts) != bool(mods.shift) or ("alt" in parts) != bool(mods.alt):
        return False
    name = (e.key.name or "").lower()
    code = (e.key.code or "").lower()
    if key == "enter":
        return name == "enter"
    if key == "/":
        return name in ("/", "?") or code == "slash"
    if key.isdigit():
        return code == f"digit{key}" or name == key
    return name == key or code == f"key{key}"


def apply_preset(name: str) -> bool:
    """Make preset ``name`` the current rules (config.json); False if refused."""
    try:
        cfg = store.load_preset(name)
        perrs, _ = validate_config(cfg)
        if perrs:
            ui.notify("Preset has invalid rules: " + "; ".join(perrs[:3]), type="negative")
            return False
        save_config(cfg, CONFIG_PATH)
        PREFS.update(last_preset=name)
        save_prefs()
        ui.notify(f"Preset '{name}' applied.", type="positive")
        return True
    except Exception as ex:
        ui.notify(f"Could not apply preset: {ex}", type="negative")
        return False


def open_shortcuts_dialog(shortcuts: list[tuple[str, str]]) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[440px] gap-1"):
        ui.label("Keyboard shortcuts").classes("text-lg font-semibold")
        for combo, desc in list(GLOBAL_SHORTCUTS) + list(shortcuts):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(desc).classes("text-sm")
                ui.badge(shortcut_label(combo), color="blue-grey").props("outline")
        ui.label("Shortcuts work while typing in the chart box too.").classes("text-xs opacity-60 mt-1")
        ui.button("Close", on_click=dlg.close).props("flat")
    dlg.on("hide", dlg.delete)
    dlg.open()


def palette_commands(extra: list[dict] | None = None) -> list[dict]:
    """Every command the palette offers: ``{"label", "icon", "run", "group"}``."""
    cmds: list[dict] = list(extra or [])
    for path, icon, label in NAV:
        cmds.append({"label": f"Go to {label}", "icon": icon, "group": "Pages",
                     "run": lambda p=path: ui.navigate.to(p)})
    for name in store.list_presets():
        cmds.append({"label": f"Use preset: {name}", "icon": "inventory_2", "group": "Presets",
                     "run": lambda n=name: apply_preset(n) and ui.navigate.reload()})
    cmds.append({"label": "Clipboard watcher settings", "icon": "content_paste_search",
                 "group": "Settings", "run": lambda: ui.navigate.to("/settings")})
    return cmds


def open_command_palette(extra: list[dict] | None = None) -> None:
    commands = palette_commands(extra)
    with ui.dialog().props("position=top") as dlg, ui.card().classes("w-[560px] gap-1 mt-16"):
        query = ui.input(placeholder="Type a command…").props("autofocus outlined dense clearable") \
            .classes("w-full").mark("palette-input")
        listing = ui.column().classes("w-full gap-0 max-h-[50vh] overflow-auto")
        shown: list[dict] = []

        def choose(cmd: dict) -> None:
            dlg.close()
            result = cmd["run"]()
            if asyncio.iscoroutine(result):
                asyncio.get_running_loop().create_task(result)

        def draw() -> None:
            q = (query.value or "").casefold().split()
            shown[:] = [c for c in commands if all(w in c["label"].casefold() for w in q)][:40]
            listing.clear()
            with listing:
                if not shown:
                    ui.label("No matching command.").classes("text-sm opacity-60 p-2")
                for c in shown:
                    with ui.item(on_click=lambda c=c: choose(c)).classes("w-full rounded"):
                        with ui.item_section().props("avatar"):
                            ui.icon(c.get("icon") or "chevron_right")
                        with ui.item_section():
                            ui.item_label(c["label"])
                        with ui.item_section().props("side"):
                            ui.item_label(c.get("group", "")).props("caption")

        query.on_value_change(lambda _: draw())
        query.on("keydown.enter", lambda: shown and choose(shown[0]))
        draw()
    dlg.on("hide", dlg.delete)
    dlg.open()


def _status_strip() -> None:
    """Header chips: active preset, clipboard watcher, local AI, stored-data protection."""
    with ui.row().classes("items-center gap-1 flex-nowrap").mark("status-strip"):
        preset = PREFS.get("last_preset") or ""
        ui.chip(preset or "current rules", icon="inventory_2", color="white", text_color="primary",
                on_click=lambda: ui.navigate.to("/pipeline")).props("dense") \
            .tooltip("Rule preset in use — click to edit the pipeline")
        watcher = CLIPBOARD_WATCHER.get("watcher")
        on = bool(watcher is not None and watcher.running)
        ui.chip("watcher on" if on else "watcher off", icon="content_paste_search",
                color="white", text_color="green-8" if on else "grey-8",
                on_click=lambda: ui.navigate.to("/settings")).props("dense") \
            .tooltip("Clipboard watcher — click for settings")
        ai_chip = ui.chip("AI …", icon="psychology", color="white", text_color="grey-8") \
            .props("dense").tooltip("Local AI (Ollama) status")
        ui.chip("encrypted", icon="lock", color="white", text_color="teal",
                on_click=lambda: ui.navigate.to("/settings")).props("dense") \
            .tooltip("Stored chart data (token maps, recent charts) is encrypted on this computer")

    async def probe() -> None:
        if os.environ.get("NICEGUI_USER_SIMULATION"):
            ai_chip.set_text("AI ?")
            return
        try:
            from chartcleaner.summarizer import merge_llm_config

            def work():
                opts = merge_llm_config(load_config(CONFIG_PATH))
                client = LocalLlmClient(str(opts["base_url"]), timeout=0.6)
                models = client.list_models() if client.is_available() else []
                return str(opts["model"] or (models[0] if models else "")), bool(models)

            model, up = await run.io_bound(work)
        except Exception:
            model, up = "", False
        try:
            ai_chip.set_text(model.split(":")[0] if up and model else "AI off")
            ai_chip.props(f"text-color={'green-8' if up else 'grey-8'}")
        except Exception:
            pass  # the page may be gone

    ui.timer(0.2, probe, once=True)


@contextmanager
def shell(title: str, active: str, *, wide: bool = False, commands: list[dict] | None = None,
          shortcuts: list[tuple[str, str, object]] | None = None, actions=None):
    """Page frame: header (status strip, palette), menu, footer and content column.

    ``commands`` are extra palette entries; ``shortcuts`` are
    ``(combo, description, callback)``; ``actions`` is a callable that adds
    page-specific header buttons.
    """
    ui.add_head_html(CSS)
    dark = ui.dark_mode(bool(PREFS.get("dark", True)))
    page_shortcuts = list(shortcuts or [])

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-2 flex-nowrap"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()) \
                .props("flat round color=white aria-label='Toggle menu'")
            ui.icon("health_and_safety").classes("text-2xl")
            ui.label("Chart Cleaner").classes("text-xl font-bold cursor-pointer").on("click", lambda: ui.navigate.to("/"))
            ui.badge(f"v{__version__}", color="blue-grey").props("outline")
        with ui.row().classes("items-center gap-2 flex-nowrap"):
            _status_strip()
            if actions is not None:
                actions()
            ui.button(icon="search", on_click=lambda: open_command_palette(commands)) \
                .props("flat round color=white aria-label='Command palette'").mark("palette-button") \
                .tooltip(f"Command palette ({MOD}+K)")
            ui.button(icon="keyboard", on_click=lambda: open_shortcuts_dialog(
                [(c, d) for c, d, _ in page_shortcuts])) \
                .props("flat round color=white aria-label='Keyboard shortcuts'") \
                .tooltip(f"Keyboard shortcuts ({MOD}+/)")
            ui.switch("Dark", value=dark.value,
                      on_change=lambda e: (dark.set_value(e.value), PREFS.update(dark=e.value), save_prefs()))

    with ui.left_drawer(fixed=True, value=None).classes("cc-panel") as drawer:
        ui.label("Menu").classes("text-xs uppercase opacity-60 ml-2")
        for path, icon, label in NAV:
            btn = ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p))
            btn.props("flat align=left no-caps").classes("w-full")
            if path == ("/" if active == "clean" else "/" + active.strip("/")):
                btn.props("color=primary").classes("font-semibold bg-blue-1 dark:bg-blue-9")

    with ui.footer(fixed=False).classes("bg-transparent text-xs opacity-60"):
        ui.label("Runs entirely on this computer — 127.0.0.1 only. Not a guarantee of "
                 "de-identification; review output before sharing.")

    def on_key(e) -> None:
        if not e.action.keydown or e.action.repeat:
            return
        try:
            if shortcut_matches("mod+k", e):
                open_command_palette(commands)
                return
            if shortcut_matches("mod+/", e):
                open_shortcuts_dialog([(c, d) for c, d, _ in page_shortcuts])
                return
            for combo, _desc, callback in page_shortcuts:
                if shortcut_matches(combo, e):
                    result = callback()
                    if asyncio.iscoroutine(result):
                        asyncio.get_running_loop().create_task(result)
                    return
        except Exception:
            pass  # a shortcut must never break the page

    ui.keyboard(on_key=on_key, ignore=[])

    width = "max-w-[1680px]" if wide else "max-w-[1150px]"
    with ui.column().classes(f"w-full {width} mx-auto p-6 gap-4"):
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
