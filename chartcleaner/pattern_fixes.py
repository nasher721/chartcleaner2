"""Known fixes for regex rules shipped in older ``config.json`` files.

``pattern_fixes.json`` lists ``{key, old, new, reason}`` entries. A fix applies
only where a config still holds the exact ``old`` pattern, so a rule the user
edited is never touched; ``new`` = null removes the rule. Pair lists
(``[pattern, replacement]``) keep their replacement.

The Rules page offers :func:`apply` as one click; frozen builds run it as part
of their config migration (``store._migrate_config_schema``).
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

__all__ = ["FIXES_PATH", "known_fixes", "pending", "apply"]

FIXES_PATH = Path(__file__).with_name("pattern_fixes.json")


@lru_cache(maxsize=1)
def known_fixes() -> tuple[dict, ...]:
    try:
        data = json.loads(FIXES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(f for f in data.get("fixes", [])
                 if isinstance(f, dict) and isinstance(f.get("key"), str) and isinstance(f.get("old"), str))


def _pattern(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, (list, tuple)) and entry and isinstance(entry[0], str):
        return entry[0]
    return None


def pending(cfg: dict) -> list[dict]:
    """The fixes that would change ``cfg`` (each with its list ``index``)."""
    out = []
    for fix in known_fixes():
        entries = cfg.get(fix["key"])
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if _pattern(entry) == fix["old"]:
                out.append({**fix, "index": index})
                break
    return out


def apply(cfg: dict) -> tuple[dict, int]:
    """Return ``(fixed copy of cfg, number of fixes applied)``.

    A replacement that would duplicate a rule already in the list removes the
    old rule instead, so applying never leaves the same pattern twice.
    """
    todo = pending(cfg)
    if not todo:
        return cfg, 0
    out = copy.deepcopy(cfg)
    for fix in todo:
        entries = out[fix["key"]]
        index = next((i for i, e in enumerate(entries) if _pattern(e) == fix["old"]), None)
        if index is None:
            continue
        new = fix.get("new")
        if new is None or any(_pattern(e) == new for e in entries):
            del entries[index]
        elif isinstance(entries[index], str):
            entries[index] = new
        else:
            entries[index] = [new, *list(entries[index])[1:]]
    return out, len(todo)
