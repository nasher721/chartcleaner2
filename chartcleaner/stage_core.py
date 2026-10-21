"""Shared machinery for the built-in stage runners (split from ``stages.py``).

One owner for pattern compilation, rule ids, scoped/timed application and
change tracking: the same ~170 config rules run on every clean, so they are
compiled and hashed once per process instead of once per run.
"""

from __future__ import annotations

import bisect
import hashlib
import re
import time
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable

from .abbreviations import MAX_TRACKED_CHANGES, LineCounter
from .section_parser import parse_clinical_sections

if TYPE_CHECKING:
    from .engine import CleanContext

__all__ = [
    "MAX_TRACKED_CHANGES",
    "SLOW_RULE_MS",
    "get_stage_options",
    "rule_id",
]


def get_stage_options(cfg: dict, sid: str) -> dict:
    """Return options from config['stage_options'][sid] if present."""
    so = cfg.get("stage_options")
    if not isinstance(so, dict):
        return {}
    opts = so.get(sid)
    return opts if isinstance(opts, dict) else {}


# One owner for pattern compilation and rule ids. The same ~170 config rules
# run on every clean, so they are compiled and hashed once per process instead
# of once per run. Both caches are bounded (a user can edit rules while the app
# is open); a miss just recompiles.
@lru_cache(maxsize=4096)
def _rule_id(pattern: str) -> str:
    return hashlib.sha1(pattern.encode("utf-8")).hexdigest()[:10]


def rule_id(pattern: str) -> str:
    """Stable short id for a rule pattern (used to group per-rule statistics)."""
    return _rule_id(pattern)


@lru_cache(maxsize=4096)
def _compiled(pattern: str, flags: int) -> re.Pattern:
    """Memoized ``re.compile`` so a rule is compiled once, not once per run."""
    return re.compile(pattern, flags)


def _tracking(ctx: Any) -> bool:
    return bool(getattr(ctx, "track_changes", False))


def _ledger_for(ctx: Any, text: str):
    """The run's :class:`~chartcleaner.edit_provenance.EditLedger` if it is in step with ``text``.

    A stage that transformed the text before calling the regex machinery
    leaves the ledger behind; proposing matches against stale offsets would
    mislabel provenance, so the engine's post-stage reconcile covers it instead.
    """
    ledger = getattr(ctx, "ledger", None)
    if ledger is None or ledger.current != text:
        return None
    return ledger


def _subn_tracked(
    regex: re.Pattern, replacement: str, text: str, changes: list[dict] | None, pattern: str,
    exceptions: list[str] | None = None, scope: Callable[[int], bool] | None = None,
    table_lines: set[int] | None = None, *, ledger: Any = None, sid: str | None = None,
    rule_key: str | None = None, judge: Callable[[Any], str] | None = None,
) -> tuple[str, int]:
    """``regex.subn`` that also records each change while ``changes`` is a list.

    A match containing any of ``exceptions`` (case-insensitive) is left as is
    and not counted: the user said "never remove this". ``scope(pos)`` returns
    whether a match at that offset is in scope (section-scoped removal).
    ``table_lines`` is a set of line-start offsets that must be left untouched
    (table-safe whitespace).

    With a ``ledger`` every surviving match becomes an
    :class:`~chartcleaner.edit_provenance.EditProposal` (whatever the display
    cap on ``changes``); ``judge(proposal)`` returns the decision and only
    accepted proposals change the text. All matches of one rule are proposed
    against the same input and resolved as one batch.
    """
    if (changes is None and not exceptions and scope is None and table_lines is None
            and ledger is None):
        return regex.subn(replacement, text)
    rid = rule_id(pattern)
    keep = [e.casefold() for e in exceptions or []]
    skipped = 0
    lines = LineCounter(text)
    proposals: list[tuple[Any, str]] = []

    # Line start offsets, built lazily (only when a "never remove" exception is
    # actually hit) so the common case — no exceptions, or no matches — pays
    # nothing. Replaces the previous per-match ``text.rfind``/``text.find``
    # scan, which was O(len text) for every match (O(matches x len text) on a
    # large chart) with an O(log lines) bisection.
    _line_starts = {"v": None}

    def _line_span(pos: int) -> tuple[int, int]:
        starts = _line_starts["v"]
        if starts is None:
            starts = [0]
            curr = 0
            while True:
                curr = text.find("\n", curr)
                if curr == -1:
                    break
                curr += 1
                starts.append(curr)
            _line_starts["v"] = starts
        idx = bisect.bisect_right(starts, pos) - 1
        start = starts[idx]
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else len(text)
        return start, end

    def repl(match: re.Match) -> str:
        nonlocal skipped
        found = match.group(0)
        if keep:
            # "Never remove" protects the whole line the match sits on, not just
            # the matched fragment (a rule may match a word inside a longer line).
            ls, le = _line_span(match.start())
            line_text = text[ls:le].casefold()
            if any(k in line_text for k in keep):
                skipped += 1
                return found
        if scope is not None and not scope(match.start()):
            skipped += 1
            return found
        if table_lines is not None:
            ls, _ = _line_span(match.start())
            if ls in table_lines:
                skipped += 1
                return found
        after = match.expand(replacement)
        if ledger is not None:
            proposal = ledger.propose(sid or "regex", rule_key or rid, match.start(), match.end(), after)
            decision = judge(proposal) if judge is not None else "accept"
            proposals.append((proposal, decision))
            if decision != "accept":
                skipped += 1
                return found
        if changes is not None and len(changes) < MAX_TRACKED_CHANGES:
            changes.append({
                "rule": pattern,
                "rule_id": rid,
                "before": found,
                "after": after,
                "line": lines.line_at(match.start()),
            })
        return after

    out, n = regex.subn(repl, text)
    if proposals:
        ledger.resolve_many(proposals)
    return out, n - skipped


