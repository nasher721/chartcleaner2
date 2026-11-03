# Large-Chart Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Clean-page editing and websocket handling responsive for 100–500 KB charts by isolating CPU-heavy work in a reusable process and bounding editor traffic, without changing clinical output or validation decisions.

**Architecture:** The browser owns immediate editor feedback and debounced snapshots. The NiceGUI process owns UI state and persistence; one app-wide worker process owns pipeline, audit, redaction, Review, and Insights computation. A small asynchronous request/progress protocol joins them, with an inline worker used only by the NiceGUI test harness.

**Tech Stack:** Python 3.10+, NiceGUI, `multiprocessing` spawn/Pipe, existing `Pipeline` and dataclass result models, browser JavaScript, pytest, Playwright for scheduled real-browser measurements.

## Global Constraints

- Target Clean-page editing and cleaning for **100–500 KB** charts.
- Preserve cleaned text, stage order, validation results, provenance, release decisions, and existing clinical behavior.
- Do not change Batch, REST, MCP, AI summarization, Ask this chart, or cross-note algorithms.
- Do not introduce a reduced-validation large-chart mode or character-delta editor merging.
- The editor remains locked during an explicit Clean.
- Keep the existing **30-second reconnect timeout** as a safety net.
- Use whole-snapshot editor synchronization after a **300 ms** typing pause.
- Progress reads `Stage X of Y: [stage name] · N seconds elapsed`; never invent a percentage.
- Retry a pure computation once after worker transport failure; never silently run a large clean on the UI thread.
- Chart content stays in memory and on the private local pipe; never write chart content to worker logs or temporary files.
- `review_state.release_decision` remains the only release gate.
- Wall-clock thresholds belong in scheduled benchmarks, not per-PR assertions.
- Tests never touch real `data/`; retain the root `conftest.py` redirects and use `pytest.MonkeyPatch.context()` rather than `monkeypatch.undo()`.

## File Structure

- `chartcleaner/engine.py`: add an optional completed-stage observer; no cleaning behavior changes.
- `chartcleaner/review_html.py`: UI-independent diff HTML and context-range rendering.
- `chartcleaner/clean_compute.py`: pure Clean-page computation and serializable result bundle.
- `chartcleaner/clean_worker.py`: work protocol, inline test worker, spawned production worker, retry/cancel/shutdown.
- `chartcleaner/editor_sync.py`: monotonic server-side editor revision guard.
- `chartcleaner/static/clean/editor.js`: browser counts, debounce, acknowledgement, reconnect flush, and snapshot API.
- `chartcleaner/appstate.py`: hold the app-wide worker manager across NiceGUI test-harness re-execution.
- `chartcleaner/highlight_rules.py`: suppress ordinary typing and emit an empty selection only when clearing an existing selection.
- `app_pages/common.py`: compatibility imports for moved review HTML helpers.
- `app_pages/review_workspace.py`: import the moved context-range helper.
- `app_pages/clean.py`: submit worker jobs, show progress, use precomputed metadata, and flush editor snapshots before actions.
- `app.py`: warm and stop the worker; add a packaged-worker self-test entry point.
- `scripts/benchmark_clean_stability.py`: real-browser editing, reconnect, health-latency, and progress measurements.
- `scripts/release_smoke.py`: prove the frozen app can spawn its worker and load NLP.
- `tests/test_clean_compute.py`, `tests/test_clean_worker.py`, `tests/test_editor_sync.py`: focused contract tests.
- `tests/test_responsiveness.py`, `tests/test_performance_fixes_ui.py`: adapt existing seams to the extracted computation and worker interfaces.

---

### Task 1: Completed-stage progress observer

**Files:**
- Modify: `chartcleaner/engine.py:611-615,662-663,757-794`
- Test: `tests/test_responsiveness.py`

**Interfaces:**
- Consumes: existing `Pipeline.run(..., stop=...)` behavior.
- Produces: `Pipeline.run(..., progress: Callable[[str, str, int, int], None] | None = None)`, where each event is `(stage_id, stage_label, completed_count, total_count)` after a stage finishes.

- [ ] **Step 1: Write the failing progress test**

Append to `tests/test_responsiveness.py`:

```python
def test_pipeline_reports_each_completed_stage_with_a_stable_total():
    from chartcleaner.engine import Pipeline, load_default_config

    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    pipe = Pipeline(cfg)
    events = []
    result = pipe.run(
        "Plan:  recheck K 4.1 tomorrow\n",
        progress=lambda *event: events.append(event),
    )

    assert result.text
    assert len(events) == len(pipe.stages)
    assert [event[2] for event in events] == list(range(1, len(pipe.stages) + 1))
    assert {event[3] for event in events} == {len(pipe.stages)}
    assert [event[0] for event in events] == [stage.id for stage in pipe.stages]
    assert all(event[1] == stage.label for event, stage in zip(events, pipe.stages))
```

- [ ] **Step 2: Run the test and confirm the interface is absent**

Run: `.venv/bin/python -m pytest tests/test_responsiveness.py::test_pipeline_reports_each_completed_stage_with_a_stable_total -q`
Expected: FAIL with `TypeError: Pipeline.run() got an unexpected keyword argument 'progress'`.

- [ ] **Step 3: Add the observer without changing stage semantics**

Add `progress` to `Pipeline.run` after `stop`:

```python
    def run(self, text: str, wrap: bool | None = None, *,
            track_changes: bool = False, fact_check: bool | None = None,
            cache: Any = None, provenance: bool = False,
            validation: str | None = None,
            stop: Callable[[], bool] | None = None,
            progress: Callable[[str, str, int, int], None] | None = None) -> RunResult:
```

Replace the `extra` construction with:

```python
        extra = {}
        if stop is not None:
            extra["stop"] = stop
        if progress is not None:
            extra["progress"] = progress
        text, stage_stats, warnings = self._execute_stages(
            text, ctx, tracker, cache, session, **extra)
```

Add the matching `_execute_stages` parameter and emit after statistics, validation synchronization, and fact tracking, but before cancellation:

```python
    def _execute_stages(
        self, text: str, ctx: CleanContext, tracker: Any = None, cache: Any = None, session: Any = None,
        stop: Callable[[], bool] | None = None,
        progress: Callable[[str, str, int, int], None] | None = None,
    ) -> tuple[str, list[StageStat], list[str]]:
```

Inside the stage loop, immediately before `if stop is not None and stop():`, add:

```python
            if progress is not None:
                progress(spec.id, spec.label, len(stats), len(self.stages))
```

Extend the `Pipeline.run` docstring with:

```python
        ``progress(stage_id, stage_label, completed, total)`` is called after
        each completed stage. It is observational and must not alter the run.
```

- [ ] **Step 4: Run progress and cancellation tests**

Run: `.venv/bin/python -m pytest tests/test_responsiveness.py::test_pipeline_reports_each_completed_stage_with_a_stable_total tests/test_responsiveness.py::test_pipeline_stop_abandons_the_run_between_stages -q`
Expected: 2 PASS; the stop callback still raises at the same stage boundary.

- [ ] **Step 5: Commit**

```bash
git add chartcleaner/engine.py tests/test_responsiveness.py
git commit -m "feat: report pipeline stage progress"
```

---

### Task 2: UI-independent pure computation boundary

**Files:**
- Create: `chartcleaner/review_html.py`
- Create: `chartcleaner/clean_compute.py`
- Modify: `chartcleaner/stage_cache.py`, `chartcleaner/service.py`
- Modify: `app_pages/common.py:300-486`
- Modify: `app_pages/review_workspace.py:1-31`
- Test: `tests/test_clean_compute.py`
- Existing tests: `tests/test_a11y.py`, `tests/test_review_workspace_ui.py`, `tests/test_performance_fixes_ui.py`

