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

A chart can carry a **bed tag** (``G20-1``, ``Bed 4``) so the daily-note view,
trends and deltas follow the patient rather than the paste. The tag is a
short label stored *inside* the encrypted record — never in a file name.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import store

__all__ = ["DEFAULTS", "recent_dir", "settings", "remember", "load", "texts", "count", "clear",
           "clean_tag", "set_tag", "tags", "latest_for"]

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


_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9 #._/-]{0,23}")


def clean_tag(tag: str | None) -> str:
    """A bed tag as stored: trimmed, at most 24 safe characters ("" when invalid)."""
    tag = " ".join((tag or "").split())
    return tag if _TAG.fullmatch(tag) else ""


def remember(text: str, source: str = "", prefs: dict | None = None, *, tag: str = "") -> Path | None:
    """Keep ``text`` (encrypted) unless disabled, empty, huge or already the newest."""
    from . import secure_store

    opts = settings(prefs)
    if not opts["enabled"] or not text.strip() or len(text) > MAX_CHARS:
        return None
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    files = _files()
    tag = clean_tag(tag)
    if files and files[-1].stem.endswith(digest):
        if tag:
            set_tag(files[-1], tag)
        return files[-1]  # the same chart cleaned again
    d = recent_dir()
    d.mkdir(parents=True, exist_ok=True)
    # File names sort by this stamp, so it must grow even when the clock doesn't
    # (Windows' clock can return the same value for back-to-back saves).
    stamp = time.time_ns()
    if files:
        try:
            stamp = max(stamp, int(files[-1].stem.split("-")[1]) + 1)
        except (IndexError, ValueError):
            pass
    dest = d / f"chart-{stamp:020d}-{digest}.enc"
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source, "text": text}
    if tag:
        record["tag"] = tag
    secure_store.write_text(dest, json.dumps(record, ensure_ascii=False))
    for old in _files()[:-opts["keep"]]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def load(limit: int | None = None, *, tag: str | None = None) -> list[dict]:
    """Newest-first ``{"ts", "source", "text", "file", "tag"?}``; unreadable files are skipped.

    ``tag`` keeps only the charts carrying that bed tag (case-insensitive).
    """
    from . import secure_store

    want = clean_tag(tag).casefold() if tag else None
    out: list[dict] = []
    for p in reversed(_files()):
        if limit is not None and len(out) >= limit:
            break
        try:
            data = json.loads(secure_store.read_text(p))
            if isinstance(data, dict) and isinstance(data.get("text"), str):
                if want is not None and str(data.get("tag") or "").casefold() != want:
                    continue
                out.append({**data, "file": str(p)})
        except Exception:
            continue  # encrypted with another machine's key, or damaged
    return out


def set_tag(file: str | Path, tag: str) -> bool:
    """Give a stored chart a bed tag ("" removes it)."""
    from . import secure_store

    path = Path(file)
    if path.parent.resolve() != recent_dir().resolve() or not path.exists():
        return False
    try:
        data = json.loads(secure_store.read_text(path))
    except Exception:
        return False
    tag = clean_tag(tag)
    if tag:
        data["tag"] = tag
    else:
        data.pop("tag", None)
    secure_store.write_text(path, json.dumps(data, ensure_ascii=False))
    return True


def tags() -> list[str]:
    """Bed tags in use, most recently used first."""
    seen: dict[str, str] = {}
    for rec in load():
        tag = rec.get("tag")
        if tag and tag.casefold() not in seen:
            seen[tag.casefold()] = tag
    return list(seen.values())


def latest_for(tag: str, *, exclude_text: str | None = None) -> dict | None:
    """The newest stored chart for a bed tag, skipping ``exclude_text`` (today's)."""
    for rec in load(tag=tag):
        if exclude_text is not None and rec["text"] == exclude_text:
            continue
        return rec
    return None


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
