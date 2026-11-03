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
