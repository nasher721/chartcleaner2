"""The learned rules as a searchable, sortable list (Rules page → Remove & replace).

:func:`rows` joins each ``learned_rules`` pair with its settings
(:mod:`rule_meta`) and its record in run history (:mod:`rule_health`: hits,
last hit, status), so the page can sort by hits or last use, filter by kind,
source, tag or status, and find dead rules. :func:`archive_unused` pauses every
rule that never matched in the recent runs (kept, flagged ``archived``, one
click to bring back). :func:`merge_suggestions` / :func:`merge` fold look-alike
literal rules ("…signed by Jane Doe on 10/14", "…signed by John Smith on
10/15") into one placeholder rule.
"""

from __future__ import annotations

from collections import defaultdict

from .highlight_rules import describe_rule
from .rule_meta import SOURCE_LABELS, add_rule, entry, label, prune, set_enabled
from .rule_shapes import build, describe, generalized_text, normalize, placeholders_in

__all__ = ["SORTS", "STATUSES", "archive_unused", "filter_rows", "merge", "merge_suggestions", "rows",
           "tags"]

SORTS = {"order": "Rule order", "hits": "Most hits", "last_hit": "Least recently used",
         "added": "Newest first", "text": "A → Z"}
STATUSES = ("ok", "never matched", "very broad", "slow", "not enough runs yet", "paused")


def _pairs(cfg: dict) -> list[list[str]]:
    return [list(p) for p in cfg.get("learned_rules") or []
            if isinstance(p, (list, tuple)) and len(p) == 2]


def rows(cfg: dict, runs: list[dict] | None = None) -> list[dict]:
    """One dict per learned rule, in config order."""
    health: dict[str, dict] = {}
    if runs:
        from .rule_health import report
        health = {r["pattern"]: r for r in report(cfg, runs) if r["stage"] == "learned_rules"}
    out = []
    for index, pair in enumerate(_pairs(cfg)):
        info = entry(cfg, pair[0])
        shape = info.get("shape") if isinstance(info.get("shape"), dict) else None
        literal = describe_rule(pair)
        h = health.get(pair[0], {})
        enabled = info.get("enabled", True) is not False
        out.append({
            "index": index, "pair": pair, "pattern": pair[0], "replacement": pair[1],
            "label": label(cfg, pair),
            "text": (shape or literal or {}).get("text", pair[0]),
            "kind": "replace" if pair[1] else "remove",
            "shape_kind": (shape or {}).get("kind", "text" if literal else "regex"),
            "enabled": enabled, "archived": bool(info.get("archived")),
            "scope": info.get("scope"), "note": info.get("note", ""), "tags": list(info.get("tags") or []),
            "source": info.get("source", ""), "source_label": SOURCE_LABELS.get(info.get("source", ""), ""),
            "added": info.get("added", ""),
            "hits": h.get("hits", 0), "runs": h.get("runs", 0), "last_hit": h.get("last_hit", ""),
            "status": h.get("status", "paused" if not enabled else "not enough runs yet"),
            "risks": h.get("risks", []),
        })
    return out


def tags(cfg: dict) -> list[str]:
    found = {t for r in rows(cfg) for t in r["tags"]}
    return sorted(found, key=str.casefold)


def filter_rows(items: list[dict], *, query: str = "", kind: str = "all", source: str = "all",
                tag: str = "all", status: str = "all", sort: str = "order") -> list[dict]:
    q = (query or "").strip().casefold()
    out = []
    for r in items:
        if kind != "all" and r["kind"] != kind:
            continue
        if source != "all" and r["source"] != source:
            continue
        if tag != "all" and tag not in r["tags"]:
            continue
        if status == "paused" and r["enabled"]:
            continue
        if status not in ("all", "paused") and r["status"] != status:
            continue
        if q and q not in " ".join([r["label"], r["text"], r["replacement"], r["note"],
                                    " ".join(r["tags"])]).casefold():
            continue
        out.append(r)
    if sort == "hits":
        out.sort(key=lambda r: (-r["hits"], r["index"]))
    elif sort == "last_hit":
        out.sort(key=lambda r: (r["last_hit"] or "", r["index"]))
    elif sort == "added":
        out.sort(key=lambda r: (r["added"] or "", r["index"]), reverse=True)
    elif sort == "text":
        out.sort(key=lambda r: r["text"].casefold())
    return out


def archive_unused(cfg: dict, runs: list[dict]) -> tuple[dict, list[str]]:
    """Pause (and flag ``archived``) every enabled rule that never matched recently."""
    dead = [r["pattern"] for r in rows(cfg, runs) if r["enabled"] and r["status"] == "never matched"]
    return (set_enabled(cfg, dead, False, archived=True) if dead else cfg), dead


# ---- merging look-alike rules ------------------------------------------------------

def merge_suggestions(cfg: dict, *, min_rules: int = 2) -> list[dict]:
    """Groups of literal removal rules that one placeholder rule would cover.

    ``[{"shape", "label", "patterns", "texts"}]`` — the shape is built with
    the same placeholders :func:`rule_shapes.generalize` uses.
    """
    groups: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for pair in _pairs(cfg):
        if pair[1]:
            continue
        info = entry(cfg, pair[0])
        if info.get("enabled", True) is False:
            continue
        shape = info.get("shape")
        if isinstance(shape, dict):
            if shape.get("template") or shape.get("kind") not in ("text", "line"):
                continue
            text, kind, case = shape["text"], shape["kind"], bool(shape.get("case_sensitive"))
            whole = bool(shape.get("whole_words", True))
        else:
            literal = describe_rule(pair)
            if literal is None:
                continue
            text, kind, case, whole = (literal["text"], "text", literal["case_sensitive"],
                                       literal["whole_words"])
        if "\n" in text:
            continue
        template = generalized_text(text, names=True)
        if template == text or not placeholders_in(template):
            continue
        groups[(template, kind, case, whole)].append((pair[0], text))
    out = []
    for (template, kind, case, whole), members in groups.items():
        if len(members) < min_rules:
            continue
        shape = normalize({"kind": kind, "text": template, "template": True,
                           "case_sensitive": case, "whole_words": whole})
        out.append({"shape": shape, "label": describe(shape),
                    "patterns": [m[0] for m in members], "texts": [m[1] for m in members]})
    return sorted(out, key=lambda g: -len(g["patterns"]))


def merge(cfg: dict, suggestion: dict) -> dict:
    """Replace the suggestion's rules with its one placeholder rule (at the first one's place)."""
    patterns = set(suggestion["patterns"])
    rules = _pairs(cfg)
    first = next((i for i, p in enumerate(rules) if p[0] in patterns), len(rules))
    pair = build(suggestion["shape"])
    kept = [p for p in rules if p[0] not in patterns]
    out = prune({**cfg, "learned_rules": kept})
    out, added = add_rule(out, pair, shape=suggestion["shape"], source="editor",
                          note=f"Merged from {len(patterns)} rules")
    if added:
        rules = out["learned_rules"]
        rules.insert(min(first, len(rules) - 1), rules.pop())
    return out
