"""Focused tests for the learned-rule editor's config boundary."""

from chartcleaner.engine import load_default_config
from chartcleaner.highlight_rules import make_rule
from chartcleaner.learned_editor import _ensure_enabled, _label, _pairs, _save

import pytest


def test_editor_keeps_pair_schema_and_decodes_generated_literals():
    pair = make_rule("No CPR", "DNR", case_sensitive=True, whole_words=False)
    config = {"learned_rules": [pair, [r"MRN:\s*\d+", ""]]}

    assert _pairs(config) == [pair, [r"MRN:\s*\d+", ""]]
    assert _label(pair) == ("No CPR", "DNR", True)
    assert _label(config["learned_rules"][1]) == (r"MRN:\s*\d+", "", False)


def test_enabling_learned_rules_is_local_and_explicit():
    config = {"learned_rules": []}

    assert _ensure_enabled(config) is True
    assert config["stage_options"]["learned_rules"] == {"enabled": True}
    assert _ensure_enabled(config) is False


def test_save_validates_before_calling_callback():
    saved = []
    config = load_default_config()
    config["learned_rules"] = [["quiet hours", ""]]

    ok, error = _save(config, saved.append)

    assert ok is True
    assert error is None
    assert saved == [config]


def test_save_rejects_invalid_rule_without_writing():
    saved = []
    config = load_default_config()
    config["learned_rules"] = [["[", ""]]

    ok, error = _save(config, saved.append)

    assert ok is False
    assert "learned_rules" in error
    assert saved == []


@pytest.mark.nicegui_main_file("")
async def test_editor_builds_and_opens_add_dialog(user):
    from nicegui import ui

    config = load_default_config()
    config["learned_rules"] = []
    saved = []

    @ui.page("/learned-editor-fixture")
    def fixture_page():
        from chartcleaner.learned_editor import render_learned_editor

        render_learned_editor(lambda: dict(config), saved.append)

    await user.open("/learned-editor-fixture")
    await user.should_see("Learned rules")
    await user.should_see("No learned rules yet")
    user.find("Add rule").click()
    await user.should_see("Add learned rule")
    await user.should_see("Text to match")


@pytest.mark.nicegui_main_file("")
async def test_editor_adds_literal_rule_and_refreshes_from_saved_config(user):
    from nicegui import ui

    config = load_default_config()
    config["learned_rules"] = []
    saved = []

    def load():
        return saved[-1].copy() if saved else dict(config)

    @ui.page("/learned-editor-save-fixture")
    def fixture_page():
        from chartcleaner.learned_editor import render_learned_editor

        render_learned_editor(load, lambda cfg: saved.append(cfg))

    await user.open("/learned-editor-save-fixture")
    user.find("Add rule").click()
    with user.client:
        next(iter(user.find(ui.textarea).elements)).set_value("quiet hours")
        inputs = list(user.find(ui.input).elements)
        next(element for element in inputs if element.label == "Replacement (leave empty to remove)").set_value(
            "quiet period"
        )
    user.find("Save").click()
    await user.should_see("quiet hours")
    assert saved[-1]["learned_rules"] == [["(?i:(?<!\\w)quiet\\ hours(?!\\w))", "quiet period"]]
