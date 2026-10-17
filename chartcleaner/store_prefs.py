"""Preferences, presets, exports, backups, watcher config and settings/rule
portability (split from ``store.py``).

Path constants live on the :mod:`chartcleaner.store` facade and are read
through it (never copied here) so test isolation via ``monkeypatch.setattr``
keeps working exactly as before.
"""

from __future__ import annotations

import json
import math
import time
import uuid
import zipfile
from pathlib import Path

from . import __version__

__all__ = [
    "load_prefs",
    "save_prefs",
    "list_presets",
    "save_preset",
    "load_preset",
    "delete_preset",
    "save_export",
    "prune_exports",
    "rotate_config_backup",
    "list_config_backups",
    "restore_config_backup",
    "load_watch_config",
    "save_watch_config",
    "export_settings",
    "import_settings",
    "export_rules",
    "preview_rules_import",
    "import_rules",
]


def _store():
    """The store facade (deferred: store.py re-exports this module)."""
    from . import store
    return store


def _coerce_pref(key: str, value, default):
    """Keep ``value`` only when it has the same shape as the shipped default.

    ``prefs.json`` is a plain file a user can hand-edit (or that a partial
    write can truncate). A wrong-typed value must never reach the UI — a
    string where a number belongs crashes the retention purge, and a string
    where a bool belongs is silently truthy. Anything unexpected falls back to
    the default, so a damaged file degrades to factory settings, not a break.
    """
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        if isinstance(value, bool):  # bool is an int subclass; reject it
            return default
        if isinstance(value, (int, float)):
            # json.loads accepts NaN/Infinity, which are invalid JSON and would
            # poison arithmetic downstream (e.g. retention date math).
            if isinstance(value, float) and not math.isfinite(value):
                return default
            return value
        return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, dict):
        return value if isinstance(value, dict) else default
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    if default is None:  # "unset" keys accept anything simple
        return value if isinstance(value, (str, int, float, bool, type(None))) else default
    return value


def load_prefs() -> dict:
    prefs = dict(_store().DEFAULT_PREFS)
    if _store().PREFS_FILE.exists():
        try:
            stored = json.loads(_store().PREFS_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if k in _store().DEFAULT_PREFS:
                        prefs[k] = _coerce_pref(k, v, _store().DEFAULT_PREFS[k])
        except (json.JSONDecodeError, OSError):
            pass
    return prefs


def save_prefs(prefs: dict) -> None:
    _store().ensure_dirs()
    _store().PREFS_FILE.write_text(json.dumps(prefs, indent=2) + "\n", encoding="utf-8")


def _preset_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    if not safe:
        raise ValueError("Preset name is empty")
    return _store().PRESETS_DIR / f"{safe}.json"


def list_presets() -> list[str]:
    _store().ensure_dirs()
    return sorted(p.stem for p in _store().PRESETS_DIR.glob("*.json"))


def save_preset(name: str, config: dict) -> Path:
    _store().ensure_dirs()
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


def save_export(text: str, base_name: str = "cleaned") -> str:
    """Write text into the exports dir; returns the file name to download."""
    _store().ensure_dirs()
    prune_exports()
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in base_name).strip() or "cleaned"
    name = f"{safe}_{uuid.uuid4().hex[:8]}.txt"
    (_store().EXPORTS_DIR / name).write_text(text, encoding="utf-8")
    return name


