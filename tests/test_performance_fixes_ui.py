"""Slow review and persistence work must leave the event loop usable."""
from __future__ import annotations

import asyncio
import threading

import pytest
from nicegui.testing import User

import app as cc_app
from app_pages import batch as batch_mod, clean as clean_mod, common, review_workspace
from chartcleaner import store
from chartcleaner.appstate import CLEAN_STATE
from chartcleaner.engine import Pipeline
from chartcleaner.review_state import release_decision
from tests.test_phase8_ui import _route, page  # noqa: F401
from tests.test_validation_review_ui import _clean, _write_cfg

pytestmark = pytest.mark.nicegui_main_file("")


async def _until(predicate):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(.01)
    assert predicate()


async def test_edit_validation_runs_off_loop_and_copy_waits(user: User, page, monkeypatch):
    _write_cfg()
    await _clean(user, "/perf-edit-copy", "Possible pneumonia.\n")
    previous = CLEAN_STATE["result"]
    entered, resume = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    real_audit = clean_mod.run_audit
    copied = []

    def audit(*args):
        assert threading.get_ident() != main_thread
        entered.set()
        assert resume.wait(5)
        return real_audit(*args)

    monkeypatch.setattr(clean_mod, "run_audit", audit)
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda *args: copied.append(args))
    user.find(marker="edit-result").click()
    out = next(iter(user.find(marker="result-output").elements))
    out.set_value("Pneumonia.\n")
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    try:
        await _until(entered.is_set)
        assert CLEAN_STATE["result"] is previous
        assert not copied
    finally:
        resume.set()
    await user.should_see(marker="copy-guard")
    assert "edited_output_issues" in release_decision(
        CLEAN_STATE["result"], output_text=CLEAN_STATE["result_text"], source="Possible pneumonia.\n").reasons
    assert not copied


async def test_edit_worker_discards_a_stale_snapshot(user: User, page, monkeypatch):
    _write_cfg()
    await _clean(user, "/perf-edit-stale", "Possible pneumonia.\n")
    previous = CLEAN_STATE["result"]
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
    real_audit = clean_mod.run_audit

    def audit(*args):
        entered.set()
        assert resume.wait(5)
        finished.set()
        return real_audit(*args)

    monkeypatch.setattr(clean_mod, "run_audit", audit)
    user.find(marker="edit-result").click()
    out = next(iter(user.find(marker="result-output").elements))
    out.set_value("Pneumonia.\n")
    user.find(marker="edit-result").click()
    try:
        await _until(entered.is_set)
        out.set_value("Possible pneumonia. Recheck tomorrow.\n")
    finally:
        resume.set()
    await _until(finished.is_set)
    await asyncio.sleep(.1)
    assert CLEAN_STATE["result"] is previous
    assert out.value == "Possible pneumonia. Recheck tomorrow.\n"


async def test_delayed_edit_logging_never_overwrites_the_next_edit(user: User, page, monkeypatch):
    _write_cfg()
    await _clean(user, "/perf-edit-log", "Possible pneumonia.\n")
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()

    def record(*args):
        entered.set()
        assert resume.wait(5)
        finished.set()
        return False

    monkeypatch.setattr(clean_mod.edit_log, "record", record)
    user.find(marker="edit-result").click()
    next(iter(user.find(marker="result-output").elements)).set_value("Pneumonia.\n")
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    try:
        await _until(entered.is_set)
        # Publication is complete before logging; a fresh editor can accept the next edit.
        out = next(iter(user.find(marker="result-output").elements))
        user.find(marker="edit-result").click()
        out.set_value("Possible pneumonia. Recheck tomorrow.\n")
    finally:
        resume.set()
    await _until(finished.is_set)
    await _until(lambda: CLEAN_STATE["result_text"] == "Possible pneumonia. Recheck tomorrow.\n")
    assert CLEAN_STATE["result_text"] == "Possible pneumonia. Recheck tomorrow.\n"


async def test_optional_computation_is_lazy_and_off_loop(user: User, page, monkeypatch):
    monkeypatch.setattr(clean_mod, "LAZY_TABS_MIN_CHARS", 0)
    calls = []
    main_thread = threading.get_ident()
    diff, trends = common.review_diff_html, clean_mod.build_trends

    def build_diff(*args):
        calls.append(("diff", threading.get_ident()))
        return diff(*args)

    def build_trends(*args):
        calls.append(("trends", threading.get_ident()))
        return trends(*args)

    monkeypatch.setattr(common, "review_diff_html", build_diff)
    monkeypatch.setattr(clean_mod, "review_diff_html", build_diff)
    monkeypatch.setattr(clean_mod, "build_trends", build_trends)
    await _clean(user, "/perf-lazy-work", "Plan: recheck K 4.1 tomorrow.\n")
    assert calls == []
    user.find(marker="review-tab").click()
    await user.should_see(marker="review-findings-tab")
    user.find("Insights").click()
    await _until(lambda: CLEAN_STATE.get("insights_ready"))
    assert {name for name, _ in calls} == {"diff", "trends"}
    assert all(thread != main_thread for _, thread in calls)


async def test_shortening_input_cancels_speculation_immediately(user: User, page, monkeypatch):
    speculative = {"task": object(), "inputs": ("old chart",)}
    monkeypatch.setattr(clean_mod, "SPECULATION", speculative)
    CLEAN_STATE.update(input="old chart", result=None, result_text="")
    await _route(user, "/perf-short-input", cc_app.clean_page)
    next(iter(user.find(marker="highlight-source").elements)).set_value("")
    assert speculative == {"task": None, "inputs": None}


async def test_redaction_chips_are_paged(user: User, page):
    from tests.test_validation_shadow import base
    text = "[REDACTED_NAME]\n" * (review_workspace.ROW_PAGE + 5)
    result = Pipeline(base()).run(text)
    CLEAN_STATE.update(input=text, result=result, result_text=result.text, mode="clean", result_mode="clean")
    await _route(user, "/perf-redaction-pages", cc_app.clean_page)
    assert len(user.find(marker="redaction-chip").elements) == review_workspace.ROW_PAGE
    user.find(marker="redactions-more").click()
    assert len(user.find(marker="redaction-chip").elements) == review_workspace.ROW_PAGE + 5


async def test_batch_persists_files_and_archive_off_loop(user: User, page, monkeypatch, tmp_path):
    main_thread = threading.get_ident()
    threads = []
    real_zip = batch_mod.zipfile.ZipFile

    def archive(*args, **kwargs):
        threads.append(threading.get_ident())
        return real_zip(*args, **kwargs)

    monkeypatch.setattr(batch_mod.zipfile, "ZipFile", archive)
    monkeypatch.setattr(store, "append_run", lambda record: threads.append(threading.get_ident()))
    monkeypatch.setattr(store, "EXPORTS_DIR", tmp_path / "exports")
    await _route(user, "/perf-batch-persist", cc_app.batch_page)
    next(iter(user.find(marker="patient-list-input").elements)).set_value(
        "G20-1 65M SAH, EVD day 4.\nG20-2 72F ICH, SBP goal <140.\n")
    user.find(marker="patient-list-run").click()
    await user.should_see("Processed 2 item(s)", retries=100)
    assert len(threads) >= 3 and all(thread != main_thread for thread in threads)
    assert len(list((tmp_path / "exports").rglob("*_cleaned.txt"))) == 2
    assert len(list((tmp_path / "exports").glob("*.zip"))) == 1
