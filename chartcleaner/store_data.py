"""Chart-bearing files: encrypted token maps, retention purging and stored-data
deletion (split from ``store.py``).

Run history, config backups and rules hold no chart text and are kept; token
maps, recent charts, known-good charts, exports and watcher output are deleted
after ``prefs.retention_days``.

Path constants live on the :mod:`chartcleaner.store` facade and are read
through it (never copied here) so test isolation via ``monkeypatch.setattr``
keeps working exactly as before.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from .store_prefs import load_prefs

__all__ = [
    "save_token_map",
    "list_token_maps",
    "load_token_map",
    "delete_token_map",
    "encrypt_legacy_token_maps",
    "purge_old_data",
    "maybe_purge_old_data",
    "delete_all_chart_data",
]


_TOKEN_SAVE_LOCK = threading.Lock()


def _store():
    """The store facade (deferred: store.py re-exports this module)."""
    from . import store
    return store


def _token_map_files() -> list[Path]:
    """Oldest-first: encrypted ``.enc`` maps and legacy plain ``.json`` ones."""
    if not _store().TOKENS_DIR.exists():
        return []
    return sorted(list(_store().TOKENS_DIR.glob("tokens-*.enc")) + list(_store().TOKENS_DIR.glob("tokens-*.json")),
                  key=lambda p: p.stem)


def save_token_map(mapping: dict[str, str], source: str = "") -> Path:
    """Persist one run's value→token map, encrypted (see secure_store)."""
    from . import secure_store

    _store().ensure_dirs()
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "map": mapping,
    }
    with _TOKEN_SAVE_LOCK:
        # One file per run, even for runs in the same second (API, watcher,
        # patient lists): "Restore" reads the newest map, so a shared file
        # would put another chart's values back. Names still sort oldest-first.
        now = time.time()
        stem = f"tokens-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{int(now % 1 * 1e6):06d}"
        dest = _store().TOKENS_DIR / f"{stem}.enc"
        n = 1
        while dest.exists():
            dest = _store().TOKENS_DIR / f"{stem}-{n:03d}.enc"
            n += 1
        secure_store.write_text(dest, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    for old in _token_map_files()[:-_store().TOKEN_MAPS_TO_KEEP]:
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
    if p.exists() and p.parent == _store().TOKENS_DIR:
        p.unlink()
        return True
    return False


def encrypt_legacy_token_maps() -> int:
    """Re-save plain-text token maps from older versions encrypted; returns how many."""
    from . import secure_store

    n = 0
    for p in list(_store().TOKENS_DIR.glob("tokens-*.json")) if _store().TOKENS_DIR.exists() else []:
        try:
            raw = p.read_bytes()
            if not secure_store.is_encrypted(raw):
                secure_store.write_text(p.with_suffix(".enc"), raw.decode("utf-8"))
            p.unlink()
            n += 1
        except (OSError, UnicodeDecodeError):
            continue
    return n


def _chart_data_paths() -> list[Path]:
    """Files and folders under data/ that hold chart text or PHI."""
    paths: list[Path] = _token_map_files()
    if _store().RECENT_DIR.exists():
        paths += list(_store().RECENT_DIR.glob("chart-*.enc"))
    if _store().EDIT_LOG_FILE.exists():
        paths.append(_store().EDIT_LOG_FILE)
    if _store().RULE_HISTORY_FILE.exists():
        paths.append(_store().RULE_HISTORY_FILE)
    if _store().EXPORTS_DIR.exists():
        paths += [p for p in _store().EXPORTS_DIR.iterdir() if p.name != ".gitkeep"]
    if _store().WATCHED_OUT_DIR.exists():
        paths += list(_store().WATCHED_OUT_DIR.iterdir())
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
    known_good = list(_store().KNOWN_GOOD_DIR.glob("*.enc")) if _store().KNOWN_GOOD_DIR.exists() else []
    return sum(_remove(p) for p in _chart_data_paths() + known_good)
