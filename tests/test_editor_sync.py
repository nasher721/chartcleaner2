from __future__ import annotations

import asyncio

import pytest
from nicegui.testing import User

import app as cc_app
from app_pages import clean as clean_mod
from chartcleaner import clean_worker
from chartcleaner.appstate import CLEAN_STATE
from chartcleaner.editor_sync import EditorRevisions
from tests.test_phase8_ui import _route, page  # noqa: F401

pytestmark = pytest.mark.nicegui_main_file("")


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


async def _open(user: User, route: str, text: str = "Plan: original\n") -> None:
    CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, route, cc_app.clean_page)


def _textarea(user: User):
    return next(iter(user.find(marker="highlight-source").elements))


async def test_clean_controls_expose_browser_dom_markers(user: User, page):
    await _open(user, "/editor-dom-markers")
    elements = [descendant for root in user.find().elements for descendant in root.descendants()]
    for marker in ("highlight-source", "run-clean", "clean-progress", "chart-counts"):
        element = next(element for element in elements if marker in element._markers)
        assert element.props.get("data-cc-marker") == marker


async def test_editor_has_no_unrevisioned_value_listener(user: User, page):
    await _open(user, "/editor-listeners")
    textarea = _textarea(user)
    listener_types = {listener.type for listener in textarea._event_listeners.values()}
    assert not {"update:value", "update:modelValue", "update:model-value"} & listener_types
    assert {"input", "mouseup", "keyup", "touchend"} <= listener_types

    user.find(marker="highlight-source").trigger("update:value", "unrevisioned browser echo")
    user.find(marker="highlight-source").trigger("update:modelValue", "unrevisioned browser echo")
    user.find(marker="highlight-source").trigger("update:model-value", "unrevisioned browser echo")
    assert CLEAN_STATE["input"] == "Plan: original\n"
    assert textarea.value == "Plan: original\n"


async def test_accepted_editor_snapshots_never_update_the_element(user: User, page):
    await _open(user, "/editor-no-echo")
    textarea = _textarea(user)
    updates = []
    textarea.update = lambda: updates.append(1)

    user.find(marker="highlight-source").trigger(
        "input", {"value": "Plan: revision one\n", "revision": 1})
    assert CLEAN_STATE["input"] == "Plan: revision one\n"
    assert textarea.value == "Plan: revision one\n"
    assert updates == []

    user.find(marker="highlight-source").trigger(
        "input", {"value": "Plan: revision two\n", "revision": 2})
    user.find(marker="highlight-source").trigger(
        "input", {"value": "Plan: stale revision\n", "revision": 1})
    assert CLEAN_STATE["input"] == "Plan: revision two\n"
    assert textarea.value == "Plan: revision two\n"
    assert textarea.props[textarea.VALUE_PROP] == "Plan: revision two\n"
    assert updates == []


async def test_production_clean_flushes_editor_before_worker_computation(
        user: User, page, monkeypatch):
    monkeypatch.delenv("NICEGUI_USER_SIMULATION")
    await _open(user, "/editor-action-flush", "Plan: stale server value\n")
    chart = "Plan: latest browser value\n"
    order, acknowledgements = [], []

    def run_javascript(script, *args, **kwargs):
        if "window.ccCleanEditor.snapshot()" in script:
            order.append("snapshot")

            async def snapshot():
                return {"value": chart, "revision": 1}

            return snapshot()
        if "window.ccCleanEditor.ack(" in script:
            acknowledgements.append(script)
        return None

    monkeypatch.setattr(user.client, "run_javascript", run_javascript)
    inline_worker = clean_worker.InlineCleanWorker()
    monkeypatch.setattr(clean_mod, "get_clean_worker", lambda: inline_worker)
    real_run = clean_worker.InlineCleanWorker.run

    async def observed_run(self, request, on_progress=None):
        order.append("compute")
        assert request.payload["text"] == chart
        return await real_run(self, request, on_progress)

    monkeypatch.setattr(clean_worker.InlineCleanWorker, "run", observed_run)
    errors = []
    monkeypatch.setattr(clean_mod, "report_error", lambda *args: errors.append(args))
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None or errors:
            break
        await asyncio.sleep(0.02)
    assert errors == []
    assert CLEAN_STATE.get("result") is not None
    assert order[:2] == ["snapshot", "compute"]
    assert len(acknowledgements) == 1
    assert "ack(1)" in acknowledgements[0]
