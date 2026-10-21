"""What a new rule would do beyond the chart it was taught on.

Before a learned rule, a line-removal rule or a "never remove" exception is
saved, :func:`preview` runs *only that rule's stage* — with the rule added — on
the recent charts (:mod:`recent_charts`) and on the stored learned-rule
examples (:mod:`rule_examples`), and reports which lines it would change:

    "This rule would also change 3 line(s) in 2 of your recent charts"

Running one stage (not the whole pipeline) keeps it fast and attributes every
difference to the new rule. Results hold chart text; they are shown and dropped,
never stored.
"""

from __future__ import annotations

import copy
import difflib
import time
from collections import Counter
from dataclasses import dataclass, field

from .regex_risk import risks as regex_risks
from .stages import SLOW_RULE_MS, run_learned, run_regex_list, run_regex_pairs

__all__ = ["ELLIPSIS", "PipelineTrialReport", "RuleImpact", "clip_segments", "excerpt_windows",
           "preview", "run_stage_text", "trial_config", "trial_pipeline", "visible_line"]

# How much of the note to show around a cluster, and how many clusters to draw.
EXCERPT_CONTEXT = 2
EXCERPT_MAX_WINDOWS = 12
EXCERPT_VISIBLE_CHARS = 240
ELLIPSIS = "…"

# stage id -> (config key, entries are [pattern, replacement] pairs, flags)
_STAGES = {
    "metadata_lines": ("emr_line_metadata", False),
    "boilerplate": ("boilerplate", False),
    "learned_rules": ("learned_rules", True),
    "literal_replacements": ("literal_replacements", True),
}


@dataclass
class RuleImpact:
    charts_checked: int = 0
    charts_changed: int = 0
    lines_changed: int = 0
    samples: list[dict] = field(default_factory=list)  # {"before", "after"}
    example_failures: list[dict] = field(default_factory=list)
    # slowest extra time the rule added on one chart, and backtracking shapes
    max_ms: float = 0.0
    risks: list[str] = field(default_factory=list)

    @property
    def slow(self) -> bool:
        return self.max_ms >= SLOW_RULE_MS

    @property
    def headline(self) -> str:
        if not self.charts_checked:
            text = "No recent charts to compare against yet."
        elif not self.lines_changed:
            text = f"Changes nothing else in your {self.charts_checked} recent chart(s)."
        else:
            text = (f"Would also change {self.lines_changed} line(s) in {self.charts_changed} "
                    f"of your {self.charts_checked} recent chart(s).")
        if self.slow:
            text += f" ⚠ Slow: adds up to {self.max_ms:.0f} ms per chart."
        if self.risks:
            text += f" ⚠ Could hang: {self.risks[0]}."
        return text

    def to_dict(self) -> dict:
        return {"charts_checked": self.charts_checked, "charts_changed": self.charts_changed,
                "lines_changed": self.lines_changed, "samples": list(self.samples),
                "example_failures": list(self.example_failures), "headline": self.headline,
                "max_ms": self.max_ms, "slow": self.slow, "risks": list(self.risks)}


def _run_stage(sid: str, text: str, cfg: dict) -> str:
    import re
    key, pairs = _STAGES[sid]
    if sid == "metadata_lines":
        return run_regex_list(text, cfg, None, key, re.IGNORECASE | re.MULTILINE, sid=sid)[0]
    if sid == "boilerplate":
        return run_regex_list(text, cfg, None, key,
                              re.IGNORECASE | re.DOTALL | re.MULTILINE, sid=sid)[0]
    if sid == "learned_rules":
        return run_learned(text, cfg, None)[0]  # honors paused / section-scoped rules
    return run_regex_pairs(text, cfg, None, key, sid=sid)[0]


def run_stage_text(sid: str, text: str, cfg: dict) -> str:
    """Run only stage ``sid`` of ``cfg`` on ``text`` (contract examples use this)."""
    if sid not in _STAGES:
        raise ValueError(f"No stage-only run for {sid!r}")
    return _run_stage(sid, text, cfg)


