"""End-to-end UI smoke test for the Clean page.

Uses NiceGUI's in-process ``User`` fixture (no real browser / server needed),
which exercises the actual event handlers: paste a chart into the source
textarea, click Run clean, and confirm the result output is produced and the
planted PHI is gone.

The route under test is the real ``/`` (Clean) page registered in ``app.py``
(``main_file = app.py`` in pytest.ini), so this reuses the exact handlers the
shipped app uses.

Real-browser Playwright coverage for CI lives in ``e2e/run_clean_e2e.py``
(Page-Object-Model sync runner that drives a live app); this in-process test is
the runnable equivalent in this environment.
"""
from __future__ import annotations

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import common
from chartcleaner import store
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
from chartcleaner.engine import load_default_config, save_config


@pytest.fixture()
def clean_page(monkeypatch, tmp_path):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}  # deterministic + fast for the smoke test
    cfg["audit"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "append_run", lambda _r: None)
    monkeypatch.setitem(common.PREFS, "auto_clean", False)
    monkeypatch.setitem(common.PREFS, "onboarded", True)
    monkeypatch.setattr(cc_app.LocalLlmClient, "is_available", lambda self: False)
    monkeypatch.setattr(cc_app.LocalLlmClient, "list_models", lambda self: [])
    # Start from a clean source so the typed sample is the only input.
    CLEAN_STATE["input"] = ""
    CLEAN_STATE["result"] = None
    CLEAN_STATE["result_text"] = ""
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    yield
    CLEAN_STATE.clear()
    CLEAN_STATE.update(before)
    AUTO_LAST.clear()
    AUTO_LAST.update(auto_before)


async def test_clean_flow_produces_output_and_redacts(user: User, clean_page) -> None:
    sample = (
        "Patient Name: John Doe\n"
        "MRN: 1234567\n"
        "DOB: 03/04/1975\n"
        "Plan: continue aspirin 81 mg daily.\n"
    )
    await user.open("/")
    # Simulate the user pasting the chart into the source textarea.
    user.find(marker="highlight-source").type(sample)
    # Click "Clean" and let the (async) clean run to completion.
    user.find(marker="run-clean").click()

    # Wait for the clean to finish and the result to render.
    for _ in range(200):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)

    await user.should_see(marker="result-output")
    # The planted PHI must not survive the clean.
    assert "John Doe" not in CLEAN_STATE.get("result_text", "")
    assert "1234567" not in CLEAN_STATE.get("result_text", "")
    assert CLEAN_STATE.get("result") is not None