def prune_exports(max_age_hours: float = 24.0) -> None:
    _store().ensure_dirs()
    cutoff = time.time() - max_age_hours * 3600
    for f in list(_store().EXPORTS_DIR.glob("*.txt")) + list(_store().EXPORTS_DIR.glob("*.zip")) \
            + list(_store().EXPORTS_DIR.glob("*.docx")) + list(_store().EXPORTS_DIR.glob("*.md")):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def rotate_config_backup() -> Path | None:
    """Copy config.json into data/backups (timestamped), keep the newest N."""
    if not _store().CONFIG_PATH.exists():
        return None
    _store().ensure_dirs()
    dest = _store().BACKUPS_DIR / f"config-{time.strftime('%Y%m%d-%H%M%S')}.json"
    if not dest.exists():  # second save within one second reuses the file
        dest.write_text(_store().CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    backups = sorted(_store().BACKUPS_DIR.glob("config-*.json"))
    for old in backups[:-_store().BACKUPS_TO_KEEP]:
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
    if not _store().BACKUPS_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(_store().BACKUPS_DIR.glob("config-*.json"), reverse=True):
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
    save_config(cfg, _store().CONFIG_PATH, source="restore")
    return True, f"Restored backup from {p.name} ({_rule_count(cfg)} rule entries)."


def load_watch_config() -> dict:
    cfg = dict(_store().DEFAULT_WATCH)
    if _store().WATCH_CONFIG_FILE.exists():
        try:
            stored = json.loads(_store().WATCH_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if k in cfg:
                        cfg[k] = v
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_watch_config(cfg: dict) -> None:
    _store().ensure_dirs()
    _store().WATCH_CONFIG_FILE.write_text(
        json.dumps({k: cfg.get(k, _store().DEFAULT_WATCH[k]) for k in _store().DEFAULT_WATCH},
                   indent=2) + "\n", encoding="utf-8")


def export_settings(dest: str | Path | None = None) -> tuple[Path, dict]:
    """Zip up everything the user customized so it can move to a clean install.

    Included: config.json (rules, options and learned rules), prefs.json,
    presets, custom scripts, folder-watcher config and suggestion dismissals.
    Deliberately excluded: run history (reproducible), token maps and cleaned
    exports (they undo or contain the cleaning, i.e. potential PHI).

    Returns (zip_path, counts) where counts maps what was bundled.
    """
    _store().ensure_dirs()
    if dest is None:
        dest = _store().EXPORTS_DIR / f"chart-cleaner-settings-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    dest = Path(dest)
    counts: dict[str, int] = {}
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arc in ((_store().CONFIG_PATH, "config.json"),
                         (_store().PREFS_FILE, "data/prefs.json"),
                         (_store().WATCH_CONFIG_FILE, "data/watch.json"),
                         (_store().SUGGESTIONS_STATE_FILE, "data/suggestions_state.json")):
            p = Path(src)
            if p.exists():
                zf.write(p, arc)
                counts[arc.split("/")[-1]] = counts.get(arc.split("/")[-1], 0) + 1
        presets = sorted(_store().PRESETS_DIR.glob("*.json")) if _store().PRESETS_DIR.exists() else []
        for p in presets:
            zf.write(p, f"presets/{p.name}")
        counts["presets"] = len(presets)
        scripts = [p for p in sorted(_store().CUSTOM_RULES_DIR.glob("*.py"))
                   if not p.name.startswith("_")] if _store().CUSTOM_RULES_DIR.exists() else []
        for p in scripts:
            zf.write(p, f"custom_rules/{p.name}")
        counts["custom_rules"] = len(scripts)
        manifest = {
            "kind": "chart-cleaner-settings",
            "version": _store().SETTINGS_BUNDLE_VERSION,
            "app_version": __version__,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
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
            if version > _store().SETTINGS_BUNDLE_VERSION:
                return False, (f"Bundle was written by a newer app version (bundle v{version} "
                               f"> v{_store().SETTINGS_BUNDLE_VERSION}); update this install first.")

            applied: list[str] = []
            _store().ensure_dirs()

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
                save_config(cfg, _store().CONFIG_PATH, source="restore")
                applied.append(f"{_rule_count(cfg)} rule entries")

            if "data/prefs.json" in names:
                try:
                    prefs = json.loads(zf.read("data/prefs.json"))
                except json.JSONDecodeError:
                    prefs = {}
                if isinstance(prefs, dict):
                    save_prefs({k: prefs[k] for k in _store().DEFAULT_PREFS if k in prefs})
                    applied.append("preferences")

            n_presets = 0
            for name in names:
                if name.startswith("presets/") and name.endswith(".json"):
                    target = _store().PRESETS_DIR / Path(name).name
                    target.write_bytes(zf.read(name))
                    n_presets += 1
            if n_presets:
                applied.append(f"{n_presets} preset(s)")

            n_scripts = 0
            for name in names:
                if name.startswith("custom_rules/") and name.endswith(".py") \
                        and not Path(name).name.startswith("_"):
                    target = _store().CUSTOM_RULES_DIR / Path(name).name
                    target.write_bytes(zf.read(name))
                    n_scripts += 1
            if n_scripts:
                applied.append(f"{n_scripts} custom script(s)")

            for arc, label in (("data/watch.json", "folder watcher"),
                               ("data/suggestions_state.json", "suggestion dismissals")):
                if arc in names:
                    (_store().DATA_DIR / Path(arc).name).write_bytes(zf.read(arc))
                    applied.append(label)
    except zipfile.BadZipFile:
        return False, "That file is not a valid zip archive."
    except OSError as e:
        return False, f"Could not read the bundle: {e}"

    if not applied:
        return True, "Bundle was empty — nothing to import."
    return True, "Imported: " + ", ".join(applied) + "."


RULE_EXPORT_FORMATS = ("json", "csv", "txt")


def _current_config() -> dict:
    config = json.loads(_store().CONFIG_PATH.read_text(encoding="utf-8")) if _store().CONFIG_PATH.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("Current config is not an object")
    return config


def export_rules(dest: str | Path | None = None, fmt: str = "json", *,
                 complete: bool = False) -> tuple[Path, dict]:
    """Export learned removal/replacement rules without app state or history.

    ``fmt`` is ``json`` (everything, signable), ``csv`` (text rules only) or
    ``txt`` (plain-text remove / replace / abbreviation lists). ``complete``
    (JSON only) also exports the built-in stage rule lists and exceptions.
    """
    from .quick_rules import export_text
    from .rule_sharing import export_csv, export_json, payload_from_config

    _store().ensure_dirs()
    config = _current_config()
    fmt = fmt.lower()
    if fmt not in RULE_EXPORT_FORMATS:
        raise ValueError("fmt must be 'json', 'csv' or 'txt'")
    path = Path(dest) if dest is not None else _store().EXPORTS_DIR / (
        f"chart-cleaner-rules-{time.strftime('%Y%m%d-%H%M%S')}.{fmt}")
    if fmt == "txt":
        path.write_text(export_text(config), encoding="utf-8")
    elif fmt == "csv":
        export_csv(config, path)
    else:
        export_json(config, path, complete=complete)
    payload = payload_from_config(config, complete=complete and fmt == "json")
    return path, {"removals": len(payload["removals"]),
                  "replacements": len(payload["replacements"]),
                  "abbreviations": len((payload.get("abbreviations") or {}).get("custom", []))
                  if fmt in ("json", "txt") else 0,
                  "stage_rules": sum(len(v) for v in (payload.get("stage_rules") or {}).values())}


def _read_rules_file(source: str | Path) -> dict:
    from .rule_sharing import import_csv, import_json

    suffix = Path(source).suffix.lower()
    return import_csv(source) if suffix == ".csv" else import_json(source)


def preview_rules_import(source: str | Path, mode: str = "merge") -> dict:
    """Validate a portable rules file and return counts before applying it."""
    from .rule_sharing import preview_import

    return preview_import(_current_config(), _read_rules_file(source), mode)


def _import_text_rules(source: str | Path, config: dict) -> tuple[dict, str]:
    """Merge a plain-text (.txt) rules file; raises ValueError on bad lines."""
    from .quick_rules import apply_text

    updated, reports, errors = apply_text(config, Path(source).read_text(encoding="utf-8-sig"))
    if errors:
        raise ValueError(errors[0])
    return updated, (f"Imported {len(reports['remove'].added)} removal(s), "
                     f"{len(reports['replace'].added) + len(reports['replace'].updated)} replacement(s) and "
                     f"{len(reports['abbreviations'].added) + len(reports['abbreviations'].updated)} "
                     f"abbreviation(s)" + (f"; {len(reports['abbreviations'].blocked)} Do Not Use "
                                           "abbreviation(s) skipped" if reports['abbreviations'].blocked else "")
                     + ".")


def import_rules(source: str | Path, mode: str = "merge") -> tuple[bool, str]:
    """Atomically apply a portable rules file after validation and preview.

    ``.txt`` files (plain-text lists) always merge.
    """
    from .engine import save_config
    from .rule_sharing import apply_import, preview_import

    try:
        if mode not in {"merge", "replace"}:
            return False, "mode must be 'merge' or 'replace'"
        try:
            config = _current_config()
        except ValueError:
            return False, "Current config is not an object — nothing was changed."
        if Path(source).suffix.lower() == ".txt":
            updated, message = _import_text_rules(source, config)
            payload = None
        else:
            payload = _read_rules_file(source)
            preview = preview_import(config, payload, mode)
            updated = apply_import(config, payload, mode)
        from .engine import validate_config
        errors, _ = validate_config(updated)
        if errors:
            return False, "Imported rules are invalid (" + errors[0] + ") — nothing was changed."
        rotate_config_backup()
        _store().ensure_dirs()
        save_config(updated, _store().CONFIG_PATH, source="import")
        if payload is None:
            return True, message
        added = len(updated.get("learned_rules", [])) - len(config.get("learned_rules", []))
        extra = preview["stage_rules_added"] + preview["exceptions_added"]
        return True, f"Imported {len(payload.get('removals', []))} removal(s) and " \
                     f"{len(payload.get('replacements', []))} replacement(s) " \
                     + (f"plus {extra} new built-in stage rule(s)/exception(s) " if extra else "") + \
                     f"({mode}; net rule change {added:+d})."
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return False, f"Could not import rules — nothing was changed: {exc}"
