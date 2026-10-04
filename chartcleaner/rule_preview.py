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
from dataclasses import dataclass, field

from .stages import run_regex_list, run_regex_pairs

__all__ = ["RuleImpact", "preview", "trial_config"]

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

    @property
    def headline(self) -> str:
        if not self.charts_checked:
            return "No recent charts to compare against yet."
        if not self.lines_changed:
            return f"Changes nothing else in your {self.charts_checked} recent chart(s)."
        return (f"Would also change {self.lines_changed} line(s) in {self.charts_changed} "
                f"of your {self.charts_checked} recent chart(s).")

    def to_dict(self) -> dict:
        return {"charts_checked": self.charts_checked, "charts_changed": self.charts_changed,
                "lines_changed": self.lines_changed, "samples": list(self.samples),
                "example_failures": list(self.example_failures), "headline": self.headline}


def _run_stage(sid: str, text: str, cfg: dict) -> str:
    import re
    key, pairs = _STAGES[sid]
    if sid == "metadata_lines":
        return run_regex_list(text, cfg, None, key, re.IGNORECASE | re.MULTILINE, sid=sid)[0]
    if sid == "boilerplate":
        return run_regex_list(text, cfg, None, key,
                              re.IGNORECASE | re.DOTALL | re.MULTILINE, sid=sid)[0]
    return run_regex_pairs(text, cfg, None, key, sid=sid)[0]


def trial_config(cfg: dict, sid: str, entry) -> dict:
    """``cfg`` with ``entry`` (a pattern, or a [pattern, replacement] pair) added to ``sid``."""
    key, pairs = _STAGES[sid]
    trial = copy.deepcopy(cfg)
    rules = trial.setdefault(key, [])
    entry = list(entry) if pairs else entry
    if entry not in rules:
        rules.append(entry)
    return trial


def _changed_lines(before: str, after: str) -> list[dict]:
    a, b = before.splitlines(), after.splitlines()
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
    for text in texts:
        if not text or (skip_text is not None and text == skip_text):
            continue
        impact.charts_checked += 1
        try:
            before, after = _run_stage(sid, text, base), _run_stage(sid, text, trial)
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
