"""Local Clean-page work protocol and worker implementations."""
from __future__ import annotations

import asyncio
import multiprocessing as mp
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


class WorkerTransportError(RuntimeError):
    """Startup, handshake, protocol, pipe, EOF, or timeout failure; safe to retry."""


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
        await run.io_bound(self._conn.send, value)

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


def get_clean_worker():
    manager = WORKER_STATE.get("manager")
    if manager is None:
        manager = (InlineCleanWorker() if os.environ.get("NICEGUI_USER_SIMULATION")
                   else ProcessCleanWorker())
        WORKER_STATE["manager"] = manager
    return manager


async def shutdown_clean_worker() -> None:
    manager = WORKER_STATE.pop("manager", None)
    if manager is not None:
        await manager.close()
