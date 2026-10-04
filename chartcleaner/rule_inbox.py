"""Rule suggestions that come to you ("rule inbox").

:mod:`rule_miner` (repeated EMR chrome lines) and :mod:`phrase_miner` (long
phrases worth abbreviating) used to run only when asked. :func:`suggestions`
runs both over the recent charts (:mod:`recent_charts`) and returns items like

    "Printed on <DATE> by <NUM>" appeared in 9 of your last 10 charts — remove it?

Safety filters:

* a line containing any clinical fact (numbers with units, lab/vital values,
  drug names, allergy/code-status words — see :mod:`fact_check`) is never
  suggested for removal;
* lines your current rules already remove are skipped;
* lines you deleted from results three or more times (:mod:`edit_log`) are
  suggested too, through the same filters;
* dismissed suggestions never come back (ids in ``data/inbox_state.json`` —
  hashes only, no chart text; dismissed abbreviation phrases also go to
  ``abbreviations.rejected_suggestions`` so the abbreviation editor agrees).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from . import store

__all__ = ["InboxItem", "suggestions", "dismiss", "dismissed", "accept"]

MIN_CHARTS = 3       # need at least this many recent charts before suggesting anything
MIN_SHARE = 0.5      # a chrome line must appear in at least half of them


@dataclass
class InboxItem:
    id: str
    kind: str            # "remove_line" | "abbreviation"
    title: str
    detail: str
    pattern: str = ""    # remove_line: regex for emr_line_metadata
    phrase: str = ""     # abbreviation: the long phrase
    suggestion: str = ""  # abbreviation: proposed short form
    samples: list[str] = field(default_factory=list)
    charts: int = 0
    of: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _item_id(kind: str, key: str) -> str:
    return hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:16]


def dismissed() -> set[str]:
    try:
        data = json.loads(store.INBOX_STATE_FILE.read_text(encoding="utf-8"))
        return {str(x) for x in data.get("dismissed", [])} if isinstance(data, dict) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def dismiss(item_id: str) -> None:
    ids = dismissed() | {item_id}
    store.ensure_dirs()
    store.INBOX_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    store.INBOX_STATE_FILE.write_text(json.dumps({"dismissed": sorted(ids)}) + "\n",
                                      encoding="utf-8")


def _has_clinical_fact(line: str) -> bool:
    from .fact_check import fact_counts
    try:
        return bool(fact_counts(line))
    except Exception:
        return True  # when unsure, never suggest removing it


def _already_removed(line: str, cfg: dict) -> bool:
    from .rule_preview import _run_stage
    for sid in ("metadata_lines", "boilerplate", "learned_rules"):
        key = {"metadata_lines": "emr_line_metadata", "boilerplate": "boilerplate",
               "learned_rules": "learned_rules"}[sid]
        if not cfg.get(key):
            continue
        try:
            if _run_stage(sid, line + "\n", cfg).strip() != line.strip():
                return True
        except Exception:
            continue
    return False


def _is_heading_line(line: str, cfg: dict) -> bool:
    """"Subjective: …", "Assessment and Plan: …" — a clinical section, never chrome."""
    head = line.split(":", 1)[0].strip().casefold() if ":" in line else ""
    headers = {str(h).strip().rstrip(":").casefold() for h in cfg.get("clinical_headers") or []
               if isinstance(h, str)}
    return bool(head) and head in headers


def _pattern_ok(pattern: str, samples: list[str]) -> bool:
    try:
        rx = re.compile(pattern)
    except re.error:
        return False
    return all(rx.search(s) for s in samples)


def suggestions(cfg: dict, texts: list[str], *, limit: int = 8,
                hidden: set[str] | None = None) -> list[InboxItem]:
    """Ranked suggestions for ``texts`` (newest first) under the current ``cfg``."""
    texts = [t for t in texts if t and t.strip()]
    hidden = dismissed() if hidden is None else hidden
    items: list[InboxItem] = _from_edits(cfg, hidden)
    if len(texts) < MIN_CHARTS:
        return items[:limit]

    from .rule_miner import mine_chrome_rules
    need = max(MIN_CHARTS, int(len(texts) * MIN_SHARE + 0.999))
    for cand in mine_chrome_rules(texts, min_occurrence=need):
        iid = _item_id("remove_line", cand.pattern)
        if iid in hidden or not cand.sample_matches or any(i.id == iid for i in items):
            continue
        if any(_has_clinical_fact(s) or _is_heading_line(s, cfg) for s in cand.sample_matches):
            continue
        if _already_removed(cand.sample_matches[0], cfg):
            continue
        if not _pattern_ok(cand.pattern, cand.sample_matches):
            continue
        sample = cand.sample_matches[0]
        items.append(InboxItem(
            id=iid, kind="remove_line",
            title=f"“{sample[:80]}” appeared in {cand.frequency} of your last {len(texts)} charts",
            detail="Remove lines like this on every clean?",
            pattern=cand.pattern, samples=list(cand.sample_matches),
            charts=cand.frequency, of=len(texts)))

    from .phrase_miner import mine
    for s in mine(texts, cfg, min_count=max(3, len(texts) // 2), limit=10):
        iid = _item_id("abbreviation", s["phrase"])
        if iid in hidden:
            continue
        items.append(InboxItem(
            id=iid, kind="abbreviation",
            title=f"“{s['phrase']}” came up {s['count']} times in your recent charts",
            detail=f"Abbreviate it as “{s['suggestion']}”?",
            phrase=s["phrase"], suggestion=s["suggestion"], charts=s["count"], of=len(texts)))

    items.sort(key=lambda i: (-(i.charts / max(1, i.of)), i.kind != "remove_line"))
    return items[:limit]


def _from_edits(cfg: dict, hidden: set[str]) -> list[InboxItem]:
    """Line shapes you deleted from results again and again (see edit_log)."""
    from . import edit_log
    out: list[InboxItem] = []
    try:
        found = edit_log.candidates()
    except Exception:
        return out
    for cand in found:
        iid = _item_id("remove_line", cand["pattern"])
        sample = cand["sample"]
        if iid in hidden or not sample:
            continue
        if _has_clinical_fact(sample) or _is_heading_line(sample, cfg) or _already_removed(sample, cfg):
            continue
        if not _pattern_ok(cand["pattern"], [sample]):
            continue
        out.append(InboxItem(
            id=iid, kind="remove_line",
            title=f"You deleted “{sample[:80]}” from {cand['count']} results",
            detail="Remove lines like this on every clean?",
            pattern=cand["pattern"], samples=[sample], charts=cand["count"], of=cand["count"]))
    return out


def accept(cfg: dict, item: InboxItem, *, replacement: str | None = None,
           acknowledged: bool = False) -> dict:
    """A copy of ``cfg`` with ``item`` applied (caller validates and saves)."""
    import copy
    out = copy.deepcopy(cfg)
    if item.kind == "remove_line":
        rules = out.setdefault("emr_line_metadata", [])
        if item.pattern not in rules:
            rules.append(item.pattern)
        return out
    if item.kind == "abbreviation":
        from .abbreviations import with_custom
        return with_custom(out, item.phrase, (replacement or item.suggestion).strip(),
                           acknowledged=acknowledged)
    raise ValueError(f"Unknown inbox item kind {item.kind!r}")


def reject_phrase(cfg: dict, phrase: str) -> dict:
    """``cfg`` with ``phrase`` added to the abbreviation editor's dismissed list."""
    import copy
    from .abbreviations import normalize_settings
    out = copy.deepcopy(cfg)
    group = normalize_settings(out.get("abbreviations"))
    if phrase not in group.get("rejected_suggestions", []):
        group["rejected_suggestions"] = list(group.get("rejected_suggestions", [])) + [phrase]
    out["abbreviations"] = normalize_settings(group)
    return out
