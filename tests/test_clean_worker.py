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