def _exceptions(cfg: dict, sid: str | None) -> list[str]:
    values = get_stage_options(cfg, sid).get("exceptions") if sid else None
    return [v for v in values if isinstance(v, str) and v.strip()] if isinstance(values, list) else []


def _is_table_line(line: str) -> bool:
    """A line that should keep its internal spacing (pipe/tab or fixed-width)."""
    if "|" in line or "\t" in line:
        return True
    return len(re.findall(r" {2,}", line)) >= 3


def _table_line_starts(text: str) -> set[int]:
    """Offsets of line starts that look like aligned tables / monospace blocks."""
    starts: set[int] = set()
    pos = 0
    for line in text.split("\n"):
        if _is_table_line(line):
            starts.add(pos)
        pos += len(line) + 1
    return starts


def _section_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, domain) for each clinical section parsed from the chart."""
    parsed = parse_clinical_sections(text)
    return [(s.start_pos, s.end_pos, s.domain) for s in parsed.sections]


def _build_scope(cfg: dict, sid: str | None, text: str) -> Callable[[int], bool] | None:
    """Return an in-scope predicate for section-scoped removal, or None.

    Scope lives in ``stage_options.<sid>.scope`` as
    ``{"mode": "only"|"except", "sections": [taxonomy keys]}``. With ``mode``
    "only", a match is kept only inside the listed sections; with "except" it is
    kept everywhere except them. Sections absent from the chart (preamble) count
    as domain "preamble".
    """
    if not sid:
        return None
    return _scope_predicate(get_stage_options(cfg, sid).get("scope"), text)


def _scope_predicate(scope: Any, text: str) -> Callable[[int], bool] | None:
    """``in_scope(pos)`` for one ``{"mode", "sections"}`` scope over ``text``."""
    if not isinstance(scope, dict):
        return None
    mode = scope.get("mode", "all")
    if mode == "all":
        return None
    sections = {str(s).strip().casefold() for s in scope.get("sections", [])}
    if not sections:
        return None
    spans = _section_spans(text)

    def in_scope(pos: int) -> bool:
        domain = "preamble"
        for start, end, dom in spans:
            if start <= pos < end:
                domain = dom
                break
        inside = domain in sections
        return inside if mode == "only" else not inside

    return in_scope


# A rule taking at least this long on one chart lands in details["slow_rules"]
# (rule id -> ms); the Statistics page's rule health reads it.
SLOW_RULE_MS = 25.0


def _timed_subn(regex, replacement, text, changes, pattern, keep, slow: dict[str, float],
                scope: Callable[[int], bool] | None = None,
                table_lines: set[int] | None = None, **provenance):
    started = time.perf_counter()
    out, n = _subn_tracked(regex, replacement, text, changes, pattern, keep, scope, table_lines,
                           **provenance)
    ms = (time.perf_counter() - started) * 1000
    if ms >= SLOW_RULE_MS:
        slow[rule_id(pattern)] = round(ms, 1)
    return out, n


def _with_tracking(details: dict, hits: dict[str, int], changes: list[dict] | None,
                   slow: dict[str, float] | None = None) -> dict:
    if hits:
        details["rule_hits"] = hits
    if slow:
        details["slow_rules"] = slow
    if changes is not None:
        details["changes"] = changes
    return details


def _both(a: Callable[[int], bool] | None, b: Callable[[int], bool] | None):
    if a is None or b is None:
        return a or b
    return lambda pos: a(pos) and b(pos)


def _apply_rules(
    text: str, cfg: dict, ctx: CleanContext, rules: list[tuple[str, str]], flags: int,
    sid: str | None, *, phi: bool = False, rule_scopes: dict[str, dict] | None = None,
) -> tuple[str, int, dict]:
    """Run ``(pattern, replacement)`` rules in order, tracking hits/changes/slow rules.

    Both regex stages share this shape: "delete what the pattern matches" is just
    a pair with an empty replacement, so they only differ in the rule list and
    the flags. ``scope`` (section restriction) and ``preserve_tables`` (leave
    table lines alone) are read from ``stage_options[<sid>]`` when present.
    ``rule_scopes`` (pattern -> scope) narrows single rules further; their
    sections are parsed from the text as it stands when that rule runs.
    """
    if sid and get_stage_options(cfg, sid).get("case_sensitive"):
        flags &= ~re.IGNORECASE
    changes: list[dict] | None = [] if _tracking(ctx) else None
    keep = _exceptions(cfg, sid)
    scope = _build_scope(cfg, sid, text)
    table_lines = (
        _table_line_starts(text) if sid and get_stage_options(cfg, sid).get("preserve_tables") else None
    )
    hits: dict[str, int] = {}
    slow: dict[str, float] = {}
    total = 0
    ledger = _ledger_for(ctx, text)
    judge = getattr(ctx, "judge", None) if ledger is not None else None
    for pattern, replacement in rules:
        own = rule_scopes.get(pattern) if rule_scopes else None
        rule_scope = _both(scope, _scope_predicate(own, text)) if own else scope
        text, n = _timed_subn(_compiled(pattern, flags), replacement, text, changes, pattern,
                              keep, slow, rule_scope, table_lines, ledger=ledger, sid=sid,
                              rule_key=rule_id(pattern), judge=judge)
        if n:
            hits[rule_id(pattern)] = n
        total += n
    details = {"phi": {"pattern_redactions": total}} if phi else {}
    return text, total, _with_tracking(details, hits, changes, slow)