**Interfaces:**
- Consumes: `Pipeline.run(..., progress=...)` from Task 1 and existing audit/insight/review functions.
- Produces:
  - `CleanComputation(result, audit, extras, review, groups, finding_rows)`.
  - `compute_clean(text, mode, cfg, custom_dir, lazy, stop=None, progress=None) -> CleanComputation`.
  - `edit_details(source, value, result, baseline, cfg, lazy) -> CleanComputation`.
  - `review_details(source, result, audit) -> tuple[str, int]`.
  - `insight_details(cleaned, fact_report, stop=None) -> dict`.
  - `review_html.review_diff_html(...)` and `review_html.collapse_context_ranges(...)`, with no NiceGUI import.

- [ ] **Step 1: Write failing parity and import-boundary tests**

Create `tests/test_clean_compute.py`:

```python
from __future__ import annotations

import subprocess
import sys

from chartcleaner.clean_compute import compute_clean, review_details
from chartcleaner.engine import Pipeline, load_default_config
from chartcleaner.review_state import release_decision


def _cfg():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    return cfg


def test_compute_bundle_preserves_pipeline_validation_and_provenance():
    cfg = _cfg()
    text = "Printed by Dr. Lee\nPlan: history of hypertension. Recheck K 4.1 tomorrow.\n"
    expected = Pipeline(cfg).run(text, track_changes=True)
    got = compute_clean(text, "clean", cfg, None, lazy=False)

    assert got.result.text == expected.text
    assert got.result.validation == expected.validation
    assert [p.to_summary() for p in got.result.ledger.proposals] == [
        p.to_summary() for p in expected.ledger.proposals]
    assert release_decision(got.result, output_text=got.result.text, source=text) == \
        release_decision(expected, output_text=expected.text, source=text)
    assert got.groups and got.finding_rows is not None
    assert got.review == review_details(text, got.result, got.audit)
    assert got.extras["redactions"] is not None


def test_lazy_compute_defers_full_review_and_nonessential_insights():
    got = compute_clean("Plan: recheck K 4.1\n", "clean", _cfg(), None, lazy=True)
    assert got.review is None
    assert set(got.extras) == {"safety", "conflicts", "timeline", "redactions"}


def test_review_html_module_does_not_import_nicegui():
    code = (
        "import sys; import chartcleaner.review_html; "
        "assert 'nicegui' not in sys.modules; "
        "print(chartcleaner.review_html.review_diff_html('a\\n', 'b\\n', None, []))"
    )
    completed = subprocess.run([sys.executable, "-c", code], check=True,
                               capture_output=True, text=True)
    assert "cc-diff" in completed.stdout
```

- [ ] **Step 2: Run the new tests and confirm the missing boundary**

Run: `.venv/bin/python -m pytest tests/test_clean_compute.py -q`
Expected: collection FAIL with `ModuleNotFoundError: chartcleaner.clean_compute`.

- [ ] **Step 3: Move review rendering out of the NiceGUI page layer**

Create `chartcleaner/review_html.py` by moving these existing bodies verbatim so the generated HTML does not change:

- `app_pages/common.py::_diff_table_start`
- `app_pages/common.py::_wrap_fragments`
- `app_pages/common.py::review_diff_html`
- `app_pages/review_workspace.py::collapse_context_ranges`

Use these imports and the same escaping behavior as `app_pages/common.py`:

```python
"""Review diff HTML with no NiceGUI or page-state dependency."""
from __future__ import annotations

from html import escape as _escape

from chartcleaner.edit_provenance import line_opcodes


def esc(s: str) -> str:
    return _escape(s)
```

In `review_diff_html`, delete `from app_pages.review_workspace import collapse_context_ranges`; the function now lives in the same module.

In `app_pages/common.py`, remove the moved bodies and retain public compatibility:

```python
from chartcleaner.review_html import _diff_table_start, review_diff_html
```

Keep `diff_html` in `common.py`; it continues using the imported `_diff_table_start`.

In `app_pages/review_workspace.py`, use:

```python
from chartcleaner.review_html import collapse_context_ranges
```

and remove its local function definition.

- [ ] **Step 4: Implement the pure computation module**

Move the process-wide stage-cache holder out of the integration service so the pure boundary does not import persistence. In `chartcleaner/stage_cache.py`, add:

```python
_PROCESS_CACHE: dict[str, StageCache | None] = {"cache": None}


def process_cache() -> StageCache:
    if _PROCESS_CACHE["cache"] is None:
        _PROCESS_CACHE["cache"] = StageCache()
    return _PROCESS_CACHE["cache"]
```

In `chartcleaner/service.py`, keep the public API but delegate to it:

```python
def result_cache():
    from .stage_cache import process_cache
    return process_cache()
```

Remove the old `_RESULT_CACHE` holder so there is still exactly one cache. Add `chartcleaner/stage_cache.py` and `chartcleaner/service.py` to this task's commit.

Create `chartcleaner/clean_compute.py` with this complete public boundary:

```python
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
```

- [ ] **Step 5: Run parity and existing review-rendering tests**

Run: `.venv/bin/python -m pytest tests/test_clean_compute.py tests/test_a11y.py tests/test_review_workspace_ui.py -q`
Expected: all PASS; existing HTML assertions remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add chartcleaner/review_html.py chartcleaner/clean_compute.py chartcleaner/stage_cache.py chartcleaner/service.py app_pages/common.py app_pages/review_workspace.py tests/test_clean_compute.py
git commit -m "refactor: isolate clean computation from the UI"
```

---

### Task 3: Work protocol and inline worker

**Files:**
- Create: `chartcleaner/clean_worker.py`
- Modify: `chartcleaner/appstate.py`
- Test: `tests/test_clean_worker.py`

**Interfaces:**
- Consumes: `compute_clean`, `edit_details`, `review_details`, and `insight_details` from Task 2.
- Produces:
  - `WorkRequest(job_id: str, kind: str, payload: dict)`.
  - `WorkProgress(job_id, stage_id, stage_label, completed, total, elapsed)`.
  - `execute_work(request, on_progress, cancel_event) -> object`.
  - `InlineCleanWorker.run(request, on_progress=None) -> object`, `.cancel(job_id)`, and `.close()`.
  - Supported kinds: `warm`, `compute`, `edit`, `review`, `insights`.

- [ ] **Step 1: Write failing inline protocol tests**

Create `tests/test_clean_worker.py`:

```python
from __future__ import annotations

import asyncio
import uuid

import pytest

from chartcleaner.clean_worker import InlineCleanWorker, WorkRequest, WorkProgress
from chartcleaner.engine import load_default_config


def _request(kind="compute", text="Plan: recheck K 4.1 tomorrow\n"):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    payload = {"text": text, "mode": "clean", "cfg": cfg,
               "custom_dir": None, "lazy": False}
    return WorkRequest(str(uuid.uuid4()), kind, payload)


async def test_inline_worker_returns_compute_bundle_and_progress():
    worker = InlineCleanWorker()
    events = []
    loop = asyncio.get_running_loop()

    def progressed(event):
        assert asyncio.get_running_loop() is loop
        events.append(event)

    got = await worker.run(_request(), progressed)
    assert got.result.text
    assert events and all(isinstance(event, WorkProgress) for event in events)
    assert events[-1].completed == events[-1].total
    await worker.close()


