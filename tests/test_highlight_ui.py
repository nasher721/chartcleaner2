"""Isolated interaction tests for Clean-page highlight learning."""

from __future__ import annotations

import pytest

import app as cc_app
from chartcleaner.engine import load_default_config, load_config, save_config
from chartcleaner.highlight_rules import make_rule


def _selection(value: str, text: str, start: int | None = None) -> dict:
    start = value.index(text) if start is None else start
    return {"start": start, "end": start + len(text), "text": text, "value": value}


@pytest.fixture()
def clean_fixture(monkeypatch, tmp_path):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(cc_app, "CONFIG_PATH", path)
    monkeypatch.setitem(cc_app.PREFS, "auto_clean", False)
    return cfg, path


async def _open_clean(user, path="/highlight-fixture"):
    from nicegui import ui

    @ui.page(path)
    async def fixture_page():
        await cc_app.clean_page()

    await user.open(path)


def _checkbox(user, label):
    matches = [element for element in user.find().elements
               if getattr(element, "_text", None) == label]
    assert len(matches) == 1
    return matches[0]


def _chart_textarea(user):
    matches = [element for element in user.find().elements
               if element.props.get("label", "").startswith("Chart text")]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.nicegui_main_file("")
async def test_off_mode_does_not_change_selection_or_config(user, clean_fixture):
    from nicegui import ui

    _cfg, path = clean_fixture
    cc_app.CLEAN_STATE.update(input="Keep remove", mode="clean", result=None,
                              result_text="", audit=None, summary=None, qa=[])
    await _open_clean(user, "/highlight-off")
    before = load_config(path)

    user.find(marker="highlight-source").trigger("mouseup", _selection("Keep remove", "remove"))

    assert _chart_textarea(user).value == "Keep remove"
    assert load_config(path) == before


@pytest.mark.nicegui_main_file("")
async def test_remove_mode_saves_only_selection_and_checkboxes_are_exclusive(user, clean_fixture):
    from nicegui import ui

    _cfg, path = clean_fixture
    text = "Keep remove"
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    await _open_clean(user, "/highlight-remove")
    with user.client:
        _checkbox(user, "Remove mode").set_value(True)
        _checkbox(user, "Replace mode").set_value(True)
    checks = {element._text: element for element in user.find(ui.checkbox).elements}
    assert checks["Remove mode"].value is False
    assert checks["Replace mode"].value is True
    with user.client:
        _checkbox(user, "Remove mode").set_value(True)
    assert checks["Remove mode"].value is True
    assert checks["Replace mode"].value is False

    user.find(marker="highlight-source").trigger("mouseup", _selection(text, "remove"))
    assert _chart_textarea(user).value == "Keep "
    saved = load_config(path)
    assert saved["learned_rules"] == [make_rule("remove")]


@pytest.mark.nicegui_main_file("")
async def test_undo_restores_text_rule_and_previous_enabled_setting(user, clean_fixture):
    from nicegui import ui

    _cfg, path = clean_fixture
    text = "Keep remove"
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    await _open_clean(user, "/highlight-undo")
    with user.client:
        _checkbox(user, "Remove mode").set_value(True)
    user.find(marker="highlight-source").trigger("mouseup", _selection(text, "remove"))
    user.find("Undo last highlight").click()

    assert _chart_textarea(user).value == text
    restored = load_config(path)
    assert restored["learned_rules"] == []
    assert restored.get("stage_options", {}).get("learned_rules", {}).get("enabled") is None


@pytest.mark.nicegui_main_file("")
async def test_replacement_cancel_does_not_write_and_confirm_escapes_backslash(user, clean_fixture):
    from nicegui import ui

    _cfg, path = clean_fixture
    text = "Keep remove"
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    await _open_clean(user, "/highlight-cancel")
    with user.client:
        _checkbox(user, "Replace mode").set_value(True)
    selection = _selection(text, "remove")
    user.find(marker="highlight-source").trigger("mouseup", selection)
    await user.should_see("Replace highlighted text")
    user.find("Cancel").click()
    assert load_config(path)["learned_rules"] == []
    assert _chart_textarea(user).value == text


@pytest.mark.nicegui_main_file("")
async def test_replacement_confirm_escapes_backslash(user, clean_fixture):
    from nicegui import ui

    _cfg, path = clean_fixture
    text = "Keep remove"
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    await _open_clean(user, "/highlight-confirm")
    with user.client:
        _checkbox(user, "Replace mode").set_value(True)
    selection = _selection(text, "remove")
    user.find(marker="highlight-source").trigger("mouseup", selection)
    await user.should_see("Replace highlighted text")
    replacement = next(
        element for element in user.find(ui.textarea).elements
        if element.props.get("label") == "Replace with"
    )
    with user.client:
        replacement.set_value(r"A\B")
    user.find("Replace & remember").click()
    assert _chart_textarea(user).value == r"Keep A\B"
    assert load_config(path)["learned_rules"] == [make_rule("remove", r"A\B")]


@pytest.mark.nicegui_main_file("")
@pytest.mark.parametrize("kind", ["remove", "replace"])
async def test_stale_selection_and_conflicting_rule_are_refused(user, clean_fixture, kind):
    from nicegui import ui

    _cfg, path = clean_fixture
    text = "Keep remove"
    cfg = load_config(path)
    cfg["learned_rules"] = [make_rule("remove", "already")]
    save_config(cfg, path)
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    await _open_clean(user, f"/highlight-conflict-{kind}")
    with user.client:
        _checkbox(user, "Remove mode" if kind == "remove" else "Replace mode").set_value(True)

    stale = _selection(text, "remove")
    with user.client:
        _chart_textarea(user).set_value("Changed remove")
    user.find(marker="highlight-source").trigger("mouseup", stale)
    assert load_config(path)["learned_rules"] == [make_rule("remove", "already")]
    assert _chart_textarea(user).value == "Changed remove"

    with user.client:
        _chart_textarea(user).set_value(text)
    user.find(marker="highlight-source").trigger("mouseup", _selection(text, "remove"))
    assert load_config(path)["learned_rules"] == [make_rule("remove", "already")]


@pytest.mark.nicegui_main_file("")
async def test_rules_page_mounts_with_isolated_config(user, clean_fixture):
    from nicegui import ui

    @ui.page("/rules")
    def fixture_page():
        cc_app.text_rules_page()

    await user.open("/rules")
    await user.should_see("My text rules")
    await user.should_see("Remove & replace")
    await user.should_see("Abbreviations")
    await user.should_see("Share & import")
