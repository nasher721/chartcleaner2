"""Rules page UI: pause/resume, details (note, tags, keep lines), clean up unused,
merge look-alikes, Recent changes undo, abbreviation level, edit in the builder."""

from __future__ import annotations

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import common
from chartcleaner import rule_examples, rule_meta, rule_shapes, store
from chartcleaner.engine import load_config, load_default_config, save_config
from chartcleaner.highlight_rules import make_rule
from chartcleaner.stage_core import rule_id

pytestmark = pytest.mark.nicegui_main_file("")


@pytest.fixture()
def path(monkeypatch, tmp_path):
    cfg = load_default_config()
    cfg["learned_rules"] = []
    p = tmp_path / "config.json"
    save_config(cfg, p)
    monkeypatch.setattr(common, "CONFIG_PATH", p)
    monkeypatch.setattr(store, "load_runs", lambda *a, **k: [])
    return p


def _set_rules(path, *pairs, edit=None):
    cfg = load_config(path)
    cfg["learned_rules"] = [list(p) for p in pairs]
    if edit:
        cfg = edit(cfg)
    save_config(cfg, path)


async def _open_tab(user: User, route: str, tab: str) -> None:
    @ui.page(route)
    def fixture_page():
        cc_app.text_rules_page()

    await user.open(route)
    tabs = [e for e in user.find(ui.tab).elements if e.props.get("label") == tab]
    with user.client:
        tabs[0].parent_slot.parent.set_value(tab)


async def test_pause_and_resume_a_rule(user: User, path):
    pair = make_rule("Printed on")
    _set_rules(path, pair)
    await _open_tab(user, "/rp-pause", "Remove & replace")
    await user.should_see("Remove “Printed on”")
    user.find(marker="rule-toggle").click()
    assert not rule_meta.is_enabled(load_config(path), pair[0])
    await user.should_see("Paused")
    user.find(marker="rule-toggle").click()
    assert rule_meta.is_enabled(load_config(path), pair[0])


async def test_stage_switch_pauses_every_rule(user: User, path):
    _set_rules(path, make_rule("Printed on"))
    await _open_tab(user, "/rp-stage", "Remove & replace")
    switch = next(iter(user.find(marker="rules-stage-switch").elements))
    with user.client:
        switch.set_value(False)
    assert load_config(path)["stage_options"]["learned_rules"]["enabled"] is False


async def test_details_save_note_tags_and_keep_lines(user: User, path):
    pair = make_rule("Printed on")
    _set_rules(path, pair)
    await _open_tab(user, "/rp-details", "Remove & replace")
    user.find(marker="rule-details-open").click()
    await user.should_see(marker="rule-details")
    note = next(e for e in user.find(ui.input).elements if e.props.get("label") == "Note")
    tags = next(e for e in user.find(ui.input).elements if e.props.get("label") == "Tags (comma-separated)")
    keep = next(iter(user.find(marker="keep-example-input").elements))
    with user.client:
        note.set_value("Epic footer")
        tags.set_value("epic, footer")
        keep.set_value("Medication list printed today")
    user.find("Add line").click()
    user.find(marker="rule-details-save").click()
    info = rule_meta.entry(load_config(path), pair[0])
    assert info["note"] == "Epic footer" and info["tags"] == ["epic", "footer"]
    [ex] = rule_examples.for_rule(pair[0])
    assert ex["kind"] == "keep" and ex["text"] == "Medication list printed today"


async def test_clean_up_unused_archives_dead_rules(user: User, path, monkeypatch):
    used, dead = make_rule("Printed on"), make_rule("Page 1 of 2")
    _set_rules(path, used, dead)
    runs = [{"ts": f"2026-10-1{i}T08:00:00", "lines_before": 40,
             "stages": [{"id": "learned_rules", "details": {"rule_hits": {rule_id(used[0]): 1}}}]}
            for i in range(6)]
    monkeypatch.setattr(store, "load_runs", lambda *a, **k: runs)
    await _open_tab(user, "/rp-unused", "Remove & replace")
    await user.should_see("Clean up unused (1)")
    user.find(marker="rules-archive-unused").click()
    cfg = load_config(path)
    assert rule_meta.entry(cfg, dead[0]) == {"enabled": False, "archived": True}
    await user.should_see("Archived")


async def test_merge_look_alikes(user: User, path):
    a = make_rule("Signed by Jane Doe on 10/14/2026")
    b = make_rule("Signed by John Smith on 10/15/2026")
    _set_rules(path, a, b)
    await _open_tab(user, "/rp-merge", "Remove & replace")
    user.find(marker="rules-merge").click()
    await user.should_see(marker="merge-dialog")
    user.find(marker="merge-apply").click()
    [pair] = load_config(path)["learned_rules"]
    assert rule_meta.entry(load_config(path), pair[0])["shape"]["text"] == "Signed by {name} on {date}"


async def test_recent_changes_undo_button(user: User, path):
    _set_rules(path, make_rule("only"))
    await _open_tab(user, "/rp-history-undo", "Recent changes")
    user.find(marker="history-undo").click()
    assert load_config(path)["learned_rules"] == []
    await user.should_see("undone")


async def test_abbreviation_level_select_saves(user: User, path):
    await _open_tab(user, "/rp-level", "Abbreviations")
    level = next(iter(user.find(marker="abbr-level").elements))
    with user.client:
        level.set_value("conservative")
    assert load_config(path)["abbreviations"]["level"] == "conservative"
    with user.client:
        level.set_value("standard")
    assert "level" not in load_config(path)["abbreviations"]


async def test_editing_a_shaped_rule_opens_the_builder(user: User, path):
    shape = rule_shapes.normalize({"kind": "line_start", "text": "Printed by"})
    old = rule_shapes.build(shape)
    _set_rules(path, edit=lambda c: rule_meta.add_rule(c, old, shape=shape, note="footer")[0])
    await _open_tab(user, "/rp-edit", "Remove & replace")
    user.find(marker="rule-edit").click()
    await user.should_see(marker="rule-builder")
    await user.should_see("Edit rule")
    kind = next(iter(user.find(marker="builder-kind").elements))
    with user.client:
        kind.set_value("line")
    user.find(marker="builder-save").click()
    [pair] = load_config(path)["learned_rules"]
    assert pair == rule_shapes.build({"kind": "line", "text": "Printed by"})
    info = rule_meta.entry(load_config(path), pair[0])
    assert info["shape"]["kind"] == "line" and info["note"] == "footer"
