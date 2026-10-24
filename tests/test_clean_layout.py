"""Clean-page controls stay compact while selection actions remain available."""

import pytest

import app as cc_app
from app_pages import common
from chartcleaner.engine import load_default_config, save_config


@pytest.mark.nicegui_main_file("")
async def test_selection_options_are_disclosed_and_upload_is_quiet(user, monkeypatch, tmp_path):
    from nicegui import ui

    path = tmp_path / "config.json"
    save_config(load_default_config(), path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    monkeypatch.setitem(common.PREFS, "onboarded", True)
    cc_app.CLEAN_STATE.update(input="", mode="clean", result=None, tag="")

    @ui.page("/clean-layout-fixture")
    async def fixture():
        await cc_app.clean_page()

    await user.open("/clean-layout-fixture")
    settings = next(e for e in user.find(ui.expansion).elements
                    if e.props.get("label") == "Selection settings")
    assert settings.value is False
    toggle = next(iter(user.find(marker="selection-mode").elements))
    assert toggle.parent_slot.parent is settings
    await user.should_not_see(marker="selection-bar")
    # The simulated user does not execute Quasar's expansion click handler.
    with user.client:
        settings.set_value(True)
    assert settings.value is True
    await user.should_see(marker="selection-mode")
    upload = next(iter(user.find(ui.upload).elements))
    assert upload.props.get("hide-upload-progress") is True
    tag = next(iter(user.find(marker="bed-tag").elements))
    assert tag.props["label"] == "Bed tag (optional)"
    destination = next(iter(user.find(marker="clean-destination").elements))
    assert destination.props["label"] == "Destination"
    assert destination.value == ""
