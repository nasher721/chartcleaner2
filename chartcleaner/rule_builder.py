"""Everything worth knowing about a learned rule *before* it is saved.

The Clean page's rule builder (and the generalize list) call :func:`preview`
for the rule being built and show:

* how often it matches in the chart on screen, with each change shown in place;
* what it would do to the recent charts (:mod:`rule_preview` — one stage only,
  so every difference is this rule's), slow or risky patterns included;
* clinical values it would delete from the chart on screen
  (:func:`fact_check.fact_counts` before vs after — numbers with units, labs,
  vitals, drugs, allergy/code-status words);
* clashes: a "never remove" text that would protect some of its matches, or
  an existing rule for the same text with a different replacement.

:func:`rank` scores several candidates (from :func:`rule_shapes.generalize`) in
one pass, and :func:`config_impact` compares two whole configs on the recent
charts (used before importing someone else's rules). Results hold chart text
for display only; nothing is stored.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field

from .fact_check import extract_facts, fact_counts
from .regex_risk import risks as regex_risks
from .rule_preview import _align_changes, _changed_lines, excerpt_windows, preview as stage_preview
from .rule_shapes import build, normalize

__all__ = ["BuilderPreview", "config_impact", "keep_conflicts", "preview", "rank", "trial"]

# Stages that honor "never remove" exceptions (stage_options.<sid>.exceptions).
EXCEPTION_STAGES = ("metadata_lines", "boilerplate", "learned_rules", "literal_replacements")
REGEX_STAGES = ("metadata_lines", "boilerplate", "learned_rules", "literal_replacements")


@dataclass
class BuilderPreview:
    pattern: str
    replacement: str
    hits: int = 0                       # matches in the chart on screen
    samples: list[dict] = field(default_factory=list)   # {"before", "after"} lines here
    facts_lost: list[dict] = field(default_factory=list)  # {"display", "line", "count"}
    protected: list[str] = field(default_factory=list)  # "never remove" texts that keep matches
    conflict: str = ""                  # an existing rule with another replacement
    risks: list[str] = field(default_factory=list)
    impact: dict | None = None          # rule_preview.RuleImpact.to_dict() on recent charts
    excerpt: dict | None = None        # in-place windows; None if that view could not be built

    @property
    def warnings(self) -> list[str]:
        out = []
        if self.facts_lost:
            shown = ", ".join(f["display"] for f in self.facts_lost[:4])
            out.append(f"Would delete clinical values from this chart: {shown}"
                       + (" …" if len(self.facts_lost) > 4 else ""))
        if self.protected:
            out.append("Your “never remove” list keeps some matches: "
                       + ", ".join(f"“{p[:40]}”" for p in self.protected[:3]))
        if self.conflict:
            out.append(self.conflict)
        if self.risks:
            out.append(f"Could be slow: {self.risks[0]}")
        if self.impact and self.impact.get("example_failures"):
            out.append(f"Changes {len(self.impact['example_failures'])} earlier lesson(s)")
        return out

    @property
    def headline(self) -> str:
        here = f"Matches {self.hits} time(s) in this chart"
        if self.impact and self.impact.get("charts_checked"):
            return f"{here}. {self.impact['headline']}"
        return here + "."

    def to_dict(self) -> dict:
        return {"pattern": self.pattern, "replacement": self.replacement, "hits": self.hits,
                "samples": list(self.samples), "facts_lost": list(self.facts_lost),
                "protected": list(self.protected), "conflict": self.conflict,
                "risks": list(self.risks), "impact": self.impact, "warnings": self.warnings,
                "headline": self.headline, "excerpt": self.excerpt}


def _pair(rule) -> list[str]:
    return build(rule) if isinstance(rule, dict) else list(rule)


def trial(cfg: dict, pair: list[str], scope: dict | None = None) -> dict:
    """A minimal learned-rules config with only ``pair`` (and its scope), enabled.

    The user's "never remove" exceptions for the stage are kept, so the preview
    shows what the saved rule would really do.
    """
    from .rule_meta import update
    opts = ((cfg.get("stage_options") or {}).get("learned_rules") or {})
    out = {"learned_rules": [list(pair)],
           "stage_options": {"learned_rules": {k: v for k, v in opts.items()
                                               if k in ("exceptions", "case_sensitive", "preserve_tables")}}}
    if scope:
        out = update(out, pair[0], scope=scope)
    return out


def _apply(cfg: dict, pair: list[str], text: str, scope: dict | None) -> tuple[str, int]:
    from .stages import run_learned
    out, n, _ = run_learned(text, trial(cfg, pair, scope), None)
    return out, n


def _facts_lost(before: str, after: str) -> list[dict]:
    lost = fact_counts(before) - fact_counts(after)
    if not lost:
        return []
    shown: dict[str, dict] = {}
    for fact in extract_facts(before):
        if fact.key in lost and fact.key not in shown:
            shown[fact.key] = {"display": fact.display, "line": fact.line[:200], "count": lost[fact.key]}
    return list(shown.values())


def keep_conflicts(cfg: dict, pair: list[str], text: str) -> list[str]:
    """“Never remove” texts on lines this rule matches (those matches stay)."""
    import re
    keep: list[str] = []
    for sid in EXCEPTION_STAGES:
        for value in ((cfg.get("stage_options") or {}).get(sid) or {}).get("exceptions") or []:
            if isinstance(value, str) and value.strip() and value not in keep:
                keep.append(value)
    if not keep or not text:
        return []
    try:
        regex = re.compile(pair[0], re.IGNORECASE)
    except re.error:
        return []
    hits: list[str] = []
    for m in regex.finditer(text):
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.end())
        span = text[start:end if end != -1 else len(text)].casefold()
        for value in keep:
            if value.casefold() in span and value not in hits:
                hits.append(value)
        if len(hits) >= len(keep):
            break
    return hits


def preview(cfg: dict, rule, chart: str, recent: list[str] | None = None, *,
            scope: dict | None = None, examples: list[dict] | None = None,
            max_samples: int = 6) -> BuilderPreview:
    """What saving ``rule`` (a shape dict or a ``[pattern, replacement]`` pair) would do."""
    pair = _pair(rule)
    result = BuilderPreview(pattern=pair[0], replacement=pair[1], risks=regex_risks(pair[0]))
    for existing in cfg.get("learned_rules") or []:
        if isinstance(existing, (list, tuple)) and len(existing) == 2 and existing[0] == pair[0] \
                and existing[1] != pair[1]:
            result.conflict = "This text already has a rule with a different replacement."
    chart = chart or ""
    result.excerpt = {"windows": [], "omitted": 0}
    if chart:
        after, result.hits = _apply(cfg, pair, chart, scope)
        # One alignment for this refresh: samples and the excerpt share it.
        # Applying the rule a second time would only repeat the same trial.
        aligned = _align_changes(chart, after)
        result.samples = [{"before": c["before"][:300], "after": c["after"][:300]}
                          for c in aligned[:max_samples]]
        result.facts_lost = _facts_lost(chart, after)
        result.protected = keep_conflicts(cfg, pair, chart)
        try:
            result.excerpt = excerpt_windows(chart, after, aligned=aligned)
        except Exception:
            result.excerpt = None
            result.samples = [{"before": c["before"][:300], "after": c["after"][:300]}
                              for c in _changed_lines(chart, after)[:max_samples]]
    if recent:
        base = copy.deepcopy(cfg)
        if scope:
            from .rule_meta import update
            base = update(base, pair[0], scope=scope)
        result.impact = stage_preview(base, "learned_rules", pair, recent, skip_text=chart or None,
                                      examples=examples).to_dict()
    return result


def rank(cfg: dict, rules: list, chart: str, recent: list[str] | None = None) -> list[dict]:
    """``[{"hits", "charts", "lines"}]`` per rule: matches here and in the recent charts."""
    import re
    out = []
    recent = [t for t in (recent or []) if t and t != chart]
    for rule in rules:
        pair = _pair(rule)
        try:
            regex = re.compile(pair[0], re.IGNORECASE)
        except re.error:
            out.append({"hits": -1, "charts": 0, "lines": 0})
            continue
        hits = len(regex.findall(chart or ""))
        charts = lines = 0
        for text in recent:
            n = len(regex.findall(text))
            if n:
                charts += 1
                lines += n
        out.append({"hits": hits, "charts": charts, "lines": lines})
    return out


def _run_regex_stages(text: str, cfg: dict) -> str:
    from .rule_preview import _run_stage
    order = [sid for sid in (cfg.get("stage_order") or REGEX_STAGES) if sid in REGEX_STAGES]
    for sid in order:
        if ((cfg.get("stage_options") or {}).get(sid) or {}).get("enabled") is False:
            continue
        try:
            text = _run_stage(sid, text, cfg)
        except Exception:
            continue
    return text


def config_impact(old: dict, new: dict, texts: list[str], *, max_samples: int = 8) -> dict:
    """What switching from ``old`` to ``new`` rules changes on ``texts``.

    Runs only the regex stages (metadata, boilerplate, learned, literal), so it
    is fast and every difference comes from the rules. Returns
    ``{"charts_checked", "charts_changed", "lines_changed", "samples", "facts_lost"}``.
    """
    report = {"charts_checked": 0, "charts_changed": 0, "lines_changed": 0, "samples": [],
              "facts_lost": []}
    lost_total: Counter = Counter()
    for text in texts:
        if not text:
            continue
        report["charts_checked"] += 1
        a, b = _run_regex_stages(text, old), _run_regex_stages(text, new)
        if a == b:
            continue
        changed = _changed_lines(a, b)
        if not changed:
            continue
        report["charts_changed"] += 1
        report["lines_changed"] += len(changed)
        for c in changed:
            if len(report["samples"]) < max_samples:
                report["samples"].append({"before": c["before"][:300], "after": c["after"][:300]})
        for fact in _facts_lost(a, b):
            if fact["display"] not in lost_total:
                report["facts_lost"].append(fact)
            lost_total[fact["display"]] += fact["count"]
    return report


def check_shape(shape: dict) -> tuple[dict | None, str]:
    """``(normalized shape, "")`` or ``(None, error message)`` for form input."""
    try:
        return normalize(shape), ""
    except ValueError as e:
        return None, str(e)
