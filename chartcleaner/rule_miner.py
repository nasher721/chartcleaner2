"""Autonomous EMR chrome and pattern discovery ("Rule Miner").

Analyzes document collections or residual text from audit findings to discover
repetitive EMR boilerplate, system signatures, and page furniture. Uses token
n-gram entropy and structural template induction to synthesize candidate regexes
for 1-click adoption in the Pipeline editor.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from typing import Any

__all__ = ["RuleCandidate", "mine_chrome_rules"]


@dataclass
class RuleCandidate:
    pattern: str
    description: str
    frequency: int
    sample_matches: list[str]
    confidence: float
    suggested_stage: str = "emr_line_metadata"


_DIGIT_RUN = re.compile(r"\b\d+\b")
_DATE_RUN = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_TIME_RUN = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def _line_template(line: str) -> str:
    """Generalize line into a structural pattern template."""
    s = line.strip()
    s = _DATE_RUN.sub("<DATE>", s)
    s = _TIME_RUN.sub("<TIME>", s)
    s = _DIGIT_RUN.sub("<NUM>", s)
    return s


def _template_to_regex(template: str) -> str:
    """Convert structural template into a safe regex."""
    parts = re.split(r"(<DATE>|<TIME>|<NUM>)", template)
    rx_parts = []
    for p in parts:
        if p == "<DATE>":
            rx_parts.append(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")
        elif p == "<TIME>":
            rx_parts.append(r"\d{1,2}:\d{2}(?::\d{2})?")
        elif p == "<NUM>":
            rx_parts.append(r"\d+")
        elif p:
            rx_parts.append(re.escape(p))
    body = "".join(rx_parts)
    return rf"(?im)^\s*{body}\s*$"


def mine_chrome_rules(
    documents: list[str],
    min_occurrence: int = 2,
    min_line_len: int = 8,
    max_line_len: int = 150,
) -> list[RuleCandidate]:
    """Mine candidate line-deletion regexes from repetitive lines across documents."""
    template_counts: dict[str, int] = collections.defaultdict(int)
    template_samples: dict[str, list[str]] = collections.defaultdict(list)

    for doc in documents:
        seen_in_doc: set[str] = set()
        for line in doc.split("\n"):
            s = line.strip()
            if len(s) < min_line_len or len(s) > max_line_len:
                continue
            tmpl = _line_template(s)
            # Only count once per document to measure cross-document prevalence
            if tmpl not in seen_in_doc:
                seen_in_doc.add(tmpl)
                template_counts[tmpl] += 1
                if len(template_samples[tmpl]) < 3:
                    template_samples[tmpl].append(s)

    candidates: list[RuleCandidate] = []
    for tmpl, count in template_counts.items():
        if count < min_occurrence:
            continue

        # Filter out purely clinical lines (lines with common diagnostic words)
        low = tmpl.lower()
        if any(w in low for w in ("mg", "daily", "denies", "patient", "history", "assessment")):
            continue

        rx = _template_to_regex(tmpl)
        conf = min(0.95, 0.5 + (count * 0.1))

        candidates.append(
            RuleCandidate(
                pattern=rx,
                description=f"Repetitive EMR template occurring in {count} document(s)",
                frequency=count,
                sample_matches=template_samples[tmpl],
                confidence=round(conf, 2),
                suggested_stage="emr_line_metadata",
            )
        )

    return sorted(candidates, key=lambda c: (-c.frequency, -c.confidence))