def trial_config(cfg: dict, sid: str, entry) -> dict:
    """``cfg`` with ``entry`` (a pattern, or a [pattern, replacement] pair) added to ``sid``."""
    key, pairs = _STAGES[sid]
    trial = copy.deepcopy(cfg)
    rules = trial.setdefault(key, [])
    entry = list(entry) if pairs else entry
    if entry not in rules:
        rules.append(entry)
    return trial


def _surplus(lines: list[str], other: list[str]) -> list[str]:
    """Non-blank lines of ``lines`` that ``other`` has fewer copies of, in order."""
    remaining = Counter(other)
    out = []
    for ln in lines:
        if remaining.get(ln, 0) > 0:
            remaining[ln] -= 1
        elif ln.strip():
            out.append(ln)
    return out


def _changed_lines(before: str, after: str) -> list[dict]:
    # Only lines missing from the other side can have changed, so only those are
    # aligned. Diffing whole charts is quadratic on copy-forward repeats (seconds
    # per chart), and it ran on every "Remove" — long enough to drop the UI socket.
    a, b = before.splitlines(), after.splitlines()
    a, b = _surplus(a, b), _surplus(b, a)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        n = max(i2 - i1, j2 - j1)
        for k in range(n):
            old = a[i1 + k] if i1 + k < i2 else ""
            new = b[j1 + k] if j1 + k < j2 else ""
            if old.strip() or new.strip():
                out.append({"before": old, "after": new})
    return out


# A gap larger than this is aligned the fast surplus way. Diffing every line
# of a copy-forward chart is what made _changed_lines necessary.
_REGION_LIMIT = 200


def _emit_change(out: list[dict], old: str, new: str, index: int | None) -> None:
    # Blank lines are not changes, matching _changed_lines.
    if old.strip() or new.strip():
        out.append({"before": old, "after": new, "index": index})


def _surplus_indexed(lines: list[str], other: list[str], origin: int) -> list[tuple[int, str]]:
    """Surplus lines of ``lines``, with indexes shifted by ``origin``."""
    remaining = Counter(other)
    out = []
    for i, line in enumerate(lines):
        if remaining.get(line, 0) > 0:
            remaining[line] -= 1
        elif line.strip():
            out.append((origin + i, line))
    return out


