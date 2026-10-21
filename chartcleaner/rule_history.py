"""A journal of rule changes, so any recent one can be undone on its own.

Every time ``config.json`` is saved (:func:`engine.save_config`), :func:`record`
compares the rule-bearing parts of the old and new config and, when they
differ, appends one entry::

    {"id": "…", "ts": "2026-10-17T09:12:00", "source": "selection",
     "summary": "Added: Remove “Printed on”",
     "changes": [{"kind": "learned", "op": "add", "pair": [...], "index": 12}]}

Changes are recorded per item, not as whole-config snapshots, so
:func:`revert` undoes *one* entry even after later, unrelated edits: removing
the rule it added, putting back the rule it deleted, restoring the
abbreviation or the "never remove" text it changed. That is what makes the
Clean page's undo multi-step and the Rules page's "Recent rule changes" list
work.

What is tracked: ``learned_rules`` pairs, their settings
(``stage_options.learned_rules.rule_meta``), custom and disabled
abbreviations, "never remove" exceptions, and the learned-rules /
medical-abbreviations stage switches when they change together with a rule.

Rules hold chart text (a highlighted phrase is the pattern), so the journal is
encrypted with :mod:`secure_store` in ``data/rule_history.enc``, keeps at most
``MAX_ENTRIES`` entries and is removed by *Delete stored chart data now*.
"""

from __future__ import annotations

import copy
import json
import secrets
import time

from . import store

__all__ = ["MAX_ENTRIES", "STAGE_SWITCHES", "clear", "diff", "entries", "get", "mark_reverted",
           "record", "revert", "summarize"]

MAX_ENTRIES = 300
STAGE_SWITCHES = ("learned_rules", "medical_abbreviations")
EXCEPTION_STAGES = ("metadata_lines", "boilerplate", "learned_rules")


# ---- reading the rule-bearing parts of a config ------------------------------

def _pairs(cfg: dict) -> list[list[str]]:
    return [list(p) for p in (cfg or {}).get("learned_rules") or []
            if isinstance(p, (list, tuple)) and len(p) == 2]


def _opts(cfg: dict, sid: str) -> dict:
    so = (cfg or {}).get("stage_options")
    opts = so.get(sid) if isinstance(so, dict) else None
    return opts if isinstance(opts, dict) else {}


def _meta(cfg: dict) -> dict:
    meta = _opts(cfg, "learned_rules").get("rule_meta")
    return meta if isinstance(meta, dict) else {}


def _abbr(cfg: dict) -> tuple[dict[str, dict], list[str]]:
    group = (cfg or {}).get("abbreviations")
    if not isinstance(group, dict):
        return {}, []
    custom = {c["term"].casefold(): c for c in group.get("custom") or []
              if isinstance(c, dict) and isinstance(c.get("term"), str)}
    disabled = [d for d in group.get("disabled") or [] if isinstance(d, str)]
    return custom, disabled


def _exceptions(cfg: dict, sid: str) -> list[str]:
    values = _opts(cfg, sid).get("exceptions")
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def _contracts(cfg: dict) -> dict[str, dict]:
    group = (cfg or {}).get("rule_contracts")
    entries = group.get("contracts") if isinstance(group, dict) else None
    return {k: v for k, v in entries.items() if isinstance(v, dict)} if isinstance(entries, dict) else {}


def diff(old: dict, new: dict) -> list[dict]:
    """The rule changes between two configs, item by item."""
    changes: list[dict] = []
    before, after = _pairs(old), _pairs(new)
    for i, pair in enumerate(before):
        if pair not in after:
            changes.append({"kind": "learned", "op": "remove", "pair": pair, "index": i})
    for i, pair in enumerate(after):
        if pair not in before:
            changes.append({"kind": "learned", "op": "add", "pair": pair, "index": i})
    mb, ma = _meta(old), _meta(new)
    for rid in sorted(set(mb) | set(ma)):
        if mb.get(rid) != ma.get(rid):
            changes.append({"kind": "meta", "id": rid, "before": copy.deepcopy(mb.get(rid)),
                            "after": copy.deepcopy(ma.get(rid))})
    kb, ka = _contracts(old), _contracts(new)
    for key in sorted(set(kb) | set(ka)):
        if kb.get(key) != ka.get(key):
            changes.append({"kind": "contract", "key": key, "before": copy.deepcopy(kb.get(key)),
                            "after": copy.deepcopy(ka.get(key))})
    (cb, db), (ca, da) = _abbr(old), _abbr(new)
    abbr_changes_from = len(changes)
    for key in sorted(set(cb) | set(ca)):
        if cb.get(key) != ca.get(key):
            changes.append({"kind": "abbreviation", "term": (ca.get(key) or cb.get(key))["term"],
                            "before": copy.deepcopy(cb.get(key)), "after": copy.deepcopy(ca.get(key))})
    for term in db:
        if term not in da:
            changes.append({"kind": "abbr_disabled", "op": "remove", "term": term})
    for term in da:
        if term not in db:
            changes.append({"kind": "abbr_disabled", "op": "add", "term": term})
    if len(changes) > abbr_changes_from and "abbreviations" not in (old or {}):
        # undone last (revert walks backwards): drops the group it created
        changes.insert(abbr_changes_from, {"kind": "abbr_group", "before": None})
    for sid in EXCEPTION_STAGES:
        eb, ea = _exceptions(old, sid), _exceptions(new, sid)
        for text in eb:
            if text not in ea:
                changes.append({"kind": "exception", "sid": sid, "op": "remove", "text": text})
        for text in ea:
            if text not in eb:
                changes.append({"kind": "exception", "sid": sid, "op": "add", "text": text})
    if changes:  # a stage switch counts only alongside a rule change
        for sid in STAGE_SWITCHES:
            b, a = _opts(old, sid).get("enabled"), _opts(new, sid).get("enabled")
            if b != a:
                changes.append({"kind": "stage", "sid": sid, "before": b, "after": a})
    return changes


