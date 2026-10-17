"""Per-rule settings for learned rules, kept beside the ``[pattern, replacement]`` pairs.

``learned_rules`` stays a plain list of pairs (rule files, the validator and
every older build read it as is). What a pair *means* and how it is managed
lives in ``stage_options.learned_rules.rule_meta``, keyed by the rule id
(:func:`stage_core.rule_id`, a hash of the pattern)::

    "rule_meta": {"3f9a0c21be": {
        "shape": {...},            # rule_shapes shape it was built from
        "enabled": false,          # paused: kept, but not applied
        "archived": true,          # paused by the dead-rule cleanup
        "scope": {"mode": "only", "sections": ["assessment_and_plan"]},
        "note": "Epic MyChart footer", "tags": ["epic"],
        "source": "selection", "added": "2026-10-17T09:12:00"}}

A pair without an entry is an ordinary enabled rule, so old configs behave as
before. Entries whose pattern is gone are dropped by :func:`prune`.
"""

from __future__ import annotations

import copy
import time

from .stage_core import rule_id

__all__ = ["FIELDS", "SOURCES", "SOURCE_LABELS", "active", "add_rule", "describe_pair", "entry",
           "is_enabled", "label", "meta_map", "prune", "set_enabled", "update"]

FIELDS = ("shape", "enabled", "archived", "scope", "note", "tags", "source", "added")
SOURCES = ("selection", "quick_add", "builder", "inbox", "import", "editor", "cli", "api")
SOURCE_LABELS = {"selection": "Highlighted on Clean", "quick_add": "Quick add", "builder": "Rule builder",
                 "inbox": "Suggestion", "import": "Imported", "editor": "Rules page", "cli": "Command line",
                 "api": "Integration", "batch": "Collected selections"}


def describe_pair(pair: list[str], meta: dict | None = None) -> str:
    """A readable sentence for a learned rule (its shape, a literal, or the regex)."""
    from .highlight_rules import describe_rule
    from .rule_shapes import describe
    shape = (meta or {}).get("shape")
    if isinstance(shape, dict):
        return describe(shape)
    literal = describe_rule(list(pair))
    if literal:
        text = f"“{literal['text'][:60]}”"
        return f"Replace {text} with “{literal['replacement'][:40]}”" if literal["replacement"] \
            else f"Remove {text}"
    return f"Regex {pair[0][:60]}"


def _options(cfg: dict) -> dict:
    so = cfg.get("stage_options")
    opts = so.get("learned_rules") if isinstance(so, dict) else None
    return opts if isinstance(opts, dict) else {}


def meta_map(cfg: dict) -> dict[str, dict]:
    meta = _options(cfg).get("rule_meta")
    return meta if isinstance(meta, dict) else {}


def entry(cfg: dict, pattern: str) -> dict:
    """The settings for ``pattern`` (an empty dict when it has none)."""
    found = meta_map(cfg).get(rule_id(pattern))
    return dict(found) if isinstance(found, dict) else {}


def label(cfg: dict, pair: list[str]) -> str:
    """:func:`describe_pair` using the settings saved for ``pair`` in ``cfg``."""
    return describe_pair(pair, entry(cfg, pair[0]))


def is_enabled(cfg: dict, pattern: str) -> bool:
    return entry(cfg, pattern).get("enabled", True) is not False


def _pairs(cfg: dict) -> list[list[str]]:
    return [list(p) for p in cfg.get("learned_rules") or []
            if isinstance(p, (list, tuple)) and len(p) == 2]


def active(cfg: dict) -> tuple[list[tuple[str, str]], dict[str, dict]]:
    """``(enabled pairs in order, {pattern: scope})`` for the learned-rules stage."""
    meta = meta_map(cfg)
    pairs, scopes = [], {}
    for pattern, replacement in _pairs(cfg):
        info = meta.get(rule_id(pattern))
        if isinstance(info, dict):
            if info.get("enabled", True) is False:
                continue
            scope = info.get("scope")
            if isinstance(scope, dict) and scope.get("mode") in ("only", "except") and scope.get("sections"):
                scopes[pattern] = scope
        pairs.append((pattern, replacement))
    return pairs, scopes


def _with_meta(cfg: dict, meta: dict) -> dict:
    out = dict(cfg)
    so = dict(out.get("stage_options") or {})
    opts = dict(so.get("learned_rules") or {})
    if meta:
        opts["rule_meta"] = meta
    else:
        opts.pop("rule_meta", None)
    so["learned_rules"] = opts
    out["stage_options"] = so
    return out


def update(cfg: dict, pattern: str, **fields) -> dict:
    """Copy of ``cfg`` with ``fields`` merged into ``pattern``'s settings.

    A field set to ``None`` is removed; an entry left empty is dropped.
    """
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"Unknown rule setting(s): {', '.join(sorted(unknown))}")
    meta = copy.deepcopy(meta_map(cfg))
    rid = rule_id(pattern)
    current = dict(meta.get(rid) or {})
    for key, value in fields.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    if current.get("enabled") is True:
        current.pop("enabled")  # enabled is the default; keep entries small
        current.pop("archived", None)
    if current:
        meta[rid] = current
    else:
        meta.pop(rid, None)
    return _with_meta(cfg, meta)


def set_enabled(cfg: dict, patterns: list[str], enabled: bool, *, archived: bool = False) -> dict:
    out = cfg
    for pattern in patterns:
        out = update(out, pattern, enabled=enabled, archived=(True if archived and not enabled else None))
    return out


def prune(cfg: dict) -> dict:
    """Drop settings whose rule is no longer in ``learned_rules``."""
    live = {rule_id(p) for p, _r in _pairs(cfg)}
    meta = meta_map(cfg)
    kept = {k: v for k, v in meta.items() if k in live}
    return cfg if kept == meta else _with_meta(cfg, copy.deepcopy(kept))


def add_rule(cfg: dict, pair: list[str], *, shape: dict | None = None, source: str | None = None,
             scope: dict | None = None, note: str | None = None,
             tags: list[str] | None = None) -> tuple[dict, bool]:
    """Copy of ``cfg`` with ``pair`` appended (if new) and its settings recorded.

    Returns ``(config, added)``. A pattern that already has a *different*
    replacement raises ``ValueError`` — edit that rule instead. The
    learned-rules stage is switched on.
    """
    pair = list(pair)
    rules = _pairs(cfg)
    for existing in rules:
        if existing[0] == pair[0] and existing[1] != pair[1]:
            raise ValueError("This text already has a different rule. Edit it in My text rules.")
    added = pair not in rules
    out = dict(cfg)
    if added:
        rules.append(pair)
    out["learned_rules"] = rules
    so = dict(out.get("stage_options") or {})
    so["learned_rules"] = {**(so.get("learned_rules") or {}), "enabled": True}
    out["stage_options"] = so
    fields: dict = {"enabled": True}
    if shape is not None:
        fields["shape"] = shape
    if scope:
        fields["scope"] = scope
    if note:
        fields["note"] = note
    if tags:
        fields["tags"] = list(tags)
    if added:
        if source:
            fields["source"] = source
        fields["added"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = update(out, pair[0], **fields)
    return out, added
