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

    task = asyncio.create_task(run_job())
    try:
        for _ in range(300):
            if seen:
                break
            await asyncio.sleep(0.02)
        assert seen, "worker never reached a stage boundary"
        worker.cancel(request.job_id)
        await task
    finally:
        if not task.done():
            worker.cancel(request.job_id)
        try:
            await task
        except BaseException:
            pass
        await worker.close()


@pytest.mark.parametrize("case", [
    "progress_missing_event",
    "progress_bad_event",
    "progress_bad_job_id",
    "result_missing_payload",
    "error_missing_error",
    "error_bad_error",
])
async def test_process_worker_malformed_message_fields_retry(monkeypatch, case):
    worker = ProcessCleanWorker()
    request = _request()
    if case == "progress_missing_event":
        frame = {"type": "progress"}
    elif case == "progress_bad_event":
        frame = {"type": "progress", "event": object()}
    elif case == "progress_bad_job_id":
        frame = {"type": "progress", "event": WorkProgress(123, "stage", "Stage", 0, 1, 0.0)}
    elif case == "result_missing_payload":
        frame = {"type": "result", "job_id": request.job_id}
    elif case == "error_missing_error":
        frame = {"type": "error", "job_id": request.job_id}
    else:
        frame = {"type": "error", "job_id": request.job_id, "error": 123}
    reads = 0
    starts = 0
    original_read = worker._read
    original_start = worker._start

    async def malformed_once():
        nonlocal reads
        reads += 1
        if reads == 2:
            return frame
        return await original_read()

    async def counted_start():
        nonlocal starts
        starts += 1
        await original_start()

    monkeypatch.setattr(worker, "_read", malformed_once)
    monkeypatch.setattr(worker, "_start", counted_start)
    try:
        got = await worker.run(request)
        assert got.result.text
        assert starts == 2
    finally:
        await worker.close()


async def test_process_worker_persistent_transport_failure_has_exact_two_attempts(monkeypatch):
    worker = ProcessCleanWorker()
    reads = 0
    starts = 0
    original_read = worker._read
    original_start = worker._start

    async def fail_work_reads():
        nonlocal reads
        reads += 1
        if reads % 2 == 0:
            raise EOFError("persistent closed pipe")
        return await original_read()

    async def counted_start():
        nonlocal starts
        starts += 1
        await original_start()

    monkeypatch.setattr(worker, "_read", fail_work_reads)
    monkeypatch.setattr(worker, "_start", counted_start)
    try:
        with pytest.raises(WorkerError, match="Cleaning worker failed twice"):
            await worker.run(_request())
        assert starts == 2
        assert reads == 4
    finally:
        await worker.close()


async def test_restart_escalates_to_kill_after_join_timeout():
    class FakeProcess:
        pid = 123
        alive = True
        terminates = 0
        kills = 0
        joins = 0

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminates += 1

        def kill(self):
            self.kills += 1
            self.alive = False

        def join(self, timeout):
            self.joins += 1

    worker = ProcessCleanWorker()
    process = FakeProcess()
    worker._process = process
    await worker._restart()
    assert process.terminates == 1
    assert process.kills == 1
    assert process.joins == 2
    assert worker._process is None


async def test_restart_retains_process_when_exit_cannot_be_confirmed():
    from chartcleaner.clean_worker import WorkerTransportError

    class FakeProcess:
        pid = 123
        joins = 0

        def is_alive(self):
            return True

        def terminate(self):
            pass

        def kill(self):
            pass

        def join(self, timeout):
            self.joins += 1

    worker = ProcessCleanWorker()
    process = FakeProcess()
    worker._process = process
    with pytest.raises(WorkerTransportError, match="did not exit"):
        await worker._restart()
    assert process.joins == 2
    assert worker._process is process


async def test_close_terminates_unresponsive_active_process_without_replacement(monkeypatch):
    worker = ProcessCleanWorker()
    request = _request(text="Plan: recheck K 4.1\n" * 20000)
    work_reading = asyncio.Event()
    release_read = asyncio.Event()
    reads = 0
    starts = 0
    original_read = worker._read
    original_start = worker._start

    async def controlled_read():
        nonlocal reads
        reads += 1
        if reads == 1:
            return await original_read()
        work_reading.set()
        await release_read.wait()
        raise EOFError("terminated worker pipe")

    async def counted_start():
        nonlocal starts
        starts += 1
        await original_start()

    monkeypatch.setattr(worker, "_read", controlled_read)
    monkeypatch.setattr(worker, "_start", counted_start)
    task = None
    close_task = None
    try:
        task = asyncio.create_task(worker.run(request))
        await asyncio.wait_for(work_reading.wait(), 10)
        process = worker._process
        assert process is not None and process.is_alive()
        close_task = asyncio.create_task(worker.close())
        for _ in range(100):
            if not process.is_alive():
                break
            await asyncio.sleep(0.02)
        assert not process.is_alive()
        assert not close_task.done()
        release_read.set()
        await asyncio.wait_for(close_task, 10)
        with pytest.raises(WorkerError, match="Cleaning worker is closed"):
            await task
        assert starts == 1
        assert worker._process is None
    finally:
        release_read.set()
        if task is not None and not task.done():
            task.cancel()
        if close_task is not None:
            try:
                await asyncio.wait_for(close_task, 10)
            except BaseException:
                pass
        if task is not None:
            try:
                await task
            except BaseException:
                pass
        await worker.close()


@pytest.mark.parametrize("worker_type", [InlineCleanWorker, ProcessCleanWorker])
async def test_queued_cancellation_skips_only_the_stale_job(worker_type):
    worker = worker_type()
    active = _request(text="Plan: recheck K 4.1 tomorrow\n" * 20000)
    active.payload["lazy"] = True
    stale, current = _request(text="Plan: stale chart\n"), _request(text="Plan: current chart\n")
    progress = []
    active_task = asyncio.create_task(worker.run(active, progress.append))
    stale_task = current_task = None
    try:
        for _ in range(300):
            if progress:
                break
            await asyncio.sleep(0.02)
        assert progress, "active work never reached a stage boundary"
        stale_task = asyncio.create_task(worker.run(stale))
        await asyncio.sleep(0)  # let the stale request register behind the active job
        worker.cancel(stale.job_id)
        worker.cancel("unknown-job")
        current_task = asyncio.create_task(worker.run(current))

        assert "K 4.1" in (await asyncio.wait_for(active_task, 30)).result.text
        with pytest.raises(PipelineCancelled):
            await asyncio.wait_for(stale_task, 10)
        assert "current chart" in (await asyncio.wait_for(current_task, 30)).result.text
        assert worker._pending == set()
        assert worker._cancelled == set()
        assert worker._active is None
    finally:
        for request in (active, stale, current):
            worker.cancel(request.job_id)
        tasks = [task for task in (active_task, stale_task, current_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await worker.close()
