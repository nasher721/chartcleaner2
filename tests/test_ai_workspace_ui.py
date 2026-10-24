import asyncio
import threading

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user import UserInteraction
import app as cc_app
from app_pages import clean as clean_mod
from tests.test_ai_ui_lifecycle import _answer, _open_ai_panel, _restore_state

pytestmark = pytest.mark.nicegui_main_file("")


async def test_ai_drawer_uses_summary_and_question_tabs(user: User, monkeypatch, tmp_path):
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        user.find(marker="ai-assistant-button").click()
        await user.should_see(marker="ai-summary-tab")
        await user.should_see(marker="ai-ask-tab")
        await user.should_see("Summarize")
        user.find(marker="ai-ask-tab").click()
        await user.should_see("Ask about this chart")
        user.find(marker="ai-summary-tab").click()
        await user.should_not_see("Custom prompt (replaces the preset)")
        user.find(marker="ai-prompt-settings").click()
        await user.should_see("Custom prompt (replaces the preset)")
    finally:
        _restore_state(before, auto_before)


async def test_ai_drawer_close_restores_invoker_focus(user: User, monkeypatch, tmp_path):
    scripts = []
    monkeypatch.setattr(clean_mod.ui, "run_javascript",
                        lambda script, *args, **kwargs: scripts.append(script))
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        user.find(marker="ai-assistant-button").click()
        user.find(marker="ai-close").click()
        assert any("ai-assistant-button" in script and ".focus(" in script
                   for script in scripts)
    finally:
        _restore_state(before, auto_before)


async def test_ai_answer_stream_survives_switching_tabs(user: User, monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    def delayed_answer(question, chart, cfg, history=None, on_token=None, **_kw):
        on_token("PARTIAL ANSWER")
        started.set()
        release.wait(timeout=3)
        return _answer(question, "COMPLETE ANSWER")

    monkeypatch.setattr(clean_mod, "ask_chart", delayed_answer)
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        user.find(marker="ai-assistant-button").click()
        user.find(marker="ai-ask-tab").click()
        user.find(ui.input).type("What happened?")
        ask = next(e for e in user.find(ui.button).elements if e.props.get("label") == "Ask")
        UserInteraction(user, {ask}, "Ask").click()
        assert await asyncio.to_thread(started.wait, 2)
        await user.should_see("PARTIAL ANSWER", retries=40)
        user.find(marker="ai-summary-tab").click()
        await asyncio.sleep(0.3)
        user.find(marker="ai-ask-tab").click()
        stream = next(iter(user.find(marker="ai-answer-stream").elements))
        assert stream.text == "PARTIAL ANSWER"
        assert stream.visible
    finally:
        release.set()
        await asyncio.sleep(0.05)
        _restore_state(before, auto_before)
