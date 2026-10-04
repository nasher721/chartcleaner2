import asyncio
import threading

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user import UserInteraction

import app as cc_app
from app_pages import common
from app_pages import clean as clean_mod
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
from chartcleaner.engine import Pipeline, load_default_config, save_config
from chartcleaner.local_llm import GroundingResult
from chartcleaner.summarizer import SummaryResult
from chartcleaner.chart_qa import QaResult


CHART = "Patient is stable.\nPotassium 4.2.\n"

pytestmark = pytest.mark.nicegui_main_file("")


def _result(text: str):
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    return Pipeline(cfg).run(text)


def _summary(text: str) -> SummaryResult:
    return SummaryResult(
        text=text,
        grounding=GroundingResult(100.0, True, [], 0),
        model="fake",
        preset="clinical",
        duration_ms=1,
    )


def _answer(question: str, text: str) -> QaResult:
    return QaResult(
        question=question,
        answer=text,
        grounding=GroundingResult(100.0, True, [], 0),
        model="fake",
        duration_ms=1,
    )


async def _open_ai_panel(user: User, monkeypatch, tmp_path):
    result = _result(CHART)
    before = dict(CLEAN_STATE)
    auto_before = dict(AUTO_LAST)
    CLEAN_STATE.update(input=CHART, result=result, result_text=result.text,
                       audit=None, summary=None, qa=[], mode="clean", result_mode="clean")
    monkeypatch.setattr(cc_app.LocalLlmClient, "is_available", lambda self: False)
    monkeypatch.setattr(cc_app.LocalLlmClient, "list_models", lambda self: [])
    monkeypatch.setitem(common.PREFS, "auto_clean", False)
    config_path = tmp_path / "config.json"
    save_config(load_default_config(), config_path)
    monkeypatch.setattr(common, "CONFIG_PATH", config_path)
    @ui.page("/ai-lifecycle")
    async def ai_lifecycle_fixture():
        await cc_app.clean_page()

    await user.open("/ai-lifecycle")
    return before, auto_before, result


def _restore_state(before, auto_before):
    CLEAN_STATE.clear()
    CLEAN_STATE.update(before)
    AUTO_LAST.clear()
    AUTO_LAST.update(auto_before)


def _change_chart(user: User, change: str) -> None:
    if change == "mode":
        next(iter(user.find(ui.toggle).elements)).set_value("abbreviations")
    elif change == "clear":
        clear_button = next(e for e in user.find(ui.button).elements
                            if e.props.get("label") == "Clear")
        UserInteraction(user, {clear_button}, "Clear").click()
    elif change == "restore":
        restored = "Restored chart text."
        CLEAN_STATE["result"].text = restored
        CLEAN_STATE["result_text"] = restored
    else:
        new_result = _result(CHART)
        CLEAN_STATE.update(result=new_result, result_text=new_result.text, summary=None, qa=[])


@pytest.mark.parametrize("change", ["mode", "clear", "reclean", "restore"])
async def test_stale_summary_is_discarded_after_chart_change(user: User, monkeypatch, tmp_path, change):
    started = threading.Event()
    release = threading.Event()

    def delayed_summary(chart, cfg, **_kw):
        started.set()
        release.wait(timeout=2)
        return _summary("STALE SUMMARY")

    monkeypatch.setattr(clean_mod, "summarize", delayed_summary)
    worker_tasks = []
    real_io_bound = cc_app.run.io_bound

    async def tracked_io_bound(fn, *args, **kwargs):
        worker_tasks.append(asyncio.current_task())
        return await real_io_bound(fn, *args, **kwargs)

    monkeypatch.setattr(cc_app.run, "io_bound", tracked_io_bound)
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        user.find("Summarize").click()
        assert await asyncio.to_thread(started.wait, 2)
        _change_chart(user, change)
        release.set()
        await asyncio.wait_for(asyncio.shield(worker_tasks[-1]), 3)
        await user.should_not_see("STALE SUMMARY", retries=20)
        assert CLEAN_STATE.get("summary") is None
    finally:
        release.set()
        _restore_state(before, auto_before)


@pytest.mark.parametrize("change", ["mode", "clear", "reclean", "restore"])
async def test_stale_answer_is_discarded_after_chart_change(user: User, monkeypatch, tmp_path, change):
    started = threading.Event()
    release = threading.Event()

    def delayed_answer(question, chart, cfg, history=None, **_kw):
        started.set()
        release.wait(timeout=2)
        return _answer(question, "STALE ANSWER")

    monkeypatch.setattr(clean_mod, "ask_chart", delayed_answer)
    worker_tasks = []
    real_io_bound = cc_app.run.io_bound

    async def tracked_io_bound(fn, *args, **kwargs):
        worker_tasks.append(asyncio.current_task())
        return await real_io_bound(fn, *args, **kwargs)

    monkeypatch.setattr(cc_app.run, "io_bound", tracked_io_bound)
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        user.find(ui.input).type("What happened?")
        ask_button = next(e for e in user.find(ui.button).elements if e.props.get("label") == "Ask")
        UserInteraction(user, {ask_button}, "Ask").click()
        assert await asyncio.to_thread(started.wait, 2)
        _change_chart(user, change)
        release.set()
        await asyncio.wait_for(asyncio.shield(worker_tasks[-1]), 3)
        await user.should_not_see("STALE ANSWER", retries=20)
        assert CLEAN_STATE.get("qa") == []
    finally:
        release.set()
        _restore_state(before, auto_before)


@pytest.mark.parametrize("kind", ["summary", "answer"])
async def test_current_chart_ai_response_is_published(user: User, monkeypatch, tmp_path, kind):
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        if kind == "summary":
            monkeypatch.setattr(clean_mod, "summarize", lambda chart, cfg, **_kw: _summary("CURRENT SUMMARY"))
            user.find("Summarize").click()
            await user.should_see("CURRENT SUMMARY", retries=20)
            assert CLEAN_STATE["summary"].text == "CURRENT SUMMARY"
        else:
            monkeypatch.setattr(clean_mod, "ask_chart",
                                lambda question, chart, cfg, history=None, **_kw: _answer(question, "CURRENT ANSWER"))
            user.find(ui.input).type("What happened?")
            ask_button = next(e for e in user.find(ui.button).elements if e.props.get("label") == "Ask")
            UserInteraction(user, {ask_button}, "Ask").click()
            await user.should_see("CURRENT ANSWER", retries=20)
            assert CLEAN_STATE["qa"][0]["a"] == "CURRENT ANSWER"
    finally:
        _restore_state(before, auto_before)
