"""Management and execution of custom Python script rules.

Custom rules are standalone .py scripts placed in a custom rules directory.
Each rule implements::

    clean(text: str, ctx: CleanContext) -> str

Trust model
-----------
A custom rule is arbitrary Python: running one is equivalent to running code
with this user's privileges. Two rules follow from that:

1. **Listing never executes.** ``list_custom_rules`` reads ``LABEL``,
   ``DESCRIPTION``, ``PLACEHOLDER`` and the presence of ``clean()`` with
   :mod:`ast`. Before this, opening the Pipeline page — or merely running a
   clean — imported every script in the folder, so a file dropped there by
   another process ran without the user doing anything.
2. **Execution needs an explicit trust record.** ``apply_custom_rule`` runs a
   script only when its SHA-256 matches the digest recorded in
   ``data/custom_rule_trust.json``. The app writes that digest itself when the
   user creates or saves a script on the Scripts page, so anything authored
   in-app keeps working; a script that appears from anywhere else stays inert
   until the user reviews it and presses Trust. Editing a trusted script
   invalidates the digest, which is the point.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
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


# ---------------------------------------------------------------------------
# trust store — digest of every script the user has approved
# ---------------------------------------------------------------------------

def _trust_path() -> Path:
    """Trust file location; resolved lazily so tests can relocate it."""
    from . import store

    return getattr(store, "CUSTOM_TRUST_FILE", store.DATA_DIR / "custom_rule_trust.json")


def rule_digest(path: str | Path) -> str:
    """SHA-256 of the script bytes, or "" when it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _load_trust() -> dict[str, str]:
    try:
        raw = json.loads(_trust_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}


def _save_trust(entries: dict[str, str]) -> None:
    path = _trust_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a trust write failure must never break the page that triggered it


def is_trusted(path: str | Path) -> bool:
    """True when this exact file content has been approved by the user."""
    path = Path(path)
    digest = rule_digest(path)
    return bool(digest) and _load_trust().get(path.name) == digest


def trust_rule(path: str | Path) -> str:
    """Record the current content of ``path`` as approved; returns the digest."""
    path = Path(path)
    digest = rule_digest(path)
    if not digest:
        raise CustomRuleError(f"Cannot read {path.name} to trust it")
    entries = _load_trust()
    entries[path.name] = digest
    _save_trust(entries)
    return digest


def untrust_rule(path: str | Path) -> None:
    """Forget the approval for ``path`` (it will not run again until re-trusted)."""
    entries = _load_trust()
    if entries.pop(Path(path).name, None) is not None:
        _save_trust(entries)


def seed_trust_if_missing(custom_dir: str | Path | None) -> None:
    """One-time migration: approve the example scripts that ship with the app.

    Existing installs upgrade without their example rules silently stopping.
    This runs only when no trust file exists yet; from then on, editing an
    example revokes its approval like any other script.
    """
    if custom_dir is None:
        return
    path = _trust_path()
    if path.exists():
        return
    directory = Path(custom_dir)
    if not directory.is_dir():
        return
    entries: dict[str, str] = {}
    for candidate in sorted(directory.glob("example_*.py")):
        digest = rule_digest(candidate)
        if digest:
            entries[candidate.name] = digest
    if entries:
        _save_trust(entries)


# ---------------------------------------------------------------------------
# static metadata — parsed, never executed
# ---------------------------------------------------------------------------

def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def _assignments(tree: ast.Module) -> dict[str, Any]:
    """Module-level ``NAME = literal`` values, read without running code."""
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = _literal(node.value)
            if value is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                value = _literal(node.value)
                if value is not None:
                    found[node.target.id] = value
    return found


def _defines_clean(tree: ast.Module) -> bool:
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "clean"
        for node in tree.body
    )


def _static_metadata(path: Path) -> dict:
    """Read a script's metadata without importing it."""
    meta: dict = {
        "label": path.stem,
        "description": "",
        "placeholder": False,
        "has_clean": False,
        "error": None,
    }
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        meta["error"] = "cannot read the file (not UTF-8 text?)"
        return meta
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as ex:
        meta["error"] = f"SyntaxError: line {ex.lineno}: {ex.msg}"
        return meta
    except (ValueError, MemoryError, RecursionError):
        meta["error"] = "cannot parse the file"
        return meta

    values = _assignments(tree)
    label = values.get("LABEL")
    if isinstance(label, str) and label.strip():
        meta["label"] = label.strip()
    description = values.get("DESCRIPTION")
    if isinstance(description, str):
        meta["description"] = description.strip()
    meta["placeholder"] = values.get("PLACEHOLDER") is True
    meta["has_clean"] = _defines_clean(tree)
    return meta


def list_custom_rules(custom_dir: str | Path | None) -> list[dict]:
    """Metadata for every script in ``custom_dir``, sorted by name.

    Nothing is imported here — see the trust model in the module docstring.
    """
    if custom_dir is None:
        return []
    directory = Path(custom_dir)
    if not directory.is_dir():
        return []

    out: list[dict] = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        meta = _static_metadata(path)
        if meta["error"] is None and not meta["has_clean"]:
            # A parse error is the more useful message; don't overwrite it.
            meta["error"] = "Script has no clean(text, ctx) function"
        out.append({
            "name": path.stem,
            "file": str(path),
            "label": meta["label"],
            "description": meta["description"],
            "placeholder": meta["placeholder"],
            "error": meta["error"],
            "trusted": is_trusted(path),
        })
    return out


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def load_custom_module(path: Path, *, require_trust: bool = True) -> Any:
    """Import a custom rule script by path.

    ``require_trust`` is on by default: the only callers that pass False are
    tests exercising the loader itself. Everything that runs a rule against a
    chart goes through :func:`apply_custom_rule`, which checks trust first.
    """
    path = Path(path)
    if require_trust and not is_trusted(path):
        raise CustomRuleError(
            f"{path.name} is not trusted — review it on the Scripts page and press Trust"
        )
    name = f"chartcleaner_custom_{path.stem}"
    sys.modules.pop(name, None)
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CustomRuleError(f"Cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    if meta.get("placeholder"):
        return text, 0, {}, "script is still a placeholder (PLACEHOLDER = True)"
    # Re-check at execution time: metadata may predate an edit, and trust is a
    # property of the bytes on disk, not of when the pipeline was built.
    path = Path(meta["file"])
    if not is_trusted(path):
        return text, 0, {}, f"{path.name} is not trusted — approve it on the Scripts page"

    mod = load_custom_module(path)
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
