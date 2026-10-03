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

from chartcleaner.audit import run_audit
from chartcleaner.engine import Pipeline
from chartcleaner.ingest import load_file
from chartcleaner.service import format_output

__all__ = ["BatchResult", "run_batch"]


@dataclass
class BatchResult:
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
    # slim per-stage summaries so batch runs still feed the Statistics dashboard
    stages: list[dict] = field(default_factory=list)


def run_batch(
    paths: list[Path],
    cfg: dict,
    custom_dir: str | Path | None = None,
    *,
    delta: bool = False,
) -> list[BatchResult]:
    """Clean every path in order; failures are isolated per file.

    With ``delta`` each file's output keeps only what changed between its
    daily notes (the copy-forward delta view).
    """
    pipe = Pipeline(cfg, custom_dir=custom_dir)
    out: list[BatchResult] = []
    for path in paths:
        started = time.monotonic()
        try:
            ing = load_file(path, cfg)
            result = pipe.run(ing.text)
            audit = run_audit(result.text, cfg)
            findings = 0 if audit.skipped else sum(audit.counts.values())
            out.append(BatchResult(
                name=path.name,
                path=str(path),
                status="ok",
                chars_before=result.chars_before,
                chars_after=result.chars_after,
                reduction=result.reduction,
                phi_total=sum(result.phi_counts().values()),
                findings=findings,
                elapsed_ms=result.duration_ms,
                facts=result.fact_check.headline() if result.fact_check else "",
                facts_status=result.fact_check.status if result.fact_check else "",
                cleaned=format_output(result.text, "text", delta)[0] if delta else result.text,
                stages=[{"label": s.label, "matches": s.matches,
                         "before": s.chars_before, "after": s.chars_after,
                         "skipped": s.skipped}
                        for s in result.stages],
            ))
        except Exception as exc:
            out.append(BatchResult(
                name=path.name,
                path=str(path),
                status="error",
                error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ))
    return out