async def test_inline_worker_executes_edit_review_and_insights():
    worker = InlineCleanWorker()
    computed = await worker.run(_request())
    edited = await worker.run(WorkRequest(str(uuid.uuid4()), "edit", {
        "source": "Plan: recheck K 4.1 tomorrow\n", "value": "Plan: recheck K 4.2\n",
        "result": computed.result, "baseline": computed.result.fact_check,
        "cfg": _request().payload["cfg"], "lazy": False,
    }))
    review = await worker.run(WorkRequest(str(uuid.uuid4()), "review", {
        "source": "Plan: recheck K 4.2\n",
        "result": edited.result, "audit": edited.audit,
    }))
    insights = await worker.run(WorkRequest(str(uuid.uuid4()), "insights", {
        "cleaned": edited.result.text,
        "fact_report": edited.result.fact_check,
    }))
    assert edited.result.text == "Plan: recheck K 4.2\n" and edited.finding_rows
    assert review[1] >= 0 and "timeline" in insights
    await worker.close()


def test_unknown_work_kind_is_rejected():
    with pytest.raises(ValueError, match="Unknown work kind"):
        from chartcleaner.clean_worker import execute_work
        import threading
        execute_work(WorkRequest("bad", "unknown", {}), None, threading.Event())
```

- [ ] **Step 2: Run tests and confirm the protocol is absent**

Run: `.venv/bin/python -m pytest tests/test_clean_worker.py -q`
Expected: collection FAIL with `ModuleNotFoundError: chartcleaner.clean_worker`.

- [ ] **Step 3: Implement protocol, work dispatch, and inline execution**

Create `chartcleaner/clean_worker.py`:

```python
"""Local Clean-page work protocol and worker implementations."""
from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

from nicegui import run

from chartcleaner import clean_compute
from chartcleaner.appstate import WORKER_STATE
from chartcleaner.engine import PipelineCancelled


@dataclass(frozen=True)
class WorkRequest:
    job_id: str
    kind: str
    payload: dict


@dataclass(frozen=True)
class WorkProgress:
    job_id: str
    stage_id: str
    stage_label: str
    completed: int
    total: int
    elapsed: float


class WorkerError(RuntimeError):
    pass


ProgressCallback = Callable[[WorkProgress], None]


def execute_work(request: WorkRequest, on_progress: ProgressCallback | None,
                 cancel_event: threading.Event) -> object:
    started = time.perf_counter()

    def progress(stage_id: str, label: str, completed: int, total: int) -> None:
        if on_progress is not None:
            on_progress(WorkProgress(request.job_id, stage_id, label, completed, total,
                                     time.perf_counter() - started))

    if request.kind == "warm":
        from chartcleaner.abbreviations import _matcher
        _matcher()
        cfg = request.payload["cfg"]
        if bool((cfg.get("nlp_redaction") or {}).get("enabled", True)):
            from chartcleaner.stage_nlp import warm
            warm(cfg)
        return None
    if cancel_event.is_set():
        raise PipelineCancelled("before start")
    if request.kind == "compute":
        p = request.payload
        return clean_compute.compute_clean(
            p["text"], p["mode"], p["cfg"], p["custom_dir"], p["lazy"],
            stop=cancel_event.is_set, progress=progress)
    if request.kind == "edit":
        p = request.payload
        return clean_compute.edit_details(
            p["source"], p["value"], p["result"], p["baseline"], p["cfg"], p["lazy"])
    if request.kind == "review":
        p = request.payload
        return clean_compute.review_details(p["source"], p["result"], p["audit"])
    if request.kind == "insights":
        p = request.payload
        return clean_compute.insight_details(
            p["cleaned"], p["fact_report"], stop=cancel_event.is_set)
    raise ValueError(f"Unknown work kind: {request.kind}")


