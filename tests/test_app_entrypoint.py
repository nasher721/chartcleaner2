import sys

import pytest

import app


def test_entrypoint_diverts_frozen_multiprocessing_before_main(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(app.multiprocessing, "freeze_support", lambda: calls.append("freeze_support"))
    monkeypatch.setattr(app, "main", lambda: calls.append("main"))

    app._entrypoint()

    assert calls == ["freeze_support", "main"]


@pytest.mark.parametrize("frozen", [False, True])
def test_main_preserves_app_arguments(monkeypatch, frozen):
    monkeypatch.delenv("NICEGUI_USER_SIMULATION", raising=False)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    argv = ["chart-cleaner", "--port", "8769", "--no-browser"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(app, "_port_busy", lambda _port: False)
    monkeypatch.setattr(app.app, "add_middleware", lambda *_a, **_kw: None)
    monkeypatch.setattr(app.updates, "SERVER_PORT", None)
    calls = []
    monkeypatch.setattr(app.ui, "run", lambda **kwargs: calls.append(kwargs))

    app.main()

    assert sys.argv == argv
    assert calls[0]["port"] == 8769
    assert calls[0]["show"] is False


@pytest.mark.parametrize("arguments", [["-c", "unexpected command"], ["-B"], ["-S"],
                                      ["-I"], ["--keep-parent"]])
def test_main_rejects_unhandled_frozen_arguments(monkeypatch, arguments):
    monkeypatch.delenv("NICEGUI_USER_SIMULATION", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["chart-cleaner", *arguments])
    monkeypatch.setattr(app, "_port_busy", lambda _port: False)
    monkeypatch.setattr(app.app, "add_middleware", lambda *_a, **_kw: None)
    monkeypatch.setattr(app.updates, "SERVER_PORT", None)
    calls = []
    monkeypatch.setattr(app.ui, "run", lambda **kwargs: calls.append(kwargs))

    with pytest.raises(SystemExit) as exc:
        app.main()

    assert exc.value.code == 2
    assert calls == []
