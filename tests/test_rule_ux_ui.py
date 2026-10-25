"""Clean page rule UX: whole-line removal, rule builder, collect tray, multi-step undo,
context menu, abbreviate "just here" + read-back, pause from the review dialog."""

from __future__ import annotations

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import common
from chartcleaner import rule_history, rule_meta, rule_shapes, store
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
from chartcleaner.engine import load_config, load_default_config, save_config
from chartcleaner.highlight_rules import make_rule

pytestmark = pytest.mark.nicegui_main_file("")

CHART = ("Progress Note\n"
         "Electronically signed by John Smith, MD on 10/15/2026 09:30\n"
         "BP 120/80\n"
         "Electronically signed by Jane Doe, MD on 10/14/2026 08:12\n")
SIGNED = "Electronically signed by Jane Doe, MD on 10/14/2026 08:12"


@pytest.fixture()
def page(monkeypatch, tmp_path):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "append_run", lambda _r: None)
    monkeypatch.setitem(common.PREFS, "auto_clean", False)
    monkeypatch.setitem(common.PREFS, "onboarded", True)
    monkeypatch.setitem(common.PREFS, "selection_mode", "ask")
    monkeypatch.setattr(cc_app.LocalLlmClient, "is_available", lambda self: False)
    monkeypatch.setattr(cc_app.LocalLlmClient, "list_models", lambda self: [])
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    yield path
    CLEAN_STATE.clear()
    CLEAN_STATE.update(before)
    AUTO_LAST.clear()
    AUTO_LAST.update(auto_before)


async def _open(user: User, route: str, text: str) -> None:
    CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[])

    @ui.page(route)
    async def fixture_page():
        await cc_app.clean_page()

    await user.open(route)


def _selection(value: str, text: str) -> dict:
    start = value.index(text)
    return {"start": start, "end": start + len(text), "text": text, "value": value}


def _select(user, value, text):
    user.find(marker="highlight-source").trigger("mouseup", _selection(value, text))


def _chart(user):
    return next(e for e in user.find().elements
                if e.props.get("label", "").startswith("Chart text"))


def _learned(path):
    return load_config(path).get("learned_rules") or []


async def _wait_rules(path, count):
    for _ in range(100):
        if len(_learned(path)) == count:
            return
        await asyncio.sleep(0.02)
    assert len(_learned(path)) == count


async def test_line_removes_every_line_containing_the_selection(user: User, page):
    text = "a\nPrinted by Epic 10/1\nb\n"
    await _open(user, "/ux-line", text)
    _select(user, text, "Printed by")
    user.find(marker="sel-line").click()
    assert _chart(user).value == "a\nb\n"
    [pair] = _learned(page)
    assert pair == rule_shapes.build({"kind": "line", "text": "Printed by"})
    assert rule_meta.entry(load_config(page), pair[0])["shape"]["kind"] == "line"


async def test_builder_saves_a_generalized_rule_and_applies_it(user: User, page):
    await _open(user, "/ux-builder", CHART)
    _select(user, CHART, SIGNED)
    user.find(marker="sel-build").click()
    await user.should_see(marker="rule-builder")
    choice = next(iter(user.find(marker="builder-choice").elements))
    named = next(k for k, v in choice.options.items() if v.startswith("Lines like this with any name"))
    with user.client:
        choice.set_value(named)
    await user.should_see("Matches 2 time(s) in this chart")
    user.find(marker="builder-save").click()
    assert _chart(user).value == "Progress Note\nBP 120/80\n"
    [pair] = _learned(page)
    shape = rule_meta.entry(load_config(page), pair[0])["shape"]
    assert shape["template"] and "{name}" in shape["text"]
    assert rule_meta.entry(load_config(page), pair[0])["source"] == "builder"


async def test_builder_warns_before_deleting_clinical_values(user: User, page):
    text = "BP 120/80 reviewed\nPlan ok\n"
    await _open(user, "/ux-builder-warn", text)
    _select(user, text, "reviewed")
    user.find(marker="sel-build").click()
    kind = next(iter(user.find(marker="builder-kind").elements))
    with user.client:
        kind.set_value("line")
    await user.should_see("Would delete clinical values from this chart")


async def test_builder_excerpt_shows_the_change_and_leaves_the_chart(user: User, page):
    text = "above line\nBP 120/80 & <reviewed>\nbelow line\n"
    await _open(user, "/ux-builder-excerpt", text)
    _select(user, text, "reviewed")
    user.find(marker="sel-build").click()
    await user.should_see(marker="rule-builder")
    kind = next(iter(user.find(marker="builder-kind").elements))
    with user.client:
        kind.set_value("line")
    await user.should_see(marker="builder-excerpt", content="above line")
    await user.should_see(marker="builder-excerpt", content="reviewed")
    await user.should_see("Would delete clinical values from this chart")
    html = next(iter(user.find(marker="builder-excerpt").elements)).content
    assert "&amp;" in html and "&lt;" in html
    assert "<reviewed>" not in html
    assert _chart(user).value == text


