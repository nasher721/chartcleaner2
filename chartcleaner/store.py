"""Persistence for the Chart Cleaner app: run history, preferences, presets,
exported files. Everything lives under the project folder — nothing is sent
anywhere, and nothing leaves this machine.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from pathlib import Path

from . import __version__
from . import paths
from .storage import migrate_config, migrate_portable_data


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
    "ai_drawer": False,
    "onboarded": False,
}


def ensure_dirs() -> None:
    global _MIGRATION_CHECKED
    if paths.is_frozen() and not _MIGRATION_CHECKED:
        from .engine import validate_config
        migrate_portable_data(paths.portable_root(), MUTABLE_DIR, validate_config)
        if CONFIG_PATH.is_file():
            migrate_config(CONFIG_PATH, validate_config, _migrate_config_schema)
        _MIGRATION_CHECKED = True
    for d in (DATA_DIR, EXPORTS_DIR, PRESETS_DIR, CUSTOM_RULES_DIR, BACKUPS_DIR, TOKENS_DIR):
        d.mkdir(parents=True, exist_ok=True)


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


# ---------------------------------------------------------------------------
# run history (JSONL — one JSON object per line)
# ---------------------------------------------------------------------------

def append_run(record: dict) -> None:
    ensure_dirs()
    with STATS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_runs() -> list[dict]:
    if not STATS_FILE.exists():
        return []
    runs: list[dict] = []
    bad = 0
    for line in STATS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                runs.append(obj)
        except json.JSONDecodeError:
            bad += 1
    if bad:
        runs.append({"ts": "", "source": f"({bad} corrupt history line(s) skipped)",
                     "chars_before": 0, "chars_after": 0})
    return runs


def clear_runs() -> int:
    """Delete the history file; returns how many runs were removed."""
    count = len(load_runs())
    if STATS_FILE.exists():
        STATS_FILE.unlink()
    return count


# ---------------------------------------------------------------------------
# preferences
# ---------------------------------------------------------------------------

def load_prefs() -> dict:
    prefs = dict(DEFAULT_PREFS)
    if PREFS_FILE.exists():
        try:
            stored = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                prefs.update({k: v for k, v in stored.items() if k in DEFAULT_PREFS})
        except (json.JSONDecodeError, OSError):
            pass
    return prefs


def save_prefs(prefs: dict) -> None:
    ensure_dirs()
    PREFS_FILE.write_text(json.dumps(prefs, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# presets (named, complete rule configurations)
# ---------------------------------------------------------------------------

def _preset_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    if not safe:
        raise ValueError("Preset name is empty")
    return PRESETS_DIR / f"{safe}.json"


def list_presets() -> list[str]:
    ensure_dirs()
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


def save_preset(name: str, config: dict) -> Path:
    ensure_dirs()
    path = _preset_path(name)
    path.write_text(json.dumps(config, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_preset(name: str) -> dict:
    path = _preset_path(name)
    return json.loads(path.read_text(encoding="utf-8"))


def delete_preset(name: str) -> bool:
    path = _preset_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# exports (downloadable cleaned files)
# ---------------------------------------------------------------------------

def save_export(text: str, base_name: str = "cleaned") -> str:
    """Write text into the exports dir; returns the file name to download."""
    ensure_dirs()
    prune_exports()
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in base_name).strip() or "cleaned"
    name = f"{safe}_{uuid.uuid4().hex[:8]}.txt"
    (EXPORTS_DIR / name).write_text(text, encoding="utf-8")
    return name


def prune_exports(max_age_hours: float = 24.0) -> None:
    ensure_dirs()
    cutoff = time.time() - max_age_hours * 3600
    for f in list(EXPORTS_DIR.glob("*.txt")) + list(EXPORTS_DIR.glob("*.zip")) \
            + list(EXPORTS_DIR.glob("*.docx")) + list(EXPORTS_DIR.glob("*.md")):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# aggregation for the dashboard
# ---------------------------------------------------------------------------

def summarize(runs: list[dict]) -> dict:
    totals = {
        "runs": len(runs),
        "chars_before": 0,
        "chars_after": 0,
        "words_before": 0,
        "words_after": 0,
        "phi": 0,
        "duration_ms": 0.0,
    }
    phi_by_type: dict[str, int] = {}
    stage_totals: dict[str, int] = {}
    by_day: dict[str, dict[str, float]] = {}

    for r in runs:
        totals["chars_before"] += r.get("chars_before", 0) or 0
        totals["chars_after"] += r.get("chars_after", 0) or 0
        totals["words_before"] += r.get("words_before", 0) or 0
        totals["words_after"] += r.get("words_after", 0) or 0
        totals["duration_ms"] += r.get("duration_ms", 0) or 0

        for s in r.get("stages", []):
            if s.get("skipped") or s.get("error"):
                continue
            delta = (s.get("chars_before", 0) or 0) - (s.get("chars_after", 0) or 0)
            if delta > 0:
                label = s.get("label") or s.get("id") or "?"
                stage_totals[label] = stage_totals.get(label, 0) + delta
            for k, v in (s.get("details") or {}).get("phi", {}).items():
                phi_by_type[k] = phi_by_type.get(k, 0) + int(v)
                totals["phi"] += int(v)

        day = (r.get("ts") or "")[:10]
        if day:
            bucket = by_day.setdefault(day, {"runs": 0, "chars_removed": 0})
            bucket["runs"] += 1
            bucket["chars_removed"] += max(0, (r.get("chars_before", 0) or 0) - (r.get("chars_after", 0) or 0))

    totals["chars_removed"] = max(0, totals["chars_before"] - totals["chars_after"])
    totals["avg_reduction"] = (
        round(100.0 * (1 - totals["chars_after"] / totals["chars_before"]), 1)
        if totals["chars_before"] else 0.0
    )
    totals["avg_duration_ms"] = round(totals["duration_ms"] / totals["runs"], 0) if totals["runs"] else 0.0

    top_stages = sorted(stage_totals.items(), key=lambda kv: kv[1], reverse=True)[:10]
    days = sorted(by_day)[-30:]

    return {
        **totals,
        "phi_by_type": dict(sorted(phi_by_type.items(), key=lambda kv: kv[1], reverse=True)),
        "top_stages": top_stages,
        "days": days,
        "by_day": {d: by_day[d] for d in days},
    }


# ---------------------------------------------------------------------------
# audit findings history + rule suggestions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def append_audit_hits(signatures: list[str]) -> None:
    """Record the audit signatures seen in one run (one history line per run)."""
    sigs = sorted({s for s in signatures if s})
    if not sigs:
        return
    ensure_dirs()
    with AUDIT_HITS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now_iso(), "sigs": sigs}, ensure_ascii=False) + "\n")


def load_audit_hits() -> list[dict]:
    if not AUDIT_HITS_FILE.exists():
        return []
    hits: list[dict] = []
    for line in AUDIT_HITS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and isinstance(obj.get("sigs"), list):
                hits.append(obj)
        except json.JSONDecodeError:
            continue
    return hits


def _load_suggestions_state() -> dict:
    if not SUGGESTIONS_STATE_FILE.exists():
        return {"dismissed": {}}
    try:
        state = json.loads(SUGGESTIONS_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("dismissed"), dict):
            return state
    except (json.JSONDecodeError, OSError):
        pass
    return {"dismissed": {}}


def dismiss_suggestion(signature: str) -> None:
    state = _load_suggestions_state()
    state["dismissed"][signature] = _now_iso()
    ensure_dirs()
    SUGGESTIONS_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def get_suggestions(days: int = 30, min_runs: int = 3) -> list[dict]:
    """Aggregate recent audit hits into suggestion candidates.

    Returns [{signature, runs, last}] for signatures seen in >= min_runs runs
    within the last `days` days, excluding dismissed ones. Most frequent first.
    """
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
    dismissed = set(_load_suggestions_state().get("dismissed", {}))
    runs_by_sig: dict[str, int] = {}
    last_by_sig: dict[str, str] = {}
    for hit in load_audit_hits():
        ts = str(hit.get("ts") or "")
        if ts and ts < cutoff:
            continue
        for sig in hit.get("sigs", []):
            runs_by_sig[sig] = runs_by_sig.get(sig, 0) + 1
            if sig not in last_by_sig or ts > last_by_sig[sig]:
                last_by_sig[sig] = ts
    out = [
        {"signature": sig, "runs": n, "last": last_by_sig.get(sig, "")}
        for sig, n in runs_by_sig.items()
        if n >= min_runs and sig not in dismissed
    ]
    out.sort(key=lambda s: (-s["runs"], s["signature"]))
    return out


# ---------------------------------------------------------------------------
# rotating config backups
# ---------------------------------------------------------------------------

BACKUPS_TO_KEEP = 5


def rotate_config_backup() -> Path | None:
    """Copy config.json into data/backups (timestamped), keep the newest N."""
    if not CONFIG_PATH.exists():
        return None
    ensure_dirs()
    dest = BACKUPS_DIR / f"config-{time.strftime('%Y%m%d-%H%M%S')}.json"
    if not dest.exists():  # second save within one second reuses the file
        dest.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    backups = sorted(BACKUPS_DIR.glob("config-*.json"))
    for old in backups[:-BACKUPS_TO_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def _rule_count(cfg: dict) -> int:
    keys = ("emr_line_metadata", "boilerplate", "epic_phi_patterns",
            "literal_replacements", "clinical_headers", "nlp_allow_list")
    total = 0
    for k in keys:
        v = cfg.get(k)
        if isinstance(v, list):
            total += len(v)
    return total


def list_config_backups() -> list[dict]:
    """Newest-first backup summaries: [{file, ts, rules}]."""
    if not BACKUPS_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(BACKUPS_DIR.glob("config-*.json"), reverse=True):
        ts = p.stem.removeprefix("config-")
        pretty = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}" if len(ts) == 15 else ts
        rules = 0
        try:
            rules = _rule_count(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            rules = -1  # unreadable; UI shows it as corrupt
        out.append({"file": str(p), "ts": pretty, "rules": rules})
    return out


def restore_config_backup(path: str | Path) -> tuple[bool, str]:
    """Validate a backup, then make it the live config. Never loads a corrupt file."""
    p = Path(path)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"Backup is unreadable: {e}"
    if not isinstance(cfg, dict):
        return False, "Backup does not contain a config object."
    from .engine import validate_config, save_config
    errors, _ = validate_config(cfg)
    if errors:
        return False, "Backup has invalid rules: " + errors[0]
    save_config(cfg, CONFIG_PATH)
    return True, f"Restored backup from {p.name} ({_rule_count(cfg)} rule entries)."


# ---------------------------------------------------------------------------
# reversible tokenization maps (data/tokens/)
# ---------------------------------------------------------------------------

TOKENS_DIR = DATA_DIR / "tokens"
TOKEN_MAPS_TO_KEEP = 10

WATCH_CONFIG_FILE = DATA_DIR / "watch.json"


def _token_map_files() -> list[Path]:
    """Oldest-first: encrypted ``.enc`` maps and legacy plain ``.json`` ones."""
    if not TOKENS_DIR.exists():
        return []
    return sorted(list(TOKENS_DIR.glob("tokens-*.enc")) + list(TOKENS_DIR.glob("tokens-*.json")),
                  key=lambda p: p.stem)


def save_token_map(mapping: dict[str, str], source: str = "") -> Path:
    """Persist one run's value→token map, encrypted (see secure_store)."""
    from . import secure_store

    ensure_dirs()
    dest = TOKENS_DIR / f"tokens-{time.strftime('%Y%m%d-%H%M%S')}.enc"
    if not dest.exists():  # two runs in the same second share the file
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": source,
            "map": mapping,
        }
        secure_store.write_text(dest, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    for old in _token_map_files()[:-TOKEN_MAPS_TO_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def _read_token_record(path: Path) -> dict:
    from . import secure_store
    data = json.loads(secure_store.read_text(path))
    if not isinstance(data, dict):
        raise ValueError("not a token map")
    return data


def list_token_maps() -> list[dict]:
    """Newest-first token map summaries: [{file, ts, count}] (count -1 = unreadable)."""
    out: list[dict] = []
    for p in reversed(_token_map_files()):
        try:
            data = _read_token_record(p)
            out.append({"file": str(p), "ts": str(data.get("ts") or p.stem),
                        "count": len(data.get("map") or {})})
        except Exception:
            out.append({"file": str(p), "ts": p.stem, "count": -1})
    return out


def load_token_map(path: str | Path) -> dict[str, str]:
    return dict(_read_token_record(Path(path)).get("map") or {})


def delete_token_map(path: str | Path) -> bool:
    p = Path(path)
    if p.exists() and p.parent == TOKENS_DIR:
        p.unlink()
        return True
    return False


def encrypt_legacy_token_maps() -> int:
    """Re-save plain-text token maps from older versions encrypted; returns how many."""
    from . import secure_store

    n = 0
    for p in list(TOKENS_DIR.glob("tokens-*.json")) if TOKENS_DIR.exists() else []:
        try:
            raw = p.read_bytes()
            if not secure_store.is_encrypted(raw):
                secure_store.write_text(p.with_suffix(".enc"), raw.decode("utf-8"))
            p.unlink()
            n += 1
        except (OSError, UnicodeDecodeError):
            continue
    return n


# ---------------------------------------------------------------------------
# retention: chart-bearing files are deleted after prefs["retention_days"]
# ---------------------------------------------------------------------------

WATCHED_OUT_DIR = DATA_DIR / "watched_out"


def _chart_data_paths() -> list[Path]:
    """Files and folders under data/ that hold chart text or PHI."""
    paths: list[Path] = _token_map_files()
    if RECENT_DIR.exists():
        paths += list(RECENT_DIR.glob("chart-*.enc"))
    if EXPORTS_DIR.exists():
        paths += [p for p in EXPORTS_DIR.iterdir() if p.name != ".gitkeep"]
    if WATCHED_OUT_DIR.exists():
        paths += list(WATCHED_OUT_DIR.iterdir())
    return paths


def _remove(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except OSError:
        return False


def purge_old_data(days: float | None = None) -> int:
    """Delete chart-bearing files older than ``days`` (default: the pref).

    Also encrypts any plain-text token maps left by older versions. Run
    history, config backups and rules hold no chart text and are kept.
    Returns the number of files/folders removed.
    """
    try:
        encrypt_legacy_token_maps()
    except Exception:
        pass
    if days is None:
        days = float(load_prefs().get("retention_days") or 0)
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for p in _chart_data_paths():
        try:
            if p.stat().st_mtime < cutoff and _remove(p):
                removed += 1
        except OSError:
            continue
    return removed


_last_purge = 0.0


def maybe_purge_old_data(every_seconds: float = 3600.0) -> None:
    """:func:`purge_old_data` at most once an hour, for long-running processes."""
    global _last_purge
    now = time.time()
    if now - _last_purge < every_seconds:
        return
    _last_purge = now
    try:
        purge_old_data()
    except Exception:
        pass  # housekeeping must never break a clean


def delete_all_chart_data() -> int:
    """"Delete stored chart data now": token maps, recent charts, known-good
    charts, exports and watcher output."""
    known_good = list(KNOWN_GOOD_DIR.glob("*.enc")) if KNOWN_GOOD_DIR.exists() else []
    return sum(_remove(p) for p in _chart_data_paths() + known_good)


# ---------------------------------------------------------------------------
# folder watcher configuration (data/watch.json)
# ---------------------------------------------------------------------------

DEFAULT_WATCH = {
    "enabled": False,
    "watch_dir": "",
    "out_dir": "",       # empty = data/watched_out
    "exts": [".txt", ".md", ".docx", ".pdf"],
}


def load_watch_config() -> dict:
    cfg = dict(DEFAULT_WATCH)
    if WATCH_CONFIG_FILE.exists():
        try:
            stored = json.loads(WATCH_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if k in cfg:
                        cfg[k] = v
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_watch_config(cfg: dict) -> None:
    ensure_dirs()
    WATCH_CONFIG_FILE.write_text(
        json.dumps({k: cfg.get(k, DEFAULT_WATCH[k]) for k in DEFAULT_WATCH},
                   indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# settings bundle: move every user customization to a (new) install
# ---------------------------------------------------------------------------

SETTINGS_BUNDLE_VERSION = 1


def export_settings(dest: str | Path | None = None) -> tuple[Path, dict]:
    """Zip up everything the user customized so it can move to a clean install.

    Included: config.json (rules, options and learned rules), prefs.json,
    presets, custom scripts, folder-watcher config and suggestion dismissals.
    Deliberately excluded: run history (reproducible), token maps and cleaned
    exports (they undo or contain the cleaning, i.e. potential PHI).

    Returns (zip_path, counts) where counts maps what was bundled.
    """
    ensure_dirs()
    if dest is None:
        dest = EXPORTS_DIR / f"chart-cleaner-settings-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    dest = Path(dest)
    counts: dict[str, int] = {}
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arc in ((CONFIG_PATH, "config.json"),
                         (PREFS_FILE, "data/prefs.json"),
                         (WATCH_CONFIG_FILE, "data/watch.json"),
                         (SUGGESTIONS_STATE_FILE, "data/suggestions_state.json")):
            p = Path(src)
            if p.exists():
                zf.write(p, arc)
                counts[arc.split("/")[-1]] = counts.get(arc.split("/")[-1], 0) + 1
        presets = sorted(PRESETS_DIR.glob("*.json")) if PRESETS_DIR.exists() else []
        for p in presets:
            zf.write(p, f"presets/{p.name}")
        counts["presets"] = len(presets)
        scripts = [p for p in sorted(CUSTOM_RULES_DIR.glob("*.py"))
                   if not p.name.startswith("_")] if CUSTOM_RULES_DIR.exists() else []
        for p in scripts:
            zf.write(p, f"custom_rules/{p.name}")
        counts["custom_rules"] = len(scripts)
        manifest = {
            "kind": "chart-cleaner-settings",
            "version": SETTINGS_BUNDLE_VERSION,
            "app_version": __version__,
            "exported_at": _now_iso(),
            "counts": counts,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    return dest, counts


def import_settings(zip_path: str | Path) -> tuple[bool, str]:
    """Apply a settings bundle made by export_settings.

    Safe on a clean install (missing files are simply absent from the bundle)
    and on a used one (the current config is backed up first; an invalid
    config refuses the whole import instead of half-applying).

    Returns (ok, summary_message).
    """
    from .engine import validate_config, save_config

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                return False, "Not a Chart Cleaner settings bundle (manifest.json missing)."
            try:
                manifest = json.loads(zf.read("manifest.json"))
            except json.JSONDecodeError:
                return False, "Bundle manifest is corrupt."
            if manifest.get("kind") != "chart-cleaner-settings":
                return False, "Not a Chart Cleaner settings bundle."
            version = int(manifest.get("version") or 0)
            if version > SETTINGS_BUNDLE_VERSION:
                return False, (f"Bundle was written by a newer app version (bundle v{version} "
                               f"> v{SETTINGS_BUNDLE_VERSION}); update this install first.")

            applied: list[str] = []

            if "config.json" in names:
                try:
                    cfg = json.loads(zf.read("config.json").decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return False, "Bundle config.json is not valid JSON — nothing was changed."
                if not isinstance(cfg, dict):
                    return False, "Bundle config.json is not a rules object — nothing was changed."
                errs, _ = validate_config(cfg)
                if errs:
                    return False, "Bundle rules are invalid (" + errs[0] + ") — nothing was changed."
                rotate_config_backup()
                ensure_dirs()
                save_config(cfg, CONFIG_PATH)
                applied.append(f"{_rule_count(cfg)} rule entries")

            if "data/prefs.json" in names:
                try:
                    prefs = json.loads(zf.read("data/prefs.json"))
                except json.JSONDecodeError:
                    prefs = {}
                if isinstance(prefs, dict):
                    save_prefs({k: prefs[k] for k in DEFAULT_PREFS if k in prefs})
                    applied.append("preferences")

            n_presets = 0
            for name in names:
                if name.startswith("presets/") and name.endswith(".json"):
                    target = PRESETS_DIR / Path(name).name
                    target.write_bytes(zf.read(name))
                    n_presets += 1
            if n_presets:
                applied.append(f"{n_presets} preset(s)")

            n_scripts = 0
            for name in names:
                if name.startswith("custom_rules/") and name.endswith(".py") \
                        and not Path(name).name.startswith("_"):
                    target = CUSTOM_RULES_DIR / Path(name).name
                    target.write_bytes(zf.read(name))
                    n_scripts += 1
            if n_scripts:
                applied.append(f"{n_scripts} custom script(s)")

            for arc, label in (("data/watch.json", "folder watcher"),
                               ("data/suggestions_state.json", "suggestion dismissals")):
                if arc in names:
                    (DATA_DIR / Path(arc).name).write_bytes(zf.read(arc))
                    applied.append(label)
    except zipfile.BadZipFile:
        return False, "That file is not a valid zip archive."
    except OSError as e:
        return False, f"Could not read the bundle: {e}"

    if not applied:
        return True, "Bundle was empty — nothing to import."
    return True, "Imported: " + ", ".join(applied) + "."


# ---------------------------------------------------------------------------
# portable learned-rule sharing (small JSON/CSV files)
# ---------------------------------------------------------------------------

def export_rules(dest: str | Path | None = None, fmt: str = "json") -> tuple[Path, dict]:
    """Export learned removal/replacement rules without app state or history."""
    from .rule_sharing import export_csv, export_json, payload_from_config

    ensure_dirs()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("Current config is not an object")
    fmt = fmt.lower()
    if fmt not in {"json", "csv"}:
        raise ValueError("fmt must be 'json' or 'csv'")
    path = Path(dest) if dest is not None else EXPORTS_DIR / (
        f"chart-cleaner-rules-{time.strftime('%Y%m%d-%H%M%S')}.{fmt}")
    (export_csv if fmt == "csv" else export_json)(config, path)
    payload = payload_from_config(config)
    return path, {"removals": len(payload["removals"]),
                  "replacements": len(payload["replacements"]),
                  "abbreviations": len((payload.get("abbreviations") or {}).get("custom", [])) if fmt == "json" else 0}


def preview_rules_import(source: str | Path, mode: str = "merge") -> dict:
    """Validate a portable rules file and return counts before applying it."""
    from .rule_sharing import import_csv, import_json, preview_import

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    suffix = Path(source).suffix.lower()
    payload = import_csv(source) if suffix == ".csv" else import_json(source)
    return preview_import(config, payload, mode)


def import_rules(source: str | Path, mode: str = "merge") -> tuple[bool, str]:
    """Atomically apply a portable rules file after validation and preview."""
    from .engine import save_config
    from .rule_sharing import apply_import, import_csv, import_json

    try:
        if mode not in {"merge", "replace"}:
            return False, "mode must be 'merge' or 'replace'"
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
        if not isinstance(config, dict):
            return False, "Current config is not an object — nothing was changed."
        suffix = Path(source).suffix.lower()
        payload = import_csv(source) if suffix == ".csv" else import_json(source)
        updated = apply_import(config, payload, mode)
        from .engine import validate_config
        errors, _ = validate_config(updated)
        if errors:
            return False, "Imported rules are invalid (" + errors[0] + ") — nothing was changed."
        rotate_config_backup()
        ensure_dirs()
        save_config(updated, CONFIG_PATH)
        added = len(updated.get("learned_rules", [])) - len(config.get("learned_rules", []))
        return True, f"Imported {len(payload.get('removals', []))} removal(s) and " \
                     f"{len(payload.get('replacements', []))} replacement(s) " \
                     f"({mode}; net rule change {added:+d})."
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return False, f"Could not import rules — nothing was changed: {exc}"
