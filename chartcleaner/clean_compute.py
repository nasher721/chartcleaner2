"""Pure computation behind the Clean page; no NiceGUI or persistence imports."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from app_pages.review_model import FindingRow, build_finding_rows
from chartcleaner.stage_cache import process_cache
from chartcleaner.audit import run_audit
from chartcleaner.bundle import build as build_bundle
from chartcleaner.conflicts import build as build_conflicts
from chartcleaner.delta_engine import extract_note_deltas
from chartcleaner.devices import build as build_devices
from chartcleaner.engine import Pipeline, PipelineCancelled, RunResult
from chartcleaner.fact_check import FactTracker
from chartcleaner.micro import build as build_micro
from chartcleaner.overnight import build as build_overnight
from chartcleaner.pending import build as build_pending
from chartcleaner.problems import build as build_problems
from chartcleaner.review_html import review_diff_html
from chartcleaner.review_state import redaction_candidates, release_decision
from chartcleaner.safety import build as build_safety, removed_lines
from chartcleaner.stale import build as build_stale
from chartcleaner.timeline import build as build_timeline
from chartcleaner.trends import build as build_trends

Stop = Callable[[], bool]
Progress = Callable[[str, str, int, int], None]


@dataclass
class CleanComputation:
    result: RunResult
    audit: object
    extras: dict
    review: tuple[str, int] | None
    groups: list[dict]
    finding_rows: list[FindingRow]


def change_groups(result: RunResult) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for st in result.stages:
        for c in st.details.get("changes") or []:
            if st.id == "medical_abbreviations":
                key = ("abbr", c["rule"], c["after"])
                g = groups.setdefault(key, {
                    "kind": "abbr", "sid": st.id, "stage": st.label,
                    "rule": c["rule"], "term": c["before"], "after": c["after"],
                    "count": 0, "source": c.get("source", "bundled")})
                g["count"] += 1
                continue
            if not c["before"].strip():
                continue
            kind = "removal" if c["after"] == "" else "replace"
            key = (kind, st.id, c["rule"])
            g = groups.setdefault(key, {
                "kind": kind, "sid": st.id, "stage": st.label, "rule": c["rule"],
                "befores": [], "afters": [], "count": 0})
            g["count"] += 1
            if c["before"] not in g["befores"] and len(g["befores"]) < 30:
                g["befores"].append(c["before"])
                g["afters"].append(c["after"])
    return list(groups.values())


def flagged_lines(audit) -> set[int]:
    return {f.line for f in audit.findings} if audit is not None and not audit.skipped else set()


def extras_work(cleaned: str, fact_report, *, stop: Stop | None = None,
                only: set[str] | None = None) -> dict:
    out = {}
    gone = removed_lines(fact_report)
    builders = (
        ("delta", extract_note_deltas), ("trends", build_trends),
        ("timeline", build_timeline), ("problems", build_problems),
        ("devices", build_devices), ("micro", build_micro),
        ("overnight", build_overnight), ("pending", build_pending),
        ("bundle", build_bundle), ("safety", lambda t: build_safety(t, gone)),
        ("stale", build_stale), ("conflicts", build_conflicts),
    )
    for key, fn in builders:
        if stop is not None and stop():
            raise PipelineCancelled("insights")
        if only is not None and key not in only:
            continue
        try:
            out[key] = fn(cleaned)
        except Exception:
            out[key] = None
    return out


def review_details(source: str, result: RunResult, audit) -> tuple[str, int]:
    metadata: dict[str, int] = {}
    html = review_diff_html(source, result.text, flagged_lines(audit),
                            change_groups(result), metadata)
    return html, metadata["change_count"]


def insight_details(cleaned: str, fact_report, *, stop: Stop | None = None) -> dict:
    return extras_work(cleaned, fact_report, stop=stop)


def edit_details(source: str, value: str, result: RunResult, baseline,
                 cfg: dict, lazy: bool) -> CleanComputation:
    tracker = FactTracker(source)
    tracker.after_stage("edited_output", "Edited output", value)
    report = tracker.report(value)
    # Preserve pipeline attribution for losses the user did not create.
    if baseline:
        attributed = []
        for loss in report.losses:
            remaining = loss.count
            for prior in baseline.losses:
                if prior.key == loss.key and remaining:
                    count = min(remaining, prior.count)
                    attributed.append(replace(prior, count=count))
                    remaining -= count
            if remaining:
                attributed.append(replace(loss, count=remaining))
        report.losses = attributed
    edited = replace(result, text=value, fact_check=report, edited_validation=None)
    release_decision(edited, output_text=value, source=source)
    audit = run_audit(value, cfg)
    review = None if lazy else review_details(source, edited, audit)
    candidates = redaction_candidates(source, value)
    groups = change_groups(edited)
    rows = build_finding_rows(edited, audit, source=source, output=value)
    return CleanComputation(
        edited, audit, {"redactions": candidates}, review, groups, rows)


def compute_clean(text: str, mode: str, cfg: dict, custom_dir: str | Path | None,
                  lazy: bool, *, stop: Stop | None = None,
                  progress: Progress | None = None) -> CleanComputation:
    if stop is not None and stop():
        raise PipelineCancelled("before start")
    cache = process_cache()
    if mode != "clean":
        result = Pipeline(cfg, mode=mode).run(
            text, track_changes=True, cache=cache, stop=stop, progress=progress)
        audit = None
    else:
        result = Pipeline(cfg, custom_dir=custom_dir).run(
            text, track_changes=True, cache=cache, stop=stop, progress=progress)
        audit = run_audit(result.text, cfg)
    if stop is not None and stop():
        raise PipelineCancelled("after pipeline")
    extras = extras_work(
        result.text, result.fact_check, stop=stop,
        only={"safety", "conflicts", "timeline"} if lazy and mode == "clean" else None,
    ) if mode == "clean" else {}
    if stop is not None and stop():
        raise PipelineCancelled("after insights")
    extras["redactions"] = redaction_candidates(text, result.text)
    review = None if lazy else review_details(text, result, audit)
    if stop is not None and stop():
        raise PipelineCancelled("after presentation")
    groups = change_groups(result)
    rows = build_finding_rows(result, audit, source=text, output=result.text)
    return CleanComputation(result, audit, extras, review, groups, rows)
