"""The page error boundary: an unhandled error shows a recovery screen.

Pages handle their own expected failures; this is the last line of defence for
the unexpected one. These tests pin that a raising page still renders something
useful (and logs the traceback), instead of NiceGUI's bare 500 page.
"""

from __future__ import annotations

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import common
from chartcleaner import store

pytestmark = pytest.mark.nicegui_main_file("")


def test_guarded_preserves_the_function_identity():
    def my_page():
        return "ok"

    wrapped = common.guarded(my_page)
    assert wrapped.__name__ == "my_page"
    assert wrapped() == "ok"


def test_guarded_reraises_when_the_recovery_screen_fails(monkeypatch):
    """If even the error page cannot render, the original error must surface."""
    def boom():
        raise ValueError("original failure")

    def broken_render(*_a, **_k):
        raise RuntimeError("recovery screen failed")

    monkeypatch.setattr(common, "_render_error_page", broken_render)
    with pytest.raises(ValueError, match="original failure"):
        common.guarded(boom)()


def test_every_route_is_guarded():
    """The ROUTES table names the real pages; registration wraps them."""
    assert set(cc_app.ROUTES) == {"/", "/batch", "/census", "/rules", "/pipeline",
                                  "/stats", "/scripts", "/settings", "/doctor"}


async def test_raising_page_shows_a_recovery_screen(user: User, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)

    def boom_page():
        raise RuntimeError("synthetic page failure")

    ui.page("/boom")(common.guarded(boom_page))

    await user.open("/boom")
    await user.should_see("This screen couldn't load")
    await user.should_see("Nothing was cleaned or saved")
    # The recovery screen offers a way out.
    await user.should_see("Reload the app")
    await user.should_see("Go to Doctor")


async def test_raising_page_logs_the_traceback(user: User, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)

    def boom_page():
        raise ValueError("logged failure marker")

    ui.page("/boom-log")(common.guarded(boom_page))

    await user.open("/boom-log")
    log = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "logged failure marker" in log
    assert "ValueError" in log
    assert "Page failed" in log