async def test_builder_scope_and_note_are_saved(user: User, page):
    text = "Medications:\nper pharmacy\n\nAssessment and Plan:\nper pharmacy\n"
    await _open(user, "/ux-builder-scope", text)
    _select(user, text, "per pharmacy")
    user.find(marker="sel-build").click()
    scope = next(iter(user.find(marker="builder-scope").elements))
    with user.client:
        scope.set_value("only")
    sections = next(e for e in user.find(ui.select).elements if e.props.get("label") == "Sections")
    with user.client:
        sections.set_value(["medications"])
    user.find(marker="builder-save").click()
    assert _chart(user).value.count("per pharmacy") == 1
    [pair] = _learned(page)
    assert rule_meta.entry(load_config(page), pair[0])["scope"] == {"mode": "only",
                                                                   "sections": ["medications"]}


async def test_undo_steps_back_through_several_edits(user: User, page):
    text = "one two three"
    await _open(user, "/ux-undo", text)
    _select(user, text, "one")
    user.find(marker="sel-remove").click()
    after_one = _chart(user).value
    _select(user, after_one, "three")
    user.find(marker="sel-remove").click()
    await _wait_rules(page, 2)
    user.find("Undo last highlight").click()
    assert _chart(user).value == after_one and _learned(page) == [make_rule("one")]
    user.find("Undo last highlight").click()
    assert _chart(user).value == text and _learned(page) == []
    assert all(e.get("reverted") for e in rule_history.entries() if e["source"] == "selection")


async def test_undo_survives_an_unrelated_later_rule(user: User, page):
    text = "one two"
    await _open(user, "/ux-undo-later", text)
    _select(user, text, "one")
    user.find(marker="sel-remove").click()
    await _wait_rules(page, 1)
    cfg = load_config(page)
    cfg["learned_rules"].append(make_rule("elsewhere"))
    save_config(cfg, page)  # e.g. added on the Rules page meanwhile
    user.find("Undo last highlight").click()
    assert _learned(page) == [make_rule("elsewhere")]


async def test_collect_saves_all_at_once_as_one_undo_step(user: User, page):
    text = "alpha beta gamma"
    await _open(user, "/ux-collect", text)
    _select(user, text, "alpha")
    user.find(marker="selection-overflow").click()
    user.find(marker="sel-collect").click()
    _select(user, text, "gamma")
    user.find(marker="selection-overflow").click()
    user.find(marker="sel-collect").click()
    await user.should_see("Collected (2):")
    user.find(marker="tray-save").click()
    assert _chart(user).value == " beta "
    assert _learned(page) == [make_rule("alpha"), make_rule("gamma")]
    user.find("Undo last highlight").click()
    assert _learned(page) == [] and _chart(user).value == text


async def test_context_menu_acts_on_the_selection(user: User, page):
    text = "keep drop"
    await _open(user, "/ux-ctx", text)
    _select(user, text, "drop")
    user.find(marker="ctx-remove").click()
    assert _chart(user).value == "keep "
    await _wait_rules(page, 1)
    assert _learned(page) == [make_rule("drop")]


async def test_abbreviate_just_here_saves_nothing(user: User, page):
    text = "Pt has left side weakness today"
    await _open(user, "/ux-abbr-here", text)
    _select(user, text, "left side weakness")
    user.find(marker="sel-abbreviate").click()
    await user.should_see("Abbreviate highlighted text")
    user.find(marker="abbr-just-here").click()
    assert _chart(user).value == "Pt has LSW today"
    assert "abbreviations" not in load_config(page)
    user.find("Undo last highlight").click()
    assert _chart(user).value == text


async def test_abbreviate_dialog_shows_other_meanings(user: User, page):
    text = "Pt mental status improved"
    await _open(user, "/ux-abbr-meanings", text)
    _select(user, text, "mental status")
    user.find(marker="sel-abbreviate").click()
    abbr = next(e for e in user.find(ui.input).elements if e.props.get("label") == "Abbreviation")
    with user.client:
        abbr.set_value("MS")
    await user.should_see("also means multiple sclerosis")


async def test_review_dialog_describes_and_pauses_a_learned_rule(user: User, page):
    cfg = load_config(page)
    shape = rule_shapes.normalize({"kind": "line_start", "text": "Printed by"})
    cfg, _ = rule_meta.add_rule(cfg, rule_shapes.build(shape), shape=shape, source="builder",
                                note="Epic footer")
    save_config(cfg, page)
    await _open(user, "/ux-pause", "Printed by Epic\nPlan: recheck\n")
    result = CLEAN_STATE.get("result")
    user.find(marker="run-clean").click()
    for _ in range(200):
        if CLEAN_STATE.get("result") is not None and CLEAN_STATE.get("result") is not result:
            break
        await asyncio.sleep(0.02)
    user.find(marker="review-diff").trigger("click", "g0")
    await user.should_see("Remove lines starting with “Printed by”")
    await user.should_see("note: Epic footer")
    user.find(marker="pause-rule").click()
    pair = rule_shapes.build(shape)
    assert not rule_meta.is_enabled(load_config(page), pair[0])
    user.find("Undo last highlight").click()
    assert rule_meta.is_enabled(load_config(page), pair[0])


async def test_undo_history_dialog_lists_edits(user: User, page):
    text = "one two"
    await _open(user, "/ux-history", text)
    _select(user, text, "one")
    user.find(marker="sel-remove").click()
    user.find(marker="advanced-input-tools").click()
    user.find(marker="undo-history-button").click()
    await user.should_see(marker="undo-history")
    await user.should_see("Added: Remove “one”")