# ---- describing ----------------------------------------------------------------

def _rule_label(pair: list[str], meta: dict | None = None) -> str:
    from .rule_meta import describe_pair
    return describe_pair(pair, meta)


def summarize(changes: list[dict], new_meta: dict | None = None) -> str:
    from .stage_core import rule_id
    parts: list[str] = []
    meta = new_meta or {}
    added = [c for c in changes if c["kind"] == "learned" and c["op"] == "add"]
    removed = [c for c in changes if c["kind"] == "learned" and c["op"] == "remove"]
    for c in added[:2]:
        parts.append("Added: " + _rule_label(c["pair"], meta.get(rule_id(c["pair"][0]))))
    for c in removed[:2]:
        parts.append("Deleted: " + _rule_label(c["pair"]))
    extra = len(added) + len(removed) - min(2, len(added)) - min(2, len(removed))
    if extra > 0:
        parts.append(f"{extra} more rule(s)")
    touched = {c["pair"][0] for c in added + removed}
    meta_changes = [c for c in changes if c["kind"] == "meta"
                    and not any(rule_id(p) == c["id"] for p in touched)]
    paused = sum(1 for c in meta_changes if (c["after"] or {}).get("enabled") is False
                 and (c["before"] or {}).get("enabled", True) is not False)
    resumed = sum(1 for c in meta_changes if (c["before"] or {}).get("enabled") is False
                  and (c["after"] or {}).get("enabled", True) is not False)
    if paused:
        parts.append(f"Paused {paused} rule(s)")
    if resumed:
        parts.append(f"Resumed {resumed} rule(s)")
    if len(meta_changes) > paused + resumed:
        parts.append(f"Edited settings of {len(meta_changes) - paused - resumed} rule(s)")
    for c in [c for c in changes if c["kind"] == "abbreviation"][:3]:
        if c["after"] is None:
            parts.append(f"Removed abbreviation “{c['term']}”")
        else:
            verb = "Added" if c["before"] is None else "Changed"
            parts.append(f"{verb} abbreviation “{c['term']}” → “{c['after'].get('replacement', '')}”")
    abbr_n = sum(1 for c in changes if c["kind"] == "abbreviation")
    if abbr_n > 3:
        parts.append(f"{abbr_n - 3} more abbreviation(s)")
    off = sum(1 for c in changes if c["kind"] == "abbr_disabled" and c["op"] == "add")
    on = sum(1 for c in changes if c["kind"] == "abbr_disabled" and c["op"] == "remove")
    if off:
        parts.append(f"Turned off {off} abbreviation(s)")
    if on:
        parts.append(f"Turned on {on} abbreviation(s)")
    keep = {c["text"] for c in changes if c["kind"] == "exception" and c["op"] == "add"}
    drop = {c["text"] for c in changes if c["kind"] == "exception" and c["op"] == "remove"}
    for text in list(keep)[:2]:
        parts.append(f"Never remove “{text[:50]}”")
    if drop:
        parts.append(f"Removed {len(drop)} “never remove” text(s)")
    contracts = [c for c in changes if c["kind"] == "contract"]
    if contracts:
        added = sum(1 for c in contracts if c["before"] is None)
        removed = sum(1 for c in contracts if c["after"] is None)
        edited = len(contracts) - added - removed
        bits = [f"{n} {verb}" for n, verb in ((added, "added"), (edited, "edited"), (removed, "removed")) if n]
        parts.append("Rule contract(s): " + ", ".join(bits))
    return "; ".join(parts) or "Rule settings changed"


# ---- storage ----------------------------------------------------------------------