def _anchors(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Lines that occur once on each side, in order. They are not the surplus."""
    count_a, count_b = Counter(a), Counter(b)
    b_at = {line: i for i, line in enumerate(b) if count_b[line] == 1 and count_a[line] == 1}
    found = []
    last = -1
    for i, line in enumerate(a):
        j = b_at.get(line)
        if j is not None and j > last:
            found.append((i, j))
            last = j
    return [(-1, -1), *found, (len(a), len(b))]


def _align_slice(a: list[str], i0: int, i1: int, b: list[str], j0: int, j1: int) -> list[dict]:
    """Changes in ``a[i0:i1]`` / ``b[j0:j1]``, indexes pointing into ``a``."""
    if i0 >= i1 and j0 >= j1:
        return []
    if j0 >= j1:
        out: list[dict] = []
        for i in range(i0, i1):
            _emit_change(out, a[i], "", i)
        return out
    if i0 >= i1:
        out = []
        for j in range(j0, j1):
            _emit_change(out, "", b[j], None)
        return out
    if (i1 - i0) > _REGION_LIMIT or (j1 - j0) > _REGION_LIMIT:
        return _align_surplus_slice(a, i0, i1, b, j0, j1)
    aa, bb = a[i0:i1], b[j0:j1]
    out = []
    for tag, x1, x2, y1, y2 in difflib.SequenceMatcher(a=aa, b=bb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        n = max(x2 - x1, y2 - y1)
        for k in range(n):
            has_old = x1 + k < x2
            old = aa[x1 + k] if has_old else ""
            new = bb[y1 + k] if y1 + k < y2 else ""
            _emit_change(out, old, new, i0 + x1 + k if has_old else None)
    return out


def _align_surplus_slice(a: list[str], i0: int, i1: int, b: list[str], j0: int, j1: int) -> list[dict]:
    """The :func:`_changed_lines` pairing, with original indexes on the before side."""
    aa, bb = a[i0:i1], b[j0:j1]
    left, right = _surplus_indexed(aa, bb, i0), _surplus_indexed(bb, aa, 0)
    out: list[dict] = []
    matcher = difflib.SequenceMatcher(a=[t for _, t in left], b=[t for _, t in right], autojunk=False)
    for tag, x1, x2, y1, y2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        n = max(x2 - x1, y2 - y1)
        for k in range(n):
            has_old = x1 + k < x2
            old = left[x1 + k][1] if has_old else ""
            new = right[y1 + k][1] if y1 + k < y2 else ""
            _emit_change(out, old, new, left[x1 + k][0] if has_old else None)
    return out


def _align_changes(before: str, after: str) -> list[dict]:
    """Changed lines in document order, each mapped back to the original note.

    Lines that occur once on both sides are anchors, the same idea as
    :func:`_changed_lines` keeping only the surplus. Only the gaps between
    anchors are paired, so a repeated line that the rule left in place is not
    the copy that gets struck. Blank lines are ignored. An inserted line has
    ``index`` None.
    """
    a, b = before.splitlines(), after.splitlines()
    out: list[dict] = []
    points = _anchors(a, b)
    for (i0, j0), (i1, j1) in zip(points, points[1:]):
        out.extend(_align_slice(a, i0 + 1, i1, b, j0 + 1, j1))
    return out


def _segments(before: str, after: str) -> list[tuple[str, str]]:
    """Shared text plain, text only on the original struck, text only on the result green."""
    if not after:
        return [("del", before)] if before else []
    if not before:
        return [("ins", after)]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
        if tag == "equal":
            out.append(("plain", before[i1:i2]))
        elif tag == "delete":
            out.append(("del", before[i1:i2]))
        elif tag == "insert":
            out.append(("ins", after[j1:j2]))
        else:
            if before[i1:i2]:
                out.append(("del", before[i1:i2]))
            if after[j1:j2]:
                out.append(("ins", after[j1:j2]))
    return [(kind, text) for kind, text in out if text]


def _inserted(text: str) -> dict:
    return {"kind": "change", "index": None, "segments": [("ins", text)]}


def _nearest_anchor(pos: int, indexed: list[int]) -> tuple[int | None, str]:
    prev = max((i for i in indexed if i < pos), default=None)
    nxt = min((i for i in indexed if i > pos), default=None)
    if prev is None and nxt is None:
        return None, ""
    if prev is None:
        return nxt, "before"
    if nxt is None or (pos - prev) <= (nxt - pos):
        return prev, "after"
    return nxt, "before"


def _place_inserts(changes: list[dict]) -> tuple[dict, dict, dict, list[str]]:
    """Insertions (no original line) join the nearest changed original line."""
    hosts: dict[int, dict] = {}
    before_map: dict[int, list[str]] = {}
    after_map: dict[int, list[str]] = {}
    orphans: list[str] = []
    indexed = [i for i, change in enumerate(changes) if change.get("index") is not None]
    for pos, change in enumerate(changes):
        index = change.get("index")
        if index is not None:
            hosts[index] = change
            continue
        text = change.get("after") or ""
        if not text.strip():
            continue
        anchor, side = _nearest_anchor(pos, indexed)
        if anchor is None:
            orphans.append(text)
            continue
        host = changes[anchor]["index"]
        (before_map if side == "before" else after_map).setdefault(host, []).append(text)
    return hosts, before_map, after_map, orphans


def _spans(indexes: list[int], n_lines: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for i in indexes:
        if n_lines <= 0:
            break
        start = max(0, i - EXCERPT_CONTEXT)
        end = min(n_lines - 1, i + EXCERPT_CONTEXT)
        if spans and start <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def _changed_in_span(start: int, end: int, hosts: dict, before_map: dict, after_map: dict) -> int:
    total = 0
    for i in range(start, end + 1):
        if i in hosts:
            total += 1
        total += len(before_map.get(i, ()))
        total += len(after_map.get(i, ()))
    return total


def excerpt_windows(before: str, after: str, *, aligned: list[dict] | None = None) -> dict:
    """Clusters of changes with two lines of the original note around each.

    ``aligned`` is the one alignment for this refresh (:func:`_align_changes`);
    pass it to avoid aligning the chart again. Returns ``{"windows", "omitted"}``.
    Each window has ``gap_before`` (0 on the first) and ``lines``. A line has
    ``kind`` (``context`` or ``change``), ``index`` (original line, or None for
    an insertion) and ``segments`` of ``("plain"|"del"|"ins", text)``. At most
    12 windows are drawn; ``omitted`` counts changed lines in the rest.
    """
    changes = aligned if aligned is not None else _align_changes(before, after)
    original = before.splitlines()
    hosts, ins_before, ins_after, orphans = _place_inserts(changes)
    if not hosts:
        if not orphans:
            return {"windows": [], "omitted": 0}
        return {"windows": [{"gap_before": 0, "lines": [_inserted(text) for text in orphans]}],
                "omitted": 0}
    spans = _spans(sorted(hosts), len(original))
    omitted = sum(_changed_in_span(start, end, hosts, ins_before, ins_after)
                  for start, end in spans[EXCERPT_MAX_WINDOWS:])
    windows = []
    prev_end = None
    for start, end in spans[:EXCERPT_MAX_WINDOWS]:
        gap = 0 if prev_end is None else start - prev_end - 1
        prev_end = end
        lines = []
        for i in range(start, end + 1):
            for text in ins_before.get(i, ()):
                lines.append(_inserted(text))
            if i in hosts:
                lines.append({"kind": "change", "index": i,
                              "segments": _segments(hosts[i]["before"], hosts[i]["after"])})
            else:
                lines.append({"kind": "context", "index": i,
                              "segments": [("plain", original[i])]})
            for text in ins_after.get(i, ()):
                lines.append(_inserted(text))
        windows.append({"gap_before": gap, "lines": lines})
    return {"windows": windows, "omitted": omitted}


def clip_segments(segments: list[tuple[str, str]],
                  limit: int = EXCERPT_VISIBLE_CHARS) -> tuple[list[tuple[str, str]], bool]:
    """The start of ``segments`` totaling at most ``limit`` characters, and whether it was cut."""
    shown: list[tuple[str, str]] = []
    left = limit
    for i, (kind, text) in enumerate(segments):
        if left <= 0:
            return shown, any(part for _kind, part in segments[i:])
        if len(text) <= left:
            if text:
                shown.append((kind, text))
            left -= len(text)
        else:
            shown.append((kind, text[:left]))
            return shown, True
    return shown, False


def visible_line(segments: list[tuple[str, str]], limit: int = EXCERPT_VISIBLE_CHARS) -> str:
    """One rendered line: at most ``limit`` characters, then an ellipsis when cut."""
    shown, clipped = clip_segments(segments, limit)
    return "".join(text for _kind, text in shown) + (ELLIPSIS if clipped else "")


def preview(cfg: dict, sid: str, entry, texts: list[str], *,
            skip_text: str | None = None, examples: list[dict] | None = None,
            max_samples: int = 8) -> RuleImpact:
    """The impact of adding ``entry`` to stage ``sid`` on ``texts``.

    ``skip_text`` (the chart the rule was taught on) is left out so the report
    is about *other* charts. ``examples`` are learned-rule examples; any whose
    result would change are listed in ``example_failures``.
    """
    if sid not in _STAGES:
        raise ValueError(f"No preview for stage {sid!r}")
    trial = trial_config(cfg, sid, entry)
    base = copy.deepcopy(cfg)
    base.setdefault(_STAGES[sid][0], [])
    impact = RuleImpact()
    pattern = entry[0] if isinstance(entry, (list, tuple)) else entry
    if isinstance(pattern, str):
        impact.risks = regex_risks(pattern)
    for text in texts:
        if not text or (skip_text is not None and text == skip_text):
            continue
        impact.charts_checked += 1
        try:
            t0 = time.perf_counter()
            before = _run_stage(sid, text, base)
            t1 = time.perf_counter()
            after = _run_stage(sid, text, trial)
            extra = ((time.perf_counter() - t1) - (t1 - t0)) * 1000
            impact.max_ms = max(impact.max_ms, round(extra, 1))
        except Exception:
            continue  # an invalid trial pattern is reported by the validator, not here
        if before == after:
            continue
        changed = _changed_lines(before, after)
        if changed:
            impact.charts_changed += 1
            impact.lines_changed += len(changed)
            for c in changed:
                if len(impact.samples) < max_samples and c not in impact.samples:
                    impact.samples.append({"before": c["before"][:300], "after": c["after"][:300]})
    if examples and sid == "learned_rules":
        from .rule_examples import check
        impact.example_failures = check(trial, examples)["failures"]
    return impact


# --- full-pipeline trial ------------------------------------------------------

@dataclass
class PipelineTrialReport:
    """What adding one rule does to the *whole* pipeline, before it is promoted.

    :func:`preview` runs one stage and attributes every difference to the new
    rule; this runs every stage, so a rule that is harmless on its own but
    exposes a line to a sloppy downstream rule shows up as an *interaction*.
    Counts are text-free (``summary``); ``samples`` hold lines for display only.
    """

    stage_id: str
    charts_checked: int = 0
    charts_changed: int = 0
    direct_facts_lost: int = 0                  # clinical values the rule itself removes
    interactions: list[dict] = field(default_factory=list)   # {"chart", "stage_id", "facts_lost"}
    new_issues: dict[str, int] = field(default_factory=dict)        # validation code -> count
    new_issue_stages: dict[str, int] = field(default_factory=dict)  # stage id -> count
    contract_failures: list[dict] = field(default_factory=list)     # rule_examples.check_contract_examples
    regressions_checked: int = 0
    regression_differences: int = 0
    samples: list[dict] = field(default_factory=list)
    max_ms: float = 0.0
    errors: int = 0

    @property
    def promotable(self) -> bool:
        """No failed mandatory counterexample and no interaction regression."""
        return not self.contract_failures and not self.interactions and not self.errors

    @property
    def needs_review(self) -> bool:
        """A difference alone asks for a look, even when promotion is allowed."""
        return bool(self.charts_changed or self.regression_differences or self.new_issues
                    or self.interactions or self.contract_failures or self.errors)

    @property
    def headline(self) -> str:
        if self.contract_failures:
            return (f"Not promotable: {len(self.contract_failures)} contract example(s) failed "
                    f"(every intended match and near-match must behave).")
        if self.interactions:
            return (f"Not promotable: in {len(self.interactions)} chart(s) another stage removed "
                    f"clinical values it used to keep.")
        parts = []
        if self.charts_checked:
            parts.append(f"Changes {self.charts_changed} of {self.charts_checked} chart(s).")
        if self.regressions_checked:
            parts.append(f"{self.regression_differences} of {self.regressions_checked} known-good "
                         f"chart(s) would change.")
        return " ".join(parts) or "Nothing to compare against yet."

    def summary(self) -> dict:
        return {"stage_id": self.stage_id, "charts_checked": self.charts_checked,
                "charts_changed": self.charts_changed, "direct_facts_lost": self.direct_facts_lost,
                "interactions": len(self.interactions),
                "interaction_stages": sorted({x["stage_id"] for x in self.interactions}),
                "new_issues": dict(self.new_issues), "new_issue_stages": dict(self.new_issue_stages),
                "contract_failures": [dict(f) for f in self.contract_failures],
                "regressions_checked": self.regressions_checked,
                "regression_differences": self.regression_differences,
                "promotable": self.promotable, "needs_review": self.needs_review,
                "max_ms": self.max_ms, "errors": self.errors}


def _losses(result) -> Counter:
    report = getattr(result, "fact_check", None)
    if report is None:
        return Counter()
    return Counter({(x.key, x.stage_id): x.count for x in report.losses if x.category != "by_design"})


def _issue_counts(result) -> tuple[Counter, Counter]:
    report = getattr(result, "validation", None)
    if report is None:
        return Counter(), Counter()
    return (Counter(i.code for i in report.issues if i.severity in ("error", "critical")),
            Counter(i.stage_id for i in report.issues if i.severity in ("error", "critical")))


def trial_pipeline(cfg: dict, sid: str, entry, texts: list[str], *, contract=None,
                   known_good: list | None = None, custom_dir=None, max_samples: int = 8,
                   skip_text: str | None = None) -> PipelineTrialReport:
    """Run the full candidate pipeline (``cfg`` + ``entry`` in stage ``sid``) on ``texts``.

    ``contract`` is the rule's :class:`~chartcleaner.rule_contracts.RuleContract`
    (its examples are checked; a destructive contract needs a match *and* a
    keep example). ``known_good`` defaults to the stored encrypted known-good
    charts; their expectations are compared, never rewritten.
    """
    from .engine import Pipeline
    from .fact_check import fact_counts

    if sid not in _STAGES:
        raise ValueError(f"No pipeline trial for stage {sid!r}")
    trial = trial_config(cfg, sid, entry)
    base = copy.deepcopy(cfg)
    base.setdefault(_STAGES[sid][0], [])
    report = PipelineTrialReport(stage_id=sid)
    baseline_pipe, trial_pipe = Pipeline(base, custom_dir=custom_dir), Pipeline(trial, custom_dir=custom_dir)
    for i, text in enumerate(texts):
        if not text or (skip_text is not None and text == skip_text):
            continue
        report.charts_checked += 1
        try:
            t0 = time.perf_counter()
            before = baseline_pipe.run(text, wrap=False, fact_check=True, validation="shadow")
            t1 = time.perf_counter()
            after = trial_pipe.run(text, wrap=False, fact_check=True, validation="shadow")
            report.max_ms = max(report.max_ms, round(((time.perf_counter() - t1) - (t1 - t0)) * 1000, 1))
        except Exception:
            report.errors += 1
            continue
        if before.text == after.text:
            continue
        report.charts_changed += 1
        # The rule's own effect, measured on its stage alone...
        try:
            own_before = _run_stage(sid, text, base)
            own_after = _run_stage(sid, text, trial)
            direct = fact_counts(own_before) - fact_counts(own_after)
        except Exception:
            direct = Counter()
        report.direct_facts_lost += sum(direct.values())
        # ...versus what the whole pipeline newly loses, attributed by stage.
        new_losses = _losses(after) - _losses(before)
        by_stage: dict[str, int] = {}
        for (_key, stage_id), n in new_losses.items():
            if stage_id != sid:
                by_stage[stage_id] = by_stage.get(stage_id, 0) + n
        for stage_id, n in sorted(by_stage.items()):
            report.interactions.append({"chart": i, "stage_id": stage_id, "facts_lost": n})
        codes_before, stages_before = _issue_counts(before)
        codes_after, stages_after = _issue_counts(after)
        for code, n in (codes_after - codes_before).items():
            report.new_issues[code] = report.new_issues.get(code, 0) + n
        for stage_id, n in (stages_after - stages_before).items():
            report.new_issue_stages[stage_id] = report.new_issue_stages.get(stage_id, 0) + n
        for c in _changed_lines(before.text, after.text):
            if len(report.samples) < max_samples and c not in report.samples:
                report.samples.append({"before": c["before"][:300], "after": c["after"][:300]})
    if contract is not None:
        from .rule_examples import check_contract_examples
        report.contract_failures = check_contract_examples(trial, sid, contract)
    from . import regression_set
    charts = regression_set.list_charts() if known_good is None else list(known_good)
    report.regressions_checked = len(charts)
    if charts:
        report.regression_differences = len(regression_set.changed(trial, custom_dir, charts))
    return report
