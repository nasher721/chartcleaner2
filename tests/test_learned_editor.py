"""Focused tests for the rule editor (Rules page → Remove & replace)."""

import copy
import re

import pytest

from chartcleaner.engine import load_default_config
from chartcleaner.highlight_rules import make_rule
from chartcleaner.learned_editor import (
    RULE_SETS,
    _ensure_enabled,
    _label,
    _pairs,
    _save,
    build_entry,
    preview_rule,
)


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


# --- rule sets, plain-text/regex building, previews --------------------------

def test_plain_text_line_removal_covers_the_whole_line_and_round_trips():
    rset = RULE_SETS["emr_line_metadata"]
    pair = build_entry(rset, "Bed Request:", case_sensitive=False, whole_words=False)
    rx = re.compile(pair[0], rset.flags)
    assert rx.sub("", "Plan\n  bed request: ICU step-down\nNext") == "Plan\nNext"
    assert _label(pair, rset) == ("Bed Request:", "", True)


def test_regex_mode_keeps_the_pattern_and_group_replacement():
    rset = RULE_SETS["literal_replacements"]
    pair = build_entry(rset, r"(\d+) year old", r"\1 y/o", regex=True)
    assert pair == [r"(\d+) year old", r"\1 y/o"]
    assert _label(pair, rset) == (r"(\d+) year old", r"\1 y/o", False)


def test_removal_lists_drop_any_replacement():
    assert build_entry(RULE_SETS["boilerplate"], "No data", "x")[1] == ""
    with pytest.raises(ValueError):
        build_entry(RULE_SETS["learned_rules"], "   ")


def test_preview_counts_matches_and_shows_before_after_lines():
    text = "Na 141\nHemoglobin 9.2\nlater hemoglobin 8.9 g/dL\nPlan"
    result = preview_rule(r"\bHemoglobin\b", "Hgb", re.I, text)
    assert result["count"] == 2
    assert result["samples"][0] == ("Hemoglobin 9.2", "Hgb 9.2")
    assert result["samples"][1] == ("later hemoglobin 8.9 g/dL", "later Hgb 8.9 g/dL")


def test_preview_of_a_whole_line_rule_shows_the_line_removed():
    rset = RULE_SETS["emr_line_metadata"]
    pattern, _ = build_entry(rset, "Cosign Required:", whole_words=False)
    result = preview_rule(pattern, "", rset.flags, "A\nCosign Required: Yes\nB")
    assert result["count"] == 1
    assert result["samples"] == [("Cosign Required: Yes", "")]


def test_preview_reports_bad_patterns_and_skips_risky_ones():
    assert preview_rule("(", "", 0, "text")["error"].startswith("Not a valid pattern")
    assert preview_rule("a", r"\9", 0, "a")["error"].startswith("Replacement problem")
    risky = preview_rule(r"(\w+\s?)+:", "", 0, "a" * 50)
    assert risky["risks"] and risky["count"] is None
    assert preview_rule(r"x*", "", 0, "abc")["empty"] is True


def _fixture_editor(config: dict, saved: list, **kw):
    from nicegui import ui

    def load():
        return copy.deepcopy(saved[-1] if saved else config)

    @ui.page(kw.pop("path"))
    def fixture_page():
        from chartcleaner.learned_editor import render_learned_editor

        render_learned_editor(load, lambda cfg: saved.append(copy.deepcopy(cfg)), **kw)


@pytest.mark.nicegui_main_file("")
async def test_delete_is_immediate_and_undo_restores_the_rule(user):
    config = load_default_config()
    config["learned_rules"] = [make_rule("quiet hours"), make_rule("comfort round", "rounding")]
    saved: list = []
    _fixture_editor(config, saved, path="/editor-undo")

    await user.open("/editor-undo")
    user.find(marker="rule-delete-0").click()
    await user.should_see("Deleted 1 rule.")
    assert saved[-1]["learned_rules"] == [make_rule("comfort round", "rounding")]
    user.find(marker="rule-undo").click()
    await user.should_see("Restored.")
    assert saved[-1]["learned_rules"] == config["learned_rules"]


@pytest.mark.nicegui_main_file("")
async def test_bulk_delete_removes_every_selected_rule(user):
    config = load_default_config()
    config["learned_rules"] = [make_rule("one"), make_rule("two"), make_rule("three")]
    saved: list = []
    _fixture_editor(config, saved, path="/editor-bulk")

    await user.open("/editor-bulk")
    with user.client:
        user.find(marker="rule-select-0").elements.pop().set_value(True)
        user.find(marker="rule-select-2").elements.pop().set_value(True)
    user.find(marker="rule-delete-selected").click()
    await user.should_see("Deleted 2 rules.")
    assert saved[-1]["learned_rules"] == [make_rule("two")]


@pytest.mark.nicegui_main_file("")
async def test_built_in_lists_are_editable_and_regex_rules_preview_live(user):
    from nicegui import ui

    config = load_default_config()
    saved: list = []
    _fixture_editor(config, saved, path="/editor-sets",
                    sample_text=lambda: "Plan\nBed Request: ICU\nBed Request: floor\nNext")

    await user.open("/editor-sets")
    with user.client:
        user.find(marker="rule-set").elements.pop().set_value("emr_line_metadata")
    await user.should_see("Line removals")
    await user.should_see("Cosign Required:")  # shipped regex rules are listed

    user.find("Add rule").click()
    await user.should_see("Add line removal")
    with user.client:
        user.find(marker="rule-mode").elements.pop().set_value("regex")
        next(iter(user.find(ui.textarea).elements)).set_value(r"^Bed Request:.*$\n?")
    await user.should_see("Matches 2 times in the preview chart.")
    user.find("Save").click()
    await user.should_see("Rule saved.")
    assert saved[-1]["emr_line_metadata"][-1] == r"^Bed Request:.*$\n?"
    assert len(saved[-1]["emr_line_metadata"]) == len(config["emr_line_metadata"]) + 1


@pytest.mark.nicegui_main_file("")
async def test_never_matched_filter_uses_run_history(user):
    from chartcleaner.stage_core import rule_id

    config = load_default_config()
    config["learned_rules"] = [make_rule("used often"), make_rule("dead rule")]
    hit = rule_id(config["learned_rules"][0][0])
    runs = [{"ts": f"2026-10-0{i}", "lines_before": 40,
             "stages": [{"id": "learned_rules", "details": {"rule_hits": {hit: 2}}}]}
            for i in range(1, 7)]
    saved: list = []
    _fixture_editor(config, saved, path="/editor-health", load_runs=lambda: runs)

    await user.open("/editor-health")
    await user.should_see("12 hits in 6 runs")
    await user.should_see("Never matched in 6 runs")
    with user.client:
        from nicegui import ui
        next(e for e in user.find(ui.select).elements if e.props.get("label") == "Show").set_value("never")
    await user.should_not_see("used often")
    await user.should_see("dead rule")
