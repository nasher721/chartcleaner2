"""Management and execution of custom Python script rules.

Custom rules are standalone .py scripts placed in a custom rules directory.
Each rule implements:
    clean(text: str, ctx: CleanContext) -> str
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import CleanContext, StageSpec

CUSTOM_RULE_TEMPLATE = '''"""Custom cleaning rule — edit freely and save.

Contract: implement  clean(text, ctx) -> str  and it becomes a pipeline stage.

Helpers available on `ctx`:
  ctx.count("name", n)  — count something; totals show up in the Stats tracker
  ctx.log("message")    — a note shown in the run details
  ctx.config            — read-only view of config.json

MODULE (top-level) settings:
  LABEL        display name in the app (default: file name)
  DESCRIPTION  one-line explanation
  PLACEHOLDER  keep True while the rule is a stub; it is skipped until False
"""

LABEL = "New custom rule"
DESCRIPTION = "Describe what this rule does."
PLACEHOLDER = True

import re


def clean(text: str, ctx) -> str:
    # Example: collapse runs of 4+ newlines down to two.
    new_text, n = re.subn(r"\\n{4,}", "\\n\\n", text)
    ctx.count("collapses", n)
    return new_text
'''


class CustomRuleError(Exception):
    """Raised when a custom script rule cannot load or run."""


def load_custom_module(path: Path) -> Any:
    """Dynamically import a custom rule python script by path."""
    name = f"chartcleaner_custom_{path.stem}"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CustomRuleError(f"Cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def list_custom_rules(custom_dir: str | Path | None) -> list[dict]:
    """Retrieve metadata for all valid custom rule files, sorted alphabetically."""
    if custom_dir is None:
        return []
    directory = Path(custom_dir)
    if not directory.exists():
        return []

    out: list[dict] = []
    for p in sorted(directory.glob("*.py")):
        if p.name.startswith("_"):
            continue
        meta = {
            "name": p.stem,
            "file": str(p),
            "label": p.stem,
            "description": "",
            "placeholder": False,
            "error": None,
        }
        try:
            mod = load_custom_module(p)
            meta["label"] = str(getattr(mod, "LABEL", p.stem) or p.stem)
            meta["description"] = str(getattr(mod, "DESCRIPTION", "") or "")
            meta["placeholder"] = bool(getattr(mod, "PLACEHOLDER", False))
            if not callable(getattr(mod, "clean", None)):
                meta["error"] = "Script has no clean(text, ctx) function"
        except Exception as e:
            meta["error"] = f"{type(e).__name__}: {e}"
        out.append(meta)
    return out


def apply_custom_rule(
    spec: StageSpec, text: str, ctx: CleanContext
) -> tuple[str, int, dict, str | None]:
    """Execute a single custom rule script against the text.

    Returns:
        tuple of (transformed_text, match_count, details_dict, skip_reason)
    """
    meta = spec.meta
    if meta.get("error"):
        raise CustomRuleError(meta["error"])

    mod = load_custom_module(Path(meta["file"]))
    clean = getattr(mod, "clean", None)
    if not callable(clean):
        raise CustomRuleError("script has no clean(text, ctx) function")
    if getattr(mod, "PLACEHOLDER", False):
        return text, 0, {}, "script is still a placeholder (PLACEHOLDER = True)"

    prev_counters = dict(ctx.counters)
    prev_logs = len(ctx.logs)

    result = clean(text, ctx)
    if not isinstance(result, str):
        raise CustomRuleError("clean() must return a string")

    matches = sum(ctx.counters.values()) - sum(prev_counters.values())
    details = {
        "counters": {
            k: v - prev_counters.get(k, 0)
            for k, v in ctx.counters.items()
            if v - prev_counters.get(k, 0)
        },
        "logs": ctx.logs[prev_logs:],
    }
    return result, matches, details, None