def _load() -> list[dict]:
    from . import secure_store
    try:
        data = json.loads(secure_store.read_text(store.RULE_HISTORY_FILE))
        return [e for e in data.get("entries", []) if isinstance(e, dict)] if isinstance(data, dict) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    from . import secure_store
    store.ensure_dir(store.RULE_HISTORY_FILE.parent)
    secure_store.write_text(store.RULE_HISTORY_FILE,
                            json.dumps({"entries": items[-MAX_ENTRIES:]}, ensure_ascii=False))


def record(old: dict | None, new: dict, source: str | None = None) -> dict | None:
    """Journal the rule changes from ``old`` to ``new``; ``None`` when there are none."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    changes = diff(old, new)
    if not changes:
        return None
    entry = {"id": secrets.token_hex(6), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "source": source or "app", "summary": summarize(changes, _meta(new)),
             "changes": changes}
    items = _load()
    items.append(entry)
    _save(items)
    return entry


def entries(limit: int | None = None) -> list[dict]:
    """Newest first."""
    items = list(reversed(_load()))
    return items[:limit] if limit else items


def get(entry_id: str) -> dict | None:
    return next((e for e in _load() if e.get("id") == entry_id), None)


def mark_reverted(entry_id: str, by: str | None = None) -> None:
    items = _load()
    for e in items:
        if e.get("id") == entry_id:
            e["reverted"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if by:
                e["reverted_by"] = by
    _save(items)


def clear() -> int:
    n = len(_load())
    if store.RULE_HISTORY_FILE.exists():
        store.RULE_HISTORY_FILE.unlink()
    return n


# ---- undo --------------------------------------------------------------------------

def _set_opt(cfg: dict, sid: str, key: str, value) -> None:
    so = cfg.setdefault("stage_options", {})
    opts = so.setdefault(sid, {})
    if value is None:
        opts.pop(key, None)
    else:
        opts[key] = value


def revert(cfg: dict, entry: dict) -> dict:
    """Copy of ``cfg`` with ``entry``'s changes undone (later edits are kept).

    Each change is undone on its own and is a no-op when already undone (the
    rule it added is gone, the rule it deleted is back), so reverting is safe
    after unrelated edits and twice in a row.
    """
    out = copy.deepcopy(cfg)
    for c in reversed(entry.get("changes") or []):
        kind = c.get("kind")
        if kind == "learned":
            rules = _pairs(out)
            pair = list(c["pair"])
            if c["op"] == "add" and pair in rules:
                rules.remove(pair)
            elif c["op"] == "remove" and pair not in rules:
                rules.insert(min(int(c.get("index", len(rules))), len(rules)), pair)
            out["learned_rules"] = rules
        elif kind == "meta":
            meta = dict(_meta(out))
            if c["before"] is None:
                meta.pop(c["id"], None)
            else:
                meta[c["id"]] = copy.deepcopy(c["before"])
            _set_opt(out, "learned_rules", "rule_meta", meta or None)
        elif kind == "abbreviation":
            group = dict(out.get("abbreviations") or {})
            custom = [x for x in group.get("custom") or []
                      if not (isinstance(x, dict) and str(x.get("term", "")).casefold() == c["term"].casefold())]
            if c["before"] is not None:
                custom.append(copy.deepcopy(c["before"]))
            group["custom"] = custom
            out["abbreviations"] = group
        elif kind == "abbr_disabled":
            group = dict(out.get("abbreviations") or {})
            disabled = [d for d in group.get("disabled") or [] if d != c["term"]]
            if c["op"] == "remove":
                disabled.append(c["term"])
            group["disabled"] = disabled
            out["abbreviations"] = group
        elif kind == "exception":
            keep = [t for t in _exceptions(out, c["sid"]) if t != c["text"]]
            if c["op"] == "remove":
                keep.append(c["text"])
            _set_opt(out, c["sid"], "exceptions", keep)
        elif kind == "stage":
            _set_opt(out, c["sid"], "enabled", c["before"])
        elif kind == "contract":
            group = copy.deepcopy(out.get("rule_contracts")) if isinstance(out.get("rule_contracts"), dict) else {}
            entries = dict(group.get("contracts") or {})
            if c["before"] is None:
                entries.pop(c["key"], None)
            else:
                entries[c["key"]] = copy.deepcopy(c["before"])
            if entries:
                group["contracts"] = entries
                group.setdefault("schema_version", 1)
                out["rule_contracts"] = group
            else:
                out.pop("rule_contracts", None)
        elif kind == "abbr_group":
            group = out.get("abbreviations")
            if isinstance(group, dict) and not group.get("custom") and not group.get("disabled"):
                out.pop("abbreviations", None)
    if "learned_rules" in out:
        from .rule_meta import prune
        out = prune(out)
    return out
