"""Liveness and readiness for the local app.

``/api/v1/health`` answers "is the process up?" — cheap, no dependencies, and
that is the right question for a supervisor restarting a hung process.

Readiness answers the question support actually asks: "would a clean work right
now, and what is degraded?" It checks the things that fail in the field —
a config.json someone edited by hand, a data directory on a full or read-only
volume, an exports folder that was deleted — and reports each one separately so
a support call can start with a fact instead of a guess.

Nothing here reads chart text, and the report carries booleans and counts only.
"""

from __future__ import annotations

import os
import time
from importlib.util import find_spec
from pathlib import Path

__all__ = ["STARTED_AT", "check", "liveness"]

STARTED_AT = time.time()


def _config_check(config_path: Path | None) -> dict:
    from .engine import load_config
    from . import store

    try:
        load_config(config_path or store.CONFIG_PATH)
    except Exception as ex:  # noqa: BLE001 — every failure mode means "not ready"
        # Class name only: the message can contain a filesystem path.
        return {"ok": False, "detail": type(ex).__name__}
    return {"ok": True}


def _writable_dir(path: Path) -> dict:
    try:
        if not path.is_dir():
            return {"ok": False, "detail": "missing"}
        return {"ok": os.access(path, os.W_OK), "detail": None if os.access(path, os.W_OK) else "read-only"}
    except OSError:
        return {"ok": False, "detail": "unreadable"}


def _rules_check() -> dict:
    from .custom_rules import list_custom_rules
    from . import store

    rules = list_custom_rules(store.CUSTOM_RULES_DIR)
    untrusted = [r for r in rules if not r["trusted"] and not r["placeholder"]]
    # Untrusted scripts are skipped, not fatal: cleaning still runs, minus that rule.
    return {"ok": True, "total": len(rules), "untrusted": len(untrusted),
            "degraded": bool(untrusted)}


def check(config_path: Path | None = None) -> dict:
    """Full readiness report; ``ready`` is true when a clean can complete."""
    from . import __version__, store

    checks = {
        "config": _config_check(config_path),
        "data_dir": _writable_dir(store.DATA_DIR),
        "exports": _writable_dir(store.EXPORTS_DIR),
        "rules": _rules_check(),
        # Optional engine: absent means no NER redaction, not a broken install.
        "nlp": {"ok": find_spec("spacy") is not None
                and find_spec("presidio_analyzer") is not None},
    }
    required = ("config", "data_dir", "exports")
    return {
        "ready": all(checks[name]["ok"] for name in required),
        "version": __version__,
        "uptime_seconds": int(time.time() - STARTED_AT),
        "checks": checks,
    }


def liveness() -> dict:
    from . import __version__

    return {"ok": True, "version": __version__}
