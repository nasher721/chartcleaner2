"""Batch cleaning of chart files (text, docx, pdf) with per-file isolation.

The /batch page drives :func:`run_batch`; each file is loaded via
:func:`chartcleaner.ingest.load_file`, cleaned with the normal
:class:`~chartcleaner.engine.Pipeline`, and audited with the post-run review.
A file that fails to load or clean becomes an ``error`` result — one bad file
never aborts the batch. Order of results follows the order of input paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from chartcleaner.audit import run_audit
from chartcleaner.engine import Pipeline
from chartcleaner.ingest import load_file
from chartcleaner.note_type import detect as detect_note_type
from chartcleaner.service import format_output

__all__ = ["BatchResult", "run_batch", "run_texts"]


@dataclass
class BatchResult:
    """One file's outcome. ``needs_review`` is true when its clinical-facts check
    flagged something or the audit found possible PHI left behind."""
    name: str
    path: str
    status: str                 # "ok" | "error"
    error: str = ""
    chars_before: int = 0
    chars_after: int = 0
    reduction: float = 0.0
    phi_total: int = 0
    findings: int = 0
    elapsed_ms: int = 0
    # clinical-facts check: "" when off, else the headline ("ok" status shows as ✓)
    facts: str = ""
    facts_status: str = ""
    cleaned: str = ""
    # detected note type label ("" when detection is off) and the preset used for it
    note_type: str = ""
    preset: str = ""
    # optional one-line AI summary (run_texts(summarize=...)); "" when not asked or failed
    summary: str = ""
    # slim per-stage summaries so batch runs still feed the Statistics dashboard
    stages: list[dict] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.status == "ok" and (self.facts_status not in ("", "ok") or self.findings > 0)


def run_batch(
    paths: list[Path],
    cfg: dict,
    custom_dir: str | Path | None = None,
    *,
    delta: bool = False,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    note_presets: dict[str, str] | None = None,
    preset_loader: Callable[[str], dict] | None = None,
) -> list[BatchResult]:
    """Clean every path in order; failures are isolated per file.

    With ``delta`` each file's output keeps only what changed between its
    daily notes (the copy-forward delta view). ``on_progress(done, total,
    result)`` is called after each file (from the worker thread).

    ``note_presets`` (note type → preset name, the app's ``prefs.note_presets``)
    turns on per-file note-type detection: a file whose type maps to a preset
    is cleaned with ``preset_loader(name)`` instead of ``cfg`` — the same rule
    the Clean page applies when "auto-apply" is on. A preset that fails to load
    falls back to ``cfg``.
    """
    runner = _Runner(cfg, custom_dir, delta, note_presets, preset_loader)
    out: list[BatchResult] = []
    for path in paths:
        started = time.monotonic()
        try:
            ing = load_file(path, cfg)
            out.append(runner.clean(path.name, str(path), ing.text))
        except Exception as exc:
            out.append(_error(path.name, str(path), exc, started))
        _report(on_progress, out, len(paths))
    return out


def run_texts(
    items: list[tuple[str, str]],
    cfg: dict,
    custom_dir: str | Path | None = None,
    *,
    delta: bool = False,
    on_progress: Callable[[int, int, BatchResult], None] | None = None,
    note_presets: dict[str, str] | None = None,
    preset_loader: Callable[[str], dict] | None = None,
    summarize: Callable[[str], str] | None = None,
) -> list[BatchResult]:
    """Like :func:`run_batch` for texts already in memory: ``(name, text)`` pairs
    (e.g. one per patient from :func:`chartcleaner.patients.split`).

    ``summarize(cleaned) -> str`` adds a one-line summary per item (the local
    AI); a failure there leaves ``summary`` empty and never fails the item.
    """
    runner = _Runner(cfg, custom_dir, delta, note_presets, preset_loader)
    out: list[BatchResult] = []
    for name, text in items:
        started = time.monotonic()
        try:
            res = runner.clean(name, "", text)
            if summarize is not None:
                try:
                    res.summary = (summarize(res.cleaned) or "").strip()
                except Exception:
                    res.summary = ""
            out.append(res)
        except Exception as exc:
            out.append(_error(name, "", exc, started))
        _report(on_progress, out, len(items))
    return out


def _error(name: str, path: str, exc: Exception, started: float) -> BatchResult:
    return BatchResult(name=name, path=path, status="error", error=str(exc),
                       elapsed_ms=int((time.monotonic() - started) * 1000))


def _report(on_progress, out: list[BatchResult], total: int) -> None:
    if on_progress is not None:
        try:
            on_progress(len(out), total, out[-1])
        except Exception:
            pass  # progress reporting must never break the batch


class _Runner:
    """One pipeline per preset, built lazily; cleans one text into a BatchResult."""

    def __init__(self, cfg, custom_dir, delta, note_presets, preset_loader):
        self.cfg, self.custom_dir, self.delta = cfg, custom_dir, delta
        self.note_presets, self.preset_loader = note_presets, preset_loader
        self.pipes: dict[str, Pipeline] = {"": Pipeline(cfg, custom_dir=custom_dir)}
        self.configs: dict[str, dict] = {"": cfg}

    def _preset_for(self, text: str) -> tuple[str, str]:
        if self.note_presets is None:
            return "", ""
        found = detect_note_type(text, self.cfg)
        wanted = self.note_presets.get(found.note_type or "") or ""
        if wanted and self.preset_loader is not None and wanted not in self.pipes:
            try:
                self.configs[wanted] = self.preset_loader(wanted)
                self.pipes[wanted] = Pipeline(self.configs[wanted], custom_dir=self.custom_dir)
            except Exception:
                wanted = ""
        return found.label, (wanted if wanted in self.pipes else "")

    def clean(self, name: str, path: str, text: str) -> BatchResult:
        note_label, preset = self._preset_for(text)
        run_cfg = self.configs[preset]
        result = self.pipes[preset].run(text)
        audit = run_audit(result.text, run_cfg)
        findings = 0 if audit.skipped else sum(audit.counts.values())
        return BatchResult(
            name=name,
            path=path,
            status="ok",
            chars_before=result.chars_before,
            chars_after=result.chars_after,
            reduction=result.reduction,
            phi_total=sum(result.phi_counts().values()),
            findings=findings,
            elapsed_ms=result.duration_ms,
            facts=result.fact_check.headline() if result.fact_check else "",
            facts_status=result.fact_check.status if result.fact_check else "",
            cleaned=format_output(result.text, "text", self.delta)[0] if self.delta else result.text,
            note_type=note_label,
            preset=preset,
            stages=[{"label": s.label, "matches": s.matches,
                     "before": s.chars_before, "after": s.chars_after,
                     "skipped": s.skipped}
                    for s in result.stages],
        )
