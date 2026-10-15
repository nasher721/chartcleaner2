"""Regex stage runners (split from ``stages.py``).

Whole-line deletion (EMR metadata), block deletion (boilerplate), structured
PHI pairs and the learned highlight-rules share one shape: "delete what the
pattern matches" is a pair with an empty replacement.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .stage_core import _apply_rules

if TYPE_CHECKING:
    from .engine import CleanContext

__all__ = ["run_regex_list", "run_regex_pairs", "run_learned"]


def run_regex_list(
    text: str, cfg: dict, ctx: CleanContext, key: str, flags: int, sid: str | None = None
) -> tuple[str, int, dict]:
    """Execute a list of regex patterns and delete matches."""
    return _apply_rules(text, cfg, ctx, [(p, "") for p in cfg[key]], flags, sid)


def run_regex_pairs(
    text: str, cfg: dict, ctx: CleanContext, key: str, phi: bool = False, sid: str | None = None
) -> tuple[str, int, dict]:
    """Execute a list of [pattern, replacement] pairs."""
    rules = [(p, r) for p, r in (cfg.get(key) or [])]
    return _apply_rules(text, cfg, ctx, rules, re.IGNORECASE, sid, phi=phi)


def run_learned(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Apply the rules learned from text selections on the Clean page.

    Each entry is [pattern, replacement]; an empty replacement removes the
    match. A missing or empty list is a no-op (older configs don't have it).
    """
    if not (cfg.get("learned_rules") or []):
        return text, 0, {}
    return run_regex_pairs(text, cfg, ctx, "learned_rules", sid="learned_rules")
