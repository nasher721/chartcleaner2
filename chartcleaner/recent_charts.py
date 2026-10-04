"""The last few charts cleaned on this computer, encrypted, for rule suggestions.

The rule inbox ("this line appeared in 9 of your last 10 charts — remove it?")
and the "this rule would also change…" preview need real charts to look at.
Run history deliberately holds no chart text, so the inputs of recent Clean-page
runs are kept here instead:

* one file per chart in ``data/recent/``, encrypted with :mod:`secure_store`
  (same key and protection as token maps);
* at most ``prefs.recent_charts.keep`` files (default 20), oldest dropped first;
* deleted by the retention purge (``prefs.retention_days``) and by
  Settings → *Delete stored chart data now*;
* off entirely when ``prefs.recent_charts.enabled`` is false.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import store

__all__ = ["DEFAULTS", "recent_dir", "settings", "remember", "load", "texts", "count", "clear"]

DEFAULTS = {"enabled": True, "keep": 20}
MAX_CHARS = 400_000  # a chart bigger than this isn't kept (suggestions don't need it)


def recent_dir() -> Path:
    return store.RECENT_DIR


def settings(prefs: dict | None = None) -> dict:
    prefs = store.load_prefs() if prefs is None else prefs
    raw = prefs.get("recent_charts") if isinstance(prefs, dict) else None
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        out["enabled"] = bool(raw.get("enabled", DEFAULTS["enabled"]))
        try:
            out["keep"] = max(1, min(200, int(raw.get("keep", DEFAULTS["keep"]))))
        except (TypeError, ValueError):
            pass
    return out


def _files() -> list[Path]:
    d = recent_dir()
    return sorted(d.glob("chart-*.enc"), key=lambda p: p.name) if d.exists() else []


def remember(text: str, source: str = "", prefs: dict | None = None) -> Path | None:
    """Keep ``text`` (encrypted) unless disabled, empty, huge or already the newest."""
    from . import secure_store

    opts = settings(prefs)
    if not opts["enabled"] or not text.strip() or len(text) > MAX_CHARS:
        return None
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    files = _files()
    if files and files[-1].stem.endswith(digest):
        return files[-1]  # the same chart cleaned again
    d = recent_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"chart-{time.time_ns():020d}-{digest}.enc"
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source, "text": text}
    secure_store.write_text(dest, json.dumps(record, ensure_ascii=False))
    for old in _files()[:-opts["keep"]]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def load(limit: int | None = None) -> list[dict]:
    """Newest-first ``{"ts", "source", "text", "file"}``; unreadable files are skipped."""
    from . import secure_store

    out: list[dict] = []
    for p in reversed(_files()):
        if limit is not None and len(out) >= limit:
            break
        try:
            data = json.loads(secure_store.read_text(p))
            if isinstance(data, dict) and isinstance(data.get("text"), str):
                out.append({**data, "file": str(p)})
        except Exception:
            continue  # encrypted with another machine's key, or damaged
    return out


def texts(limit: int | None = None) -> list[str]:
    return [r["text"] for r in load(limit)]


def count() -> int:
    return len(_files())


def clear() -> int:
    n = 0
    for p in _files():
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