class InlineCleanWorker:
    """Test-harness worker: same protocol, existing thread-offload test seams."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._cancel = threading.Event()
        self._active: str | None = None
        self._closing = False

    def cancel(self, job_id: str) -> None:
        if self._active == job_id:
            self._cancel.set()

    async def run(self, request: WorkRequest,
                  on_progress: ProgressCallback | None = None) -> object:
        if self._closing:
            raise WorkerError("Cleaning worker is closed")
        loop = asyncio.get_running_loop()

        def progress(event: WorkProgress) -> None:
            if on_progress is not None:
                loop.call_soon_threadsafe(on_progress, event)

        async with self._lock:
            self._active = request.job_id
            self._cancel.clear()
            try:
                value = await run.io_bound(execute_work, request, progress, self._cancel)
                if value is None and request.kind != "warm":
                    raise WorkerError("Cleaning worker was interrupted")
                return value
            finally:
                self._active = None

    async def close(self) -> None:
        self._closing = True
        if self._active is not None:
            self._cancel.set()


def get_clean_worker():
    manager = WORKER_STATE.get("manager")
    if manager is None:
        manager = InlineCleanWorker()
        WORKER_STATE["manager"] = manager
    return manager


async def shutdown_clean_worker() -> None:
    manager = WORKER_STATE.pop("manager", None)
    if manager is not None:
        await manager.close()
```

Add `WORKER_STATE: dict = {"manager": None}` to `chartcleaner/appstate.py` and include it in `__all__`.

- [ ] **Step 4: Run the inline protocol tests**

Run: `.venv/bin/python -m pytest tests/test_clean_worker.py -q`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add chartcleaner/clean_worker.py chartcleaner/appstate.py tests/test_clean_worker.py
git commit -m "feat: add clean work protocol and inline worker"
```

---

### Task 4: Spawned production worker with retry and cancellation

**Files:**
- Modify: `chartcleaner/clean_worker.py`
- Test: `tests/test_clean_worker.py`

**Interfaces:**
- Consumes: `WorkRequest`, `WorkProgress`, and `execute_work` from Task 3.
- Produces: `ProcessCleanWorker` with the same public `run`, `cancel`, and `close` interface; `get_clean_worker()` selects it unless `NICEGUI_USER_SIMULATION` is set.

- [ ] **Step 1: Add failing real-process and retry tests**

Append to `tests/test_clean_worker.py`:

```python
from chartcleaner.clean_worker import ProcessCleanWorker, WorkerError
from chartcleaner.engine import PipelineCancelled


async def test_process_worker_round_trip_and_shutdown():
    worker = ProcessCleanWorker()
    try:
        got = await worker.run(_request())
        assert "K 4.1" in got.result.text
        process = worker._process
        assert process is not None and process.is_alive()
    finally:
        await worker.close()
    assert not process.is_alive()


async def test_process_worker_retries_once_after_transport_failure(monkeypatch):
    worker = ProcessCleanWorker()
    calls = []
    original = worker._read

    async def fail_once():
        calls.append(1)
        if len(calls) == 2:  # ready succeeds; first work receive fails
            raise EOFError("synthetic closed pipe")
        return await original()

    monkeypatch.setattr(worker, "_read", fail_once)
    try:
        got = await worker.run(_request())
        assert got.result.text
        assert len(calls) >= 3
    finally:
        await worker.close()


async def test_process_worker_does_not_retry_a_computation_error():
    request = _request()
    request.payload["cfg"] = {}
    worker = ProcessCleanWorker()
    starts = 0
    original = worker._start

    async def counted_start():
        nonlocal starts
        starts += 1
        await original()

    worker._start = counted_start
    try:
        with pytest.raises(WorkerError, match="ConfigError"):
            await worker.run(request)
        assert starts == 1
    finally:
        await worker.close()


async def test_cancellation_is_not_retried():
    worker = ProcessCleanWorker()
    request = _request(text="Plan: recheck K 4.1\n" * 20000)
    seen = []

    async def run_job():
        with pytest.raises(PipelineCancelled):
            await worker.run(request, seen.append)

    import asyncio
    task = asyncio.create_task(run_job())
    for _ in range(300):
        if seen:
            break
        await asyncio.sleep(0.02)
    assert seen, "worker never reached a stage boundary"
    worker.cancel(request.job_id)
    await task
    await worker.close()
```

- [ ] **Step 2: Run the real-process tests and confirm the class is absent**

Run: `.venv/bin/python -m pytest tests/test_clean_worker.py -q`
Expected: collection FAIL with `ImportError` for `ProcessCleanWorker`.

- [ ] **Step 3: Implement the spawned worker and private child loop**

Add these imports to `clean_worker.py`:

```python
import multiprocessing as mp


class WorkerTransportError(RuntimeError):
    """Startup, handshake, protocol, pipe, EOF, or timeout failure; safe to retry."""
```

Keep `WorkerError` for worker-returned computation failures and a closed manager. Retry only `WorkerTransportError` and transport exceptions; never retry `WorkerError` or `PipelineCancelled`.

Add the child entry point and production manager:

```python
def _child_main(conn, cancel_event) -> None:
    conn.send({"type": "ready"})
    while True:
        try:
            message = conn.recv()
        except EOFError:
            return
        if message["type"] == "shutdown":
            return
        request = message["request"]
        try:
            def progress(event: WorkProgress) -> None:
                conn.send({"type": "progress", "event": event})
            payload = execute_work(request, progress, cancel_event)
            conn.send({"type": "result", "job_id": request.job_id, "payload": payload})
        except PipelineCancelled:
            conn.send({"type": "cancelled", "job_id": request.job_id})
        except Exception as exc:
            conn.send({"type": "error", "job_id": request.job_id,
                       "error": f"{type(exc).__name__}: {exc}"})


class ProcessCleanWorker:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._ctx = mp.get_context("spawn")
        self._cancel = self._ctx.Event()
        self._process = None
        self._conn = None
        self._active: str | None = None
        self._closing = False

    def cancel(self, job_id: str) -> None:
        if self._active == job_id:
            self._cancel.set()

    async def _read(self):
        value = await run.io_bound(self._conn.recv)
        if value is None:
            raise EOFError("worker read interrupted")
        return value

    async def _write(self, value) -> None:
        sent = await run.io_bound(self._conn.send, value)
        if sent is None:
            raise BrokenPipeError("worker write interrupted")

    async def _start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        parent, child = self._ctx.Pipe(duplex=True)
        process = self._ctx.Process(
            target=_child_main, args=(child, self._cancel),
            name="chart-cleaner-worker", daemon=True)
        self._conn, self._process = parent, process
        try:
            await run.io_bound(process.start)
            child.close()
            message = await asyncio.wait_for(self._read(), timeout=60)
            if message != {"type": "ready"}:
                raise WorkerTransportError("Cleaning worker handshake failed")
        except Exception:
            await self._restart()
            raise

    async def _restart(self) -> None:
        process, conn = self._process, self._conn
        self._process = self._conn = None
        if conn is not None:
            conn.close()
        if process is not None:
            if process.is_alive():
                process.terminate()
            await run.io_bound(process.join, 10)

    async def run(self, request: WorkRequest,
                  on_progress: ProgressCallback | None = None) -> object:
        if self._closing:
            raise WorkerError("Cleaning worker is closed")
        async with self._lock:
            self._active = request.job_id
            self._cancel.clear()
            try:
                for attempt in range(2):
                    try:
                        if self._closing:
                            raise WorkerError("Cleaning worker is closed")
                        await self._start()
                        await self._write({"type": "work", "request": request})
                        while True:
                            message = await self._read()
                            kind = message.get("type") if isinstance(message, dict) else None
                            if kind is None:
                                raise WorkerTransportError("Cleaning worker protocol failed")
                            if kind == "progress":
                                event = message["event"]
                                if event.job_id == request.job_id and on_progress is not None:
                                    on_progress(event)
                                continue
                            if message.get("job_id") != request.job_id:
                                raise WorkerTransportError("Cleaning worker returned a mismatched job")
                            if kind == "result":
                                return message["payload"]
                            if kind == "cancelled":
                                raise PipelineCancelled(request.job_id)
                            if kind == "error":
                                raise WorkerError(message["error"])
                            raise WorkerTransportError(f"Unknown worker message: {kind}")
                    except PipelineCancelled:
                        raise
                    except WorkerError:
                        raise
                    except (WorkerTransportError, EOFError, BrokenPipeError,
                            OSError, asyncio.TimeoutError) as exc:
                        await self._restart()
                        if attempt == 1:
                            raise WorkerError("Cleaning worker failed twice") from exc
                raise AssertionError("unreachable")
            finally:
                self._active = None

    async def close(self) -> None:
        self._closing = True
        process = self._process
        if self._active is not None:
            self._cancel.set()
            if process is not None and process.is_alive():
                process.terminate()  # do not wait for an unresponsive active job
        async with self._lock:
            if self._process is not None and self._process.is_alive():
                try:
                    await self._write({"type": "shutdown"})
                    await run.io_bound(self._process.join, 5)
                except Exception:
                    pass
            await self._restart()
```

Change `get_clean_worker` to:

```python
def get_clean_worker():
    manager = WORKER_STATE.get("manager")
    if manager is None:
        manager = (InlineCleanWorker() if os.environ.get("NICEGUI_USER_SIMULATION")
                   else ProcessCleanWorker())
        WORKER_STATE["manager"] = manager
    return manager
```

- [ ] **Step 4: Run all worker contract tests**

Run: `.venv/bin/python -m pytest tests/test_clean_worker.py -q`
Expected: 7 PASS; the retry test starts a replacement process, computation errors start only one process, cancellation is not retried, and every process exits.

- [ ] **Step 5: Commit**

```bash
git add chartcleaner/clean_worker.py tests/test_clean_worker.py
git commit -m "feat: isolate cleaning in a reusable worker process"
```

---

### Task 5: Clean-page and application worker integration

**Files:**
- Modify: `app_pages/clean.py:93-438,449-565`
- Modify: `app.py:18-24,122-171,181-186`
- Modify: `tests/test_responsiveness.py`
- Modify: `tests/test_performance_fixes_ui.py`

**Interfaces:**
- Consumes: `get_clean_worker().run(WorkRequest(...))`, `WorkProgress`, `CleanComputation`.
- Produces: `CLEAN_STATE["groups"]` and `CLEAN_STATE["finding_rows"]`; a `clean-progress` UI marker; identical persisted history and result behavior.

- [ ] **Step 1: Write failing UI progress and worker-integration tests**

Append to `tests/test_responsiveness.py`:

```python
async def test_clean_uses_worker_and_displays_stage_progress(user: User, page, monkeypatch):
    from chartcleaner import clean_worker

    events = []
    started, release = asyncio.Event(), asyncio.Event()
    real = clean_worker.InlineCleanWorker.run

    async def observed(self, request, on_progress=None):
        events.append(request)

        def progress(event):
            if on_progress is not None:
                on_progress(event)
            started.set()

        job = asyncio.create_task(real(self, request, progress))
        await started.wait()
        await release.wait()  # keep the progress label visible for the assertion
        return await job

    monkeypatch.setattr(clean_worker.InlineCleanWorker, "run", observed)
    CLEAN_STATE.update(input="Printed by Dr. Lee\nPlan: recheck K 4.1\n", mode="clean",
                       result=None, result_text="", audit=None, summary=None, qa=[], tag="")
    await _route(user, "/resp-worker-progress", cc_app.clean_page)
    user.find(marker="run-clean").click()
    await asyncio.wait_for(started.wait(), timeout=2)
    await user.should_see(marker="clean-progress")
    assert [request.kind for request in events] == ["compute"]
    assert events[0].payload["lazy"] is False
    release.set()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    assert CLEAN_STATE.get("result") is not None


async def test_failed_worker_preserves_previous_result_and_unlocks(user: User, page, monkeypatch):
    from chartcleaner import clean_worker

    await _clean(user, "/resp-worker-failure", "Plan: previous result\n")
    previous = CLEAN_STATE["result"]

    async def fail(self, request, on_progress=None):
        raise clean_worker.WorkerError("synthetic worker failure")

    monkeypatch.setattr(clean_worker.InlineCleanWorker, "run", fail)
    monkeypatch.setattr(clean_mod, "report_error", lambda *args: None)
    textarea = next(iter(user.find(marker="highlight-source").elements))
    textarea.set_value("Plan: replacement chart\n")
    user.find(marker="run-clean").click()
    for _ in range(300):
        if next(iter(user.find(marker="run-clean").elements)).enabled:
            break
        await asyncio.sleep(0.02)
    assert CLEAN_STATE["result"] is previous
    assert "inert" not in next(iter(user.find(marker="input-shell").elements)).props
```

Update `test_optional_computation_is_lazy_and_off_loop` in `tests/test_performance_fixes_ui.py` to patch the new pure boundary:

```python
    diff, trends = clean_compute.review_diff_html, clean_compute.build_trends
    monkeypatch.setattr(clean_compute, "review_diff_html", build_diff)
    monkeypatch.setattr(clean_compute, "build_trends", build_trends)
```

Add `from chartcleaner import clean_compute` to that test module. Keep the assertions that Review and Insights are built only after their tabs open.

Adapt the other tests whose seams move, without weakening their assertions:

- In `test_interrupted_clean_preserves_previous_result`, change the parametrized callback names from `["prepare", "compute"]` to `["prepare", "execute_work"]`; retain the same failure/cancellation, history, previous-result, and unlocked-input assertions.
- In edited-output lifecycle tests, patch `clean_compute.run_audit` instead of `clean_mod.run_audit`; retain the `clean_compute.review_diff_html` and `clean_compute.build_trends` patches above.
- Rewrite `test_newer_text_cancels_a_stale_speculative_run` without `COMPUTE_LOCK`. Gate `clean_worker.execute_work` at the `run.io_bound` seam for the first speculative request, wait until that request is active, submit newer text so `InlineCleanWorker.cancel(first_job_id)` sets its event, then release the gate. The original `execute_work` must observe cancellation before calling `Pipeline.run`; preserve assertions that the first text never reaches the pipeline, only the second text is cleaned, and the speculative-cache hit count is one.

- [ ] **Step 2: Run the new tests and confirm worker calls are absent**

Run: `.venv/bin/python -m pytest tests/test_responsiveness.py::test_clean_uses_worker_and_displays_stage_progress -q`
Expected: FAIL because no `WorkRequest` is submitted and no progress marker exists.

- [ ] **Step 3: Replace nested computation with worker requests**

In `app_pages/clean.py`, add:

```python
import uuid
from chartcleaner import clean_compute
from chartcleaner.clean_worker import WorkRequest, get_clean_worker
```

Delete the nested `compute`, `extras_work`, `change_groups`, `flagged_lines`, and `review_html` functions from `clean_page`; their implementations now live in `clean_compute`.

Add near the other page state:

```python
    state = {"running": False, "edited": 0.0, "speculated": 0.0, "job_id": None}
```

In the running UI, add a small status label beside the existing spinner:

```python
    progress_label = ui.label("").classes("text-xs cc-muted").mark("clean-progress")
    progress_label.set_visibility(False)
```

`set_running` must also clear or show it:

```python
    def set_running(flag: bool) -> None:
        # keep the existing control and inert handling unchanged
        progress_label.set_visibility(flag)
        if not flag:
            state["job_id"] = None
            progress_label.set_text("")
```

Create one helper inside `clean_page`:

```python
    def work_request(kind: str, payload: dict, job_id: str | None = None) -> WorkRequest:
        return WorkRequest(job_id or str(uuid.uuid4()), kind, payload)


    def show_work_progress(event) -> None:
        if state["job_id"] != event.job_id:
            return
        progress_label.set_text(
            f"Stage {event.completed} of {event.total}: {event.stage_label} · "
            f"{int(event.elapsed)} seconds elapsed")
```

In `speculate.go`, replace direct `prepare/compute` offload with:

```python
                cfg, _note, digest = await run.io_bound(prepare, *inputs)
                if stop():
                    raise PipelineCancelled("before submit")
                job_id = str(uuid.uuid4())
                spec["job_id"] = job_id
                request = work_request("compute", {
                    "text": inputs[0], "mode": inputs[1], "cfg": cfg,
                    "custom_dir": str(common.CUSTOM_DIR),
                    "lazy": len(inputs[0]) >= LAZY_TABS_MIN_CHARS,
                }, job_id)
                return digest, await get_clean_worker().run(request)
```

Whenever new input invalidates speculation, call `get_clean_worker().cancel(spec["job_id"])` before clearing `spec["job_id"]`; cancel only the recorded speculative job.

In `do_clean_core`, replace `out = await run.io_bound(compute, text, mode, cfg)` with:

```python
                request = work_request("compute", {
                    "text": text, "mode": mode, "cfg": cfg,
                    "custom_dir": str(common.CUSTOM_DIR),
                    "lazy": len(text) >= LAZY_TABS_MIN_CHARS,
                })
                state["job_id"] = request.job_id
                out = await get_clean_worker().run(request, show_work_progress)
```

Both explicit and speculative paths now yield `CleanComputation`. Unpack with:

```python
            result, audit, extras = out.result, out.audit, out.extras
            review, groups, finding_rows = out.review, out.groups, out.finding_rows
```

Add these keys to the successful `CLEAN_STATE.update`:

```python
                               groups=groups, finding_rows=finding_rows,
```

In `render_results`, replace local recomputation with:

```python
            groups = CLEAN_STATE.get("groups")
            if groups is None:
                groups = clean_compute.change_groups(result)  # tests that inject a result directly
            refs["groups"] = groups
            finding_rows = CLEAN_STATE.get("finding_rows")
            if finding_rows is None:
                finding_rows = review_workspace.build_finding_rows(
                    result, CLEAN_STATE.get("audit"), source=CLEAN_STATE["input"],
                    output=CLEAN_STATE.get("result_text") or result.text)
```

Replace `ensure_review` and `ensure_insights` worker bodies with:

```python
        request = work_request("review", {
            "source": source, "result": result, "audit": CLEAN_STATE.get("audit")})
        built = await get_clean_worker().run(request)
```

and:

```python
        request = work_request("insights", {
            "cleaned": result.text, "fact_report": result.fact_check})
        extras = await get_clean_worker().run(request)
```

Retain the existing identity checks before publishing lazy results.

Move edited-output verification to the worker as well; do not leave `commit_edit` calling the deleted nested helpers. Replace its nested `work()` and `run.io_bound(work)` with a request built from the existing local variables:

```python
            cfg = await run.io_bound(load_config, common.CONFIG_PATH)
            request = work_request("edit", {
                "source": source, "value": value, "result": result,
                "baseline": baseline, "cfg": cfg,
                "lazy": len(source) >= LAZY_TABS_MIN_CHARS,
            })
            try:
                checked = await get_clean_worker().run(request)
            except Exception as exc:
                with results_col.client:
                    report_error("Could not check edited output", exc)
                return False
```

Retain the existing result/value/running identity checks. Then unpack the full `CleanComputation`, not the old four-tuple:

```python
            edited, audit, review = checked.result, checked.audit, checked.review
            candidates = checked.extras["redactions"]
            CLEAN_STATE.update(
                result=edited, result_text=value, audit=audit,
                redactions=(source, value, candidates), insights_ready=False,
                review_html=(edited, source, value, *review) if review else None,
                groups=checked.groups, finding_rows=checked.finding_rows)
```

This refreshes Review findings and change groups for the edited text instead of retaining stale pre-edit rows. Add `groups` and `finding_rows` to the result-derived keys cleared by both `clear_all` and `invalidate_output`; tests that inject results directly still use the fallback recomputation in `render_results`.

- [ ] **Step 4: Warm and stop the production worker in `app.py`**

Replace the parent-thread `_warm_nlp_engines` body with a background worker request:

```python
def _warm_nlp_engines() -> None:
    if os.environ.get("NICEGUI_USER_SIMULATION"):
        return

    async def warm() -> None:
        try:
            cfg = load_config(common.CONFIG_PATH)
            request = WorkRequest("startup-warm", "warm", {"cfg": cfg})
            await get_clean_worker().run(request)
        except Exception:
            pass  # startup warm-up is best-effort; never leave an unhandled task

    try:
        asyncio.get_running_loop().create_task(warm())
    except Exception:
        pass
```

Add these imports:

```python
import asyncio
from chartcleaner.clean_worker import WorkRequest, get_clean_worker, shutdown_clean_worker
```

Remove the now-unused `threading` import. Register shutdown next to startup:

```python
app.on_shutdown(shutdown_clean_worker)
```

- [ ] **Step 5: Run Clean-page responsiveness and performance tests**

Run: `.venv/bin/python -m pytest tests/test_responsiveness.py tests/test_performance_fixes_ui.py tests/test_clean_review_ui.py -q`
Expected: all PASS; interrupted/failed runs preserve previous results, Review/Insights stay lazy, speculation reuses only matching digests, and progress uses the worker.

- [ ] **Step 6: Commit**

```bash
git add app_pages/clean.py app.py tests/test_responsiveness.py tests/test_performance_fixes_ui.py
git commit -m "perf: run Clean page work outside the UI process"
```

---

### Task 6: Revision-safe browser editor synchronization

**Files:**
- Create: `chartcleaner/editor_sync.py`
- Create: `chartcleaner/static/clean/editor.js`
- Modify: `chartcleaner/highlight_rules.py:54-66`
- Modify: `app_pages/clean.py:120,2090-2200,2679-2700,3550-3622`
- Test: `tests/test_editor_sync.py`

**Interfaces:**
- Consumes: browser `window.ccCleanEditor.snapshot() -> {value, revision}` and `.ack(revision)`.
- Produces: `EditorRevisions.accept(revision) -> bool`; server `sync_editor() -> str`; debounced `on_editor_sync` events; a `chart-counts` marker.

- [ ] **Step 1: Write failing revision tests**

Create `tests/test_editor_sync.py`:

```python
from chartcleaner.editor_sync import EditorRevisions


def test_editor_revisions_accept_only_newer_snapshots():
    revisions = EditorRevisions()
    assert revisions.accept(1)
    assert revisions.accept(2)
    assert not revisions.accept(2)
    assert not revisions.accept(1)
    assert revisions.accept(3)


def test_editor_revisions_reject_non_integers_and_bool():
    revisions = EditorRevisions()
    for value in (True, 1.0, "1", None):
        assert not revisions.accept(value)
    assert revisions.accept(1)
```

- [ ] **Step 2: Run tests and confirm the guard is absent**

Run: `.venv/bin/python -m pytest tests/test_editor_sync.py -q`
Expected: collection FAIL with `ModuleNotFoundError: chartcleaner.editor_sync`.

- [ ] **Step 3: Add the revision guard and browser module**

Create `chartcleaner/editor_sync.py`:

```python
"""Per-page monotonic guard for full editor snapshots."""
from dataclasses import dataclass


@dataclass
class EditorRevisions:
    current: int = 0

    def accept(self, revision) -> bool:
        if type(revision) is not int or revision <= self.current:
            return False
        self.current = revision
        return True
```

Create `chartcleaner/static/clean/editor.js`:

```javascript
(() => {
  const state = {revision: 0, acknowledged: 0, lastValue: null,
                 timer: null, raf: null, latestEmit: null};
  const selector = '[mark="highlight-source"] textarea';
  const textarea = () => document.querySelector(selector);

  function syncValue(ta) {
    if (ta && ta.value !== state.lastValue) {
      state.lastValue = ta.value;
      state.revision += 1;
    }
  }

  function counts(text) {
    const trimmed = text.trim();
    return {characters: Array.from(text).length,
            words: trimmed ? trimmed.split(/\s+/u).length : 0};
  }

  function drawCounts(ta) {
    if (state.raf) return;
    state.raf = requestAnimationFrame(() => {
      state.raf = null;
      const label = document.querySelector('[mark="chart-counts"]');
      if (!label || !ta) return;
      const value = counts(ta.value);
      label.textContent = `${value.characters.toLocaleString()} characters / ` +
                          `${value.words.toLocaleString()} words`;
    });
  }

  function snapshot() {
    const ta = textarea();
    if (!ta) return null;
    syncValue(ta);
    return {value: ta.value, revision: state.revision};
  }

  function flush() {
    if (!state.latestEmit) return;
    const value = snapshot();
    if (value && value.revision > state.acknowledged) {
      state.latestEmit(value);
    }
  }

  function input(event, emit) {
    const ta = event.target;
    if (!(ta instanceof HTMLTextAreaElement)) return;
    state.latestEmit = emit;
    syncValue(ta);
    drawCounts(ta);
    clearTimeout(state.timer);
    state.timer = setTimeout(flush, 300);
  }

  function ack(revision) {
    if (Number.isInteger(revision)) {
      state.acknowledged = Math.max(state.acknowledged, revision);
    }
  }

  window.ccCleanEditor = {input, snapshot, ack, counts};
  setInterval(flush, 1000);  // re-send unacknowledged edits after socket recovery
})();
```

- [ ] **Step 4: Connect the editor with action-time flushing**

At the start of `clean_page`, add:

```python
    ui.add_head_html('<script src="/static/clean/editor.js"></script>')
```

Add page state:

```python
    from chartcleaner.editor_sync import EditorRevisions
    editor_revisions = EditorRevisions()
```

Replace `on_input_change` with these helpers:

```python
    def accept_editor_snapshot(data) -> bool:
        if not isinstance(data, dict):
            return False
        value = data.get("value")
        if not isinstance(value, str) or not editor_revisions.accept(data.get("revision")):
            return False  # malformed values must not consume a new revision
        if value != CLEAN_STATE.get("input"):
            job_id = spec.get("job_id")
            if job_id:
                get_clean_worker().cancel(job_id)
            spec.update(task=None, inputs=None, job_id=None)
            CLEAN_STATE.pop("manual_note_type", None)
        CLEAN_STATE.update(input=value)
        input_area.value = value  # server-side value only; do not resend the textarea
        state["edited"] = time.monotonic()
        input_area.client.run_javascript(
            f"window.ccCleanEditor.ack({data['revision']})")
        return True


    def on_editor_sync(event) -> None:
        accept_editor_snapshot(event.args)


    async def sync_editor() -> str:
        if os.environ.get("NICEGUI_USER_SIMULATION"):
            value = input_area.value or ""
            CLEAN_STATE["input"] = value  # textarea.set_value/upload tests bypass browser JS
            return value
        data = await input_area.client.run_javascript(
            "return window.ccCleanEditor.snapshot();")
        if data is not None:
            accept_editor_snapshot(data)
        return CLEAN_STATE.get("input") or ""
```

Construct the textarea without `on_change=on_input_change`, then attach:

```python
    input_area.on(
        "input", on_editor_sync,
        js_handler="(event) => window.ccCleanEditor.input(event, emit)")
```

At the start of `run_clean`, `handle_upload`, and `on_highlight`, use:

```python
        await sync_editor()
```

Make `on_highlight` asynchronous. Before `save_collected` and `undo_highlight` consume chart text, make them asynchronous and await `sync_editor()`; pass the async functions directly to NiceGUI rather than wrapping them in lambdas that discard coroutines.

Replace the server-bound character/word label with:

```python
    initial = CLEAN_STATE.get("input") or ""
    ui.label(f"{len(initial):,} characters / {len(initial.split()):,} words") \
        .classes("text-xs opacity-60").mark("chart-counts")
```

Update `SELECTION_HANDLER` in `chartcleaner/highlight_rules.py` to:

```python
SELECTION_HANDLER = """(event) => {
    if (event.type === 'keyup' && (event.shiftKey || event.ctrlKey || event.metaKey)) return;
    const ta = event.target;
    if (!(ta instanceof HTMLTextAreaElement)) return;
    const start = ta.selectionStart, end = ta.selectionEnd;
    if (start === end || !ta.value.slice(start, end).trim()) {
        if (ta.dataset.ccHadSelection === '1') {
            ta.dataset.ccHadSelection = '0';
            emit({start: 0, end: 0, text: ''});
        }
        return;
    }
    ta.dataset.ccHadSelection = '1';
    emit({start: Array.from(ta.value.slice(0, start)).length,
          end: Array.from(ta.value.slice(0, end)).length,
          text: ta.value.slice(start, end)});
}"""
```

In `on_highlight`, after the initial state guards, handle clearing explicitly:

```python
        selection = e.args
        if isinstance(selection, dict) and not selection.get("text"):
            hide_selection_bar()
            return
```

- [ ] **Step 5: Run revision and existing selection/clean tests**

Run: `.venv/bin/python -m pytest tests/test_editor_sync.py tests/test_highlight_rules.py tests/test_responsiveness.py tests/test_clean_review_ui.py -q`
Expected: all PASS; stale revisions cannot overwrite newer text and existing selection behavior remains intact.

- [ ] **Step 6: Commit**

```bash
git add chartcleaner/editor_sync.py chartcleaner/static/clean/editor.js chartcleaner/highlight_rules.py app_pages/clean.py tests/test_editor_sync.py
git commit -m "perf: bound large chart editor synchronization"
```

---

### Task 7: Real-browser stability benchmark

**Files:**
- Create: `scripts/benchmark_clean_stability.py`
- Modify: `.github/workflows/accuracy.yml:63-85`

**Interfaces:**
- Consumes: a running app at `BASE_URL`, the new `clean-progress` and `chart-counts` markers, and unauthenticated `/api/v1/health`.
- Produces: numeric JSON only; no chart text. Default workload: 10 cycles at each of 100,000, 250,000, and 500,000 characters, with a per-size report.

- [ ] **Step 1: Add a failing parser/report test**

Create `tests/test_clean_stability_benchmark.py`:

```python
from scripts.benchmark_clean_stability import percentile, summarize


def test_stability_summary_has_no_chart_text_and_checks_targets():
    report = summarize([10, 20, 30], [20, 40, 60], [1000, 2000], [True] * 10, 0,
                       expected_cycles=10)
    assert report["health_p95_ms"] == 20
    assert report["typing_p95_ms"] == 40
    assert report["warm_clean_ms"] == 2000
    assert report["cycles"] == 10
    assert report["reconnects"] == 0
    assert report["passed"] is True
    assert "chart" not in report


def test_percentile_uses_nearest_rank():
    assert percentile([1, 2, 3, 4], 0.95) == 4
```

- [ ] **Step 2: Run the benchmark unit tests**

Run: `.venv/bin/python -m pytest tests/test_clean_stability_benchmark.py -q`
Expected: collection FAIL because `scripts.benchmark_clean_stability` does not exist.

- [ ] **Step 3: Implement the numeric benchmark**

Create `scripts/benchmark_clean_stability.py`:

```python
"""Measure Clean-page stability against a running app; emits numbers only."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from scripts.benchmark_validation import synth

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8765")


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))]


