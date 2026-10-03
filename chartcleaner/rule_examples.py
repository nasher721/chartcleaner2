"""Remember what each learned rule was taught to do, and check it still does.

When a rule is learned from highlighted text, the highlighted text and the
result that rule produced are stored in ``data/rule_examples.jsonl``. The
text is the same text the rule's own pattern already holds in config.json,
so no new chart content is written. :func:`check` replays every example
through the *current* learned rules and reports the ones whose result
changed — for example a later rule that rewrites or undoes an earlier lesson,
or the learned-rules stage being switched off. Examples whose rule was
deleted are skipped (deleting a rule is a deliberate choice).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import store
from .stages import run_regex_pairs

__all__ = ["record", "load", "check", "clear", "examples_path"]


def examples_path() -> Path:
    return store.DATA_DIR / "rule_examples.jsonl"


def _apply(pairs: list[list[str]], text: str, cfg: dict | None = None) -> str:
    options = ((cfg or {}).get("stage_options") or {}).get("learned_rules") or {}
    trial = {"learned_rules": pairs, "stage_options": {"learned_rules": options}}
    return run_regex_pairs(text, trial, None, "learned_rules", sid="learned_rules")[0]


def record(pair: list[str], text: str, cfg: dict | None = None) -> dict:
    """Store one example for ``pair`` (a learned [pattern, replacement])."""
    example = {"pattern": pair[0], "replacement": pair[1], "text": text,
               "expected": _apply([list(pair)], text, cfg)}
    store.ensure_dirs()
    with examples_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(example, ensure_ascii=False) + "\n")
    return example


def load(path: Path | None = None) -> list[dict]:
    path = path or examples_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and {"pattern", "replacement", "text", "expected"} <= set(item):
            out.append(item)
    return out


def check(cfg: dict, examples: list[dict] | None = None) -> dict:
    """``{"checked", "failures": [...], "stage_disabled"}`` for the current rules."""
    examples = load() if examples is None else examples
    rules = [list(p) for p in cfg.get("learned_rules") or []]
    live = {(p[0], p[1]) for p in rules}
    enabled = (((cfg.get("stage_options") or {}).get("learned_rules") or {}).get("enabled", True)
               and "learned_rules" in (cfg.get("stage_order") or ["learned_rules"]))
    failures, checked = [], 0
    for ex in examples:
        if (ex["pattern"], ex["replacement"]) not in live:
            continue
        checked += 1
        now = _apply(rules, ex["text"], cfg) if enabled else ex["text"]
        if now != ex["expected"]:
            failures.append({**ex, "now": now})
    return {"checked": checked, "failures": failures, "stage_disabled": not enabled}


def clear() -> int:
    path = examples_path()
    count = len(load(path))
    if path.exists():
        path.unlink()
    return count
