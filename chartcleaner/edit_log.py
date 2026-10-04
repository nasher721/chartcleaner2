"""Learn from your edits: lines you keep deleting from the output become suggestions.

When you edit a cleaned result before copying it, :func:`record` compares the
result with your edited text and counts every line you deleted, grouped by
its shape (dates, times and numbers generalized, the way :mod:`rule_miner`
does). A line shape deleted on ``MIN_DELETIONS`` separate occasions shows up
in the rule inbox::

    You deleted “Reviewed by <NUM> on <DATE>” 3 times — remove it on every clean?

Safety and privacy:

* lines with any clinical fact (numbers with units, labs, vitals, drugs,
  allergy/code-status words — see :mod:`fact_check`) are never recorded;
* the log is encrypted with :mod:`secure_store` in ``data/edit_log.enc``,
  keeps at most ``MAX_ENTRIES`` shapes, drops entries older than the
  retention setting, and is removed by *Delete stored chart data now*.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter

from . import store

__all__ = ["MIN_DELETIONS", "MAX_ENTRIES", "deleted_lines", "record", "entries", "candidates",
           "forget", "clear"]

MIN_DELETIONS = 3
MAX_ENTRIES = 200
_MIN_LEN, _MAX_LEN = 8, 200


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip()).casefold()


def deleted_lines(before: str, after: str) -> list[str]:
    """Lines of ``before`` that ``after`` no longer has (as many times as removed)."""
    remaining = Counter(_norm(ln) for ln in after.split("\n") if ln.strip())
    gone = []
    for line in before.split("\n"):
        key = _norm(line)
        if not key:
            continue
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
        else:
            gone.append(line.strip())
    return gone


def _load() -> dict:
    from . import secure_store
    try:
        data = json.loads(secure_store.read_text(store.EDIT_LOG_FILE))
        return data if isinstance(data, dict) and isinstance(data.get("entries"), dict) else {"entries": {}}
    except Exception:
        return {"entries": {}}


def _save(data: dict) -> None:
    from . import secure_store
    store.EDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    secure_store.write_text(store.EDIT_LOG_FILE, json.dumps(data, ensure_ascii=False))


def _eligible(line: str) -> bool:
    from .fact_check import fact_counts
    if not _MIN_LEN <= len(line) <= _MAX_LEN:
        return False
    try:
        return not fact_counts(line)
    except Exception:
        return False


def record(before: str, after: str, *, retention_days: float | None = None) -> int:
    """Count the lines deleted between ``before`` (the result) and ``after`` (your edit).

    Returns how many deletions were recorded. Each edit counts a shape once.
    """
    from .rule_miner import _line_template

    shapes: dict[str, str] = {}
    for line in deleted_lines(before, after):
        if _eligible(line):
            shapes.setdefault(_line_template(line), line)
    if not shapes:
        return 0
    data = _load()
    now = time.time()
    if retention_days is None:
        try:
            retention_days = float(store.load_prefs().get("retention_days") or 0)
        except Exception:
            retention_days = 0
    entries = data["entries"]
    if retention_days > 0:
        cutoff = now - retention_days * 86400
        entries = {k: v for k, v in entries.items() if v.get("last", 0) >= cutoff}
    for shape, sample in shapes.items():
        e = entries.setdefault(shape, {"count": 0, "sample": sample, "first": now})
        e["count"] = int(e.get("count", 0)) + 1
        e["last"] = now
        e["sample"] = sample
    if len(entries) > MAX_ENTRIES:
        keep = sorted(entries.items(), key=lambda kv: kv[1].get("last", 0), reverse=True)[:MAX_ENTRIES]
        entries = dict(keep)
    data["entries"] = entries
    _save(data)
    return len(shapes)


def entries() -> dict[str, dict]:
    """shape -> ``{"count", "sample", "first", "last"}``."""
    return _load()["entries"]


def candidates(min_deletions: int = MIN_DELETIONS) -> list[dict]:
    """Shapes deleted at least ``min_deletions`` times: ``{"shape", "pattern", "count", "sample"}``."""
    from .rule_miner import _template_to_regex
    out = []
    for shape, e in entries().items():
        if int(e.get("count", 0)) >= min_deletions:
            out.append({"shape": shape, "pattern": _template_to_regex(shape),
                        "count": int(e["count"]), "sample": e.get("sample", "")})
    return sorted(out, key=lambda c: -c["count"])


def forget(shape: str) -> None:
    data = _load()
    if data["entries"].pop(shape, None) is not None:
        _save(data)


def clear() -> None:
    try:
        store.EDIT_LOG_FILE.unlink()
    except OSError:
        pass