def summarize(health, typing, clean_ms, progress_seen, reconnects, expected_cycles):
    complete = len(progress_seen) == expected_cycles
    passed = (
        reconnects == 0 and complete and all(progress_seen)
        and percentile(health, .95) <= 100 and max(health) <= 1000
        and percentile(typing, .95) <= 100 and max(clean_ms) <= 30000
    )
    return {
        "cycles": len(progress_seen), "reconnects": reconnects,
        "health_p95_ms": percentile(health, .95), "health_max_ms": max(health),
        "typing_p95_ms": percentile(typing, .95), "warm_clean_ms": max(clean_ms),
        "progress_within_1s": complete and all(progress_seen), "passed": passed,
    }


class HealthProbe:
    def __init__(self):
        self.samples = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.perf_counter()
            with urllib.request.urlopen(BASE_URL + "/api/v1/health", timeout=2) as response:
                response.read()
            self.samples.append((time.perf_counter() - started) * 1000)
            self._stop.wait(.05)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join(3)


def measure(cycles: int, chars: int) -> dict:
    health, typing, clean_ms, progress_seen = [], [], [], []
    reconnects = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script("""
          window.__ccWsBytes = 0;
          const NativeWebSocket = window.WebSocket;
          window.WebSocket = class extends NativeWebSocket {
            send(data) {
              if (typeof data === 'string') window.__ccWsBytes += data.length;
              super.send(data);
            }
          };
        """)
        page.goto(BASE_URL + "/", wait_until="networkidle")
        for cycle in range(cycles):
            page.locator('[mark="highlight-source"]').fill(synth(chars) + f"\nCycle {cycle}\n")
            page.evaluate("window.__ccWsBytes = 0")
            samples = page.evaluate("""async () => {
              const ta = document.querySelector('[mark="highlight-source"] textarea');
              const samples = [];
              for (let i = 0; i < 20; i++) {
                const start = performance.now();
                ta.value += 'x';
                ta.dispatchEvent(new Event('input', {bubbles: true}));
                await new Promise(requestAnimationFrame);
                samples.push(performance.now() - start);
              }
              return samples;
            }""")
            typing.extend(samples)
            typing_bytes = page.evaluate("window.__ccWsBytes")
            assert typing_bytes < max(100000, chars * 2), \
                "typing sent repeated whole-chart websocket updates"
            started = time.perf_counter()
            with HealthProbe() as probe:
                page.locator('[mark="run-clean"]').click()
                progress = page.locator('[mark="clean-progress"]')
                try:
                    progress.wait_for(state="visible", timeout=1000)
                    progress_seen.append(True)
                except Exception:
                    progress_seen.append(False)
                progress.wait_for(state="hidden", timeout=120000)
                page.wait_for_function(
                    "!document.querySelector('[mark=\"run-clean\"]').disabled", timeout=120000)
            health.extend(probe.samples)  # include the final request after the thread joins
            clean_ms.append((time.perf_counter() - started) * 1000)
            reconnects += page.locator('text=/reconnecting/i:visible').count()
        browser.close()
    return summarize(health, typing, clean_ms, progress_seen, reconnects, cycles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--chars", type=int, nargs="+", default=[100000, 250000, 500000])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    sizes = []
    for chars in args.chars:
        result = measure(args.cycles, chars)
        result["characters"] = chars
        sizes.append(result)
    report = {"cycles": args.cycles, "sizes": sizes,
              "passed": all(result["passed"] for result in sizes)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

`expected_cycles` keeps diagnostic runs meaningful instead of hard-coding ten. The CLI runs all three required sizes by default and records independent measurements and pass/fail status for each.

- [ ] **Step 4: Run unit tests and schedule the real-browser job**

Run: `.venv/bin/python -m pytest tests/test_clean_stability_benchmark.py -q`
Expected: 2 PASS.

Add a manual/scheduled step to the existing performance job after the validation benchmark:

```yaml
      - name: Install browser for Clean stability benchmark
        run: python -m playwright install --with-deps chromium
      - name: Start Chart Cleaner
        run: python app.py --port 8765 --no-browser &
      - name: Clean-page stability benchmark
        run: >
          python scripts/benchmark_clean_stability.py
          --cycles 10 --chars 100000 250000 500000
          --report artifacts/clean-stability.json
```

Include `artifacts/clean-stability.json` in the existing artifact upload path (the current `artifacts/` directory already covers it).

- [ ] **Step 5: Run the browser benchmark against the source app**

Run in terminal 1: `.venv/bin/python app.py --port 8765 --no-browser`
Run in terminal 2: `.venv/bin/python scripts/benchmark_clean_stability.py --cycles 10 --chars 100000 250000 500000 --report artifacts/clean-stability.json`
Expected: top-level JSON with `cycles: 10` and three entries under `sizes`, for 100,000, 250,000, and 500,000 characters. Every size must report `reconnects: 0`, `progress_within_1s: true`, health p95 ≤ 100 ms, health max ≤ 1000 ms, typing p95 ≤ 100 ms, and worst clean ≤ 30000 ms on the reference Mac. Stop terminal 1 after the run.

- [ ] **Step 6: Commit**

```bash
git add scripts/benchmark_clean_stability.py tests/test_clean_stability_benchmark.py .github/workflows/accuracy.yml
git commit -m "test: measure large chart connection stability"
```

---

### Task 8: Packaged-worker proof and full regression gate

**Files:**
- Modify: `app.py`
- Modify: `scripts/release_smoke.py:32-43,76-166`
- Modify: `tests/test_release_tools.py`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: production `ProcessCleanWorker`.
- Produces: `app.py --clean-worker-self-test`, printing only `{"ok": true, "stages": N}`; release report key `frozen_clean_worker: PASS`.

- [ ] **Step 1: Add the failing release-tool assertion**

Add to the release report assertions in `tests/test_release_tools.py`:

```python
def test_release_report_requires_frozen_clean_worker_proof():
    from pathlib import Path
    source = Path("scripts/release_smoke.py").read_text(encoding="utf-8")
    assert "--clean-worker-self-test" in source
    assert "'frozen_clean_worker': 'PASS'" in source
```

- [ ] **Step 2: Run the release-tool test and confirm the proof is absent**

Run: `.venv/bin/python -m pytest tests/test_release_tools.py::test_release_report_requires_frozen_clean_worker_proof -q`
Expected: FAIL because the self-test flag and report key are absent.

- [ ] **Step 3: Add the packaged self-test**

In `app.py`, add:

```python
def _clean_worker_self_test() -> int:
    async def check():
        from chartcleaner.clean_worker import ProcessCleanWorker, WorkRequest
        from chartcleaner.engine import load_default_config

        worker = ProcessCleanWorker()
        try:
            cfg = load_default_config()
            request = WorkRequest("packaged-self-test", "compute", {
                "text": "Patient Name: John Doe\nPlan: recheck K 4.1 tomorrow\n",
                "mode": "clean", "cfg": cfg,
                "custom_dir": str(common.CUSTOM_DIR), "lazy": True,
            })
            got = await worker.run(request)
            assert "John Doe" not in got.result.text
            assert "K 4.1" in got.result.text
            return len(got.result.stages)
        finally:
            await worker.close()

    stages = asyncio.run(check())
    print(json.dumps({"ok": True, "stages": stages}))
    return 0
```

Add `import json` to `app.py`. Add the hidden argument:

```python
    ap.add_argument("--clean-worker-self-test", action="store_true", help=argparse.SUPPRESS)
```

Immediately after `args = ap.parse_args()`:

```python
    if args.clean_worker_self_test:
        raise SystemExit(_clean_worker_self_test())
```

- [ ] **Step 4: Exercise the self-test from release smoke**

Add to `scripts/release_smoke.py`:

```python
def check_clean_worker(install: Path, env: dict):
    completed = subprocess.run(
        [str(updater._executable(install)), '--clean-worker-self-test'],
        env=env, check=True, timeout=180, capture_output=True, text=True)
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result['ok'] is True and result['stages'] > 0
```

Call `check_clean_worker(install, env)` immediately after both `wait_ui(VERSION)` calls and before the corresponding `stop_installed(install)`. Add to the report:

```python
                    'frozen_clean_worker': 'PASS',
```

- [ ] **Step 5: Run source self-test and release unit tests**

Run: `.venv/bin/python app.py --clean-worker-self-test`
Expected: one JSON line with `"ok": true` and a positive stage count; no server starts and no worker remains.

Run: `.venv/bin/python -m pytest tests/test_release_tools.py tests/test_clean_worker.py -q`
Expected: all PASS.

- [ ] **Step 6: Record the new architecture invariant**

Append this concise workspace fact to `AGENTS.md`:

```markdown
- **Large-chart stability (2026-11):** the Clean page sends versioned whole-snapshot editor updates through `chartcleaner/static/clean/editor.js` (300 ms debounce) and runs pipeline/audit/Review/Insights work through the app-wide manager in `chartcleaner/clean_worker.py`. Production uses one spawned process; `NICEGUI_USER_SIMULATION` uses the inline worker to preserve test seams. `chartcleaner/clean_compute.py` is pure and must not import NiceGUI or persistence. Clinical output, validation, provenance, and `review_state.release_decision` must match in-process results; worker transport failures retry once and never fall back to a large UI-thread clean. Scheduled evidence: `scripts/benchmark_clean_stability.py`; frozen evidence: `app.py --clean-worker-self-test`.
```

- [ ] **Step 7: Run the complete regression and packaging gates**

Run: `.venv/bin/python -m pytest -q`
Expected: the full suite PASS; no test modifies tracked files or real `data/`.

Run: `git diff --check && git status --short`
Expected: no whitespace errors; only intentional implementation files and generated artifacts outside git are present.

Run on each native release runner: `python scripts/release_smoke.py` with its existing required arguments.
Expected: the report includes `frozen_installer_ui: PASS`, `frozen_clean_worker: PASS`, signed sentinel preservation, and broken-startup rollback. Do not substitute source tests for these native checks.

- [ ] **Step 8: Commit**

```bash
git add app.py scripts/release_smoke.py tests/test_release_tools.py AGENTS.md
git commit -m "test: prove packaged cleaning worker startup"
```

## Final Verification Checklist

- [ ] In-process and worker golden-chart outputs, validation reports, provenance summaries, and release decisions match.
- [ ] Ten cycles at each of 100 KB, 250 KB, and 500 KB produce no visible reconnect banner or page rebuild.
- [ ] Typing sends no whole-chart update per keystroke; a snapshot flushes after 300 ms and before actions.
- [ ] At every benchmark size, main-process health p95 ≤ 100 ms and worst response ≤ 1 second during cleaning.
- [ ] At every benchmark size, browser typing p95 ≤ 100 ms and progress appears within 1 second.
- [ ] Warm cleaning at every benchmark size meets the 30-second reference-Mac target; longer jobs still remain connected and show progress.
- [ ] Worker crash retry, repeated failure, cancellation, reconnect, lazy panels, and shutdown all pass.
- [ ] macOS and Windows packaged self-tests load NLP and leave no orphan worker.
- [ ] Full pytest suite and `git diff --check` pass.
