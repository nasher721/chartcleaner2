"""Persistence for the Chart Cleaner app: run history, preferences, presets,
exported files. Everything lives under the project folder — nothing is sent
anywhere, and nothing leaves this machine.

The implementation lives in per-area modules (``store_history``,
``store_prefs``, ``store_data``); this module owns the path constants and
re-exports the public names so existing imports keep working.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from . import paths
from .storage import migrate_config, migrate_portable_data
from .store_data import (
    _chart_data_paths,  # compat: tests read the chart-data file list through store
    delete_all_chart_data,
    delete_token_map,
    encrypt_legacy_token_maps,
    list_token_maps,
    load_token_map,
    maybe_purge_old_data,
    purge_old_data,
    save_token_map,
)
from .store_history import (
    append_audit_hits,
    append_run,
    clear_runs,
    dismiss_suggestion,
    get_suggestions,
    load_audit_hits,
    load_runs,
    summarize,
)
from .store_prefs import (
    _rule_count,  # compat: Settings shows the live rule count through store
    delete_preset,
    export_rules,
    export_settings,
    import_rules,
    import_settings,
    list_config_backups,
    list_presets,
    load_preset,
    load_prefs,
    load_watch_config,
    preview_rules_import,
    prune_exports,
    restore_config_backup,
    RULE_EXPORT_FORMATS,
    rotate_config_backup,
    save_export,
    save_prefs,
    save_preset,
    save_watch_config,
)

__all__ = [
    "BASE_DIR",
    "MUTABLE_DIR",
    "CONFIG_PATH",
    "DATA_DIR",
    "STATS_FILE",
    "PREFS_FILE",
    "PRESETS_DIR",
    "CUSTOM_RULES_DIR",
    "EXPORTS_DIR",
    "AUDIT_HITS_FILE",
    "SUGGESTIONS_STATE_FILE",
    "BACKUPS_DIR",
    "RECENT_DIR",
    "KNOWN_GOOD_DIR",
    "INBOX_STATE_FILE",
    "EDIT_LOG_FILE",
    "SIGNING_KEY_FILE",
    "TRUSTED_KEYS_FILE",
    "TOKENS_DIR",
    "WATCH_CONFIG_FILE",
    "WATCHED_OUT_DIR",
    "DEFAULT_PREFS",
    "DEFAULT_WATCH",
    "BACKUPS_TO_KEEP",
    "TOKEN_MAPS_TO_KEEP",
    "SETTINGS_BUNDLE_VERSION",
    "ensure_dir",
    "ensure_dirs",
    "seed_frozen_assets",
    "append_run",
    "load_runs",
    "clear_runs",
    "load_prefs",
    "save_prefs",
    "list_presets",
    "save_preset",
    "load_preset",
    "delete_preset",
    "save_export",
    "prune_exports",
    "summarize",
    "append_audit_hits",
    "load_audit_hits",
    "dismiss_suggestion",
    "get_suggestions",
    "rotate_config_backup",
    "list_config_backups",
    "restore_config_backup",
    "save_token_map",
    "load_token_map",
    "list_token_maps",
    "delete_token_map",
    "encrypt_legacy_token_maps",
    "purge_old_data",
    "maybe_purge_old_data",
    "delete_all_chart_data",
    "load_watch_config",
    "save_watch_config",
    "export_settings",
    "import_settings",
    "export_rules",
    "preview_rules_import",
    "import_rules",
]


def _resolve_base_dir() -> Path:
    """Repo root in normal runs; the folder holding the frozen app bundle
    when running from a PyInstaller build (so data stays writable and
    persistent next to Chart Cleaner.app / ChartCleaner.exe)."""
    if getattr(sys, "frozen", False):
        d = Path(sys.executable).resolve().parent
        while d.name in {"MacOS", "Contents"} or d.suffix == ".app":
            d = d.parent
        return d
    return Path(__file__).resolve().parent.parent


BASE_DIR = _resolve_base_dir()
MUTABLE_DIR = paths.user_data_dir() if paths.is_frozen() else BASE_DIR
CONFIG_PATH = MUTABLE_DIR / "config.json"
# CHARTCLEANER_DATA_DIR moves run history, backups, exports and tokens elsewhere
# (used by tests that start the app or MCP server as a separate process).
DATA_DIR = Path(os.environ["CHARTCLEANER_DATA_DIR"]) if os.environ.get("CHARTCLEANER_DATA_DIR") \
    else MUTABLE_DIR / "data"
STATS_FILE = DATA_DIR / "stats.jsonl"
PREFS_FILE = DATA_DIR / "prefs.json"
PRESETS_DIR = MUTABLE_DIR / "presets"
CUSTOM_RULES_DIR = MUTABLE_DIR / "custom_rules"
EXPORTS_DIR = DATA_DIR / "exports"
AUDIT_HITS_FILE = DATA_DIR / "audit_hits.jsonl"
SUGGESTIONS_STATE_FILE = DATA_DIR / "suggestions_state.json"
BACKUPS_DIR = DATA_DIR / "backups"
RECENT_DIR = DATA_DIR / "recent"            # encrypted recent inputs (recent_charts.py)
KNOWN_GOOD_DIR = DATA_DIR / "known_good"    # encrypted regression charts (regression_set.py)
INBOX_STATE_FILE = DATA_DIR / "inbox_state.json"  # dismissed rule-inbox ids (no chart text)
EDIT_LOG_FILE = DATA_DIR / "edit_log.enc"    # encrypted lines you deleted from outputs (edit_log.py)
SIGNING_KEY_FILE = DATA_DIR / "signing_key.enc"     # encrypted Ed25519 key for rule files
TRUSTED_KEYS_FILE = DATA_DIR / "trusted_keys.json"  # colleagues' public keys (no chart text)
TOKENS_DIR = DATA_DIR / "tokens"
WATCH_CONFIG_FILE = DATA_DIR / "watch.json"
WATCHED_OUT_DIR = DATA_DIR / "watched_out"
_MIGRATION_CHECKED = False


def _migrate_config_schema(cfg: dict) -> dict:
    """Apply the one known legacy change without touching current configs."""
    if "learned_rules" not in cfg:
        return {**cfg, "learned_rules": []}
    return cfg


DEFAULT_PREFS = {
    "dark": True,
    "last_preset": "",
    "auto_clean": False,
    "update_auto_check": True,
    "update_last_checked": None,
    "update_status": "Not checked",
    "update_status_code": None,
    # Kept here so load_prefs doesn't drop them (it keeps known keys only).
    "notes_folder": "",
    "clipboard_watcher": {"enabled": False, "action": "auto"},
    "note_presets": {},
    "note_auto_apply": False,
    # Delete token maps and batch/watcher output older than this (0 = keep).
    "retention_days": 14,
    # Encrypted copies of recent inputs for the rule inbox (recent_charts.py).
    "recent_charts": {"enabled": True, "keep": 20},
    # Clean page: layout and the main "Copy as…" format.
    "copy_default": "text",
    # Ask before copying a result whose fact check raised an alert (Clean page).
    "copy_guard": True,
    "ai_drawer": False,
    "onboarded": False,
    # Clean page: what selecting chart text does — "ask" shows the selection
    # bar; "remove" / "abbreviate" act at once (Remove text / Abbreviate modes).
    "selection_mode": "ask",
}

DEFAULT_WATCH = {
    "enabled": False,
    "watch_dir": "",
    "out_dir": "",       # empty = data/watched_out
    "exts": [".txt", ".md", ".docx", ".pdf"],
}

BACKUPS_TO_KEEP = 5
TOKEN_MAPS_TO_KEEP = 10
SETTINGS_BUNDLE_VERSION = 1


def ensure_dir(path: Path) -> None:
    """Create ``path`` (and its parents) only when it is missing.

    ``mkdir(parents=True, exist_ok=True)`` still costs a syscall when the
    directory already exists — measured at ~4 ms per call on a slow/external
    volume, and the persistence path calls it several times per clean. A stat
    is ~1000x cheaper and the end state is identical.
    """
    if not path.is_dir():
        path.mkdir(parents=True, exist_ok=True)


def ensure_dirs() -> None:
    global _MIGRATION_CHECKED
    if paths.is_frozen() and not _MIGRATION_CHECKED:
        from .engine import validate_config
        migrate_portable_data(paths.portable_root(), MUTABLE_DIR, validate_config)
        if CONFIG_PATH.is_file():
            migrate_config(CONFIG_PATH, validate_config, _migrate_config_schema)
        _MIGRATION_CHECKED = True
    for d in (DATA_DIR, EXPORTS_DIR, PRESETS_DIR, CUSTOM_RULES_DIR, BACKUPS_DIR, TOKENS_DIR):
        ensure_dir(d)


def seed_frozen_assets() -> None:
    """In a PyInstaller build, copy bundled defaults (config, sample chart,
    example scripts) next to the app on first run so they are user-editable."""
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", "."))
    ensure_dirs()
    if not CONFIG_PATH.exists():
        src = meipass / "chartcleaner" / "default_config.json"
        if src.exists():
            CONFIG_PATH.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    if not (BASE_DIR / "sample_chart.txt").exists():
        src = meipass / "sample_chart.txt"
        if src.exists():
            shutil.copy(src, BASE_DIR / "sample_chart.txt")
    src_rules = meipass / "custom_rules"
    if src_rules.is_dir() and not any(CUSTOM_RULES_DIR.glob("*.py")):
        for f in src_rules.glob("*.py"):
            shutil.copy(f, CUSTOM_RULES_DIR / f.name)
