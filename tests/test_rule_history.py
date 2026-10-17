"""Rule journal: every config save records rule changes; any entry can be undone alone."""

from __future__ import annotations

import pytest

from chartcleaner import rule_history, rule_meta, rule_shapes, secure_store, store
from chartcleaner.engine import load_config, load_default_config, save_config
from chartcleaner.highlight_rules import make_rule


@pytest.fixture()
def path(tmp_path):
    cfg = load_default_config()
    cfg["learned_rules"] = []
    p = tmp_path / "config.json"
    save_config(cfg, p)  # first save: nothing to compare against
    return p


def _save(path, change, source="test"):
    cfg = load_config(path)
    change(cfg)
    save_config(cfg, path, source=source)
    return cfg


def test_first_save_and_unrelated_saves_are_not_journaled(path):
    assert rule_history.entries() == []
    _save(path, lambda c: c.update(wrap_output=not c.get("wrap_output")))
    assert rule_history.entries() == []


def test_adding_a_rule_is_journaled_with_a_readable_summary(path):
    _save(path, lambda c: c["learned_rules"].append(make_rule("Printed on")), source="selection")
    [entry] = rule_history.entries()
    assert entry["source"] == "selection" and entry["summary"] == "Added: Remove “Printed on”"
    assert entry["changes"][0] == {"kind": "learned", "op": "add", "pair": make_rule("Printed on"),
                                   "index": 0}


def test_journal_is_encrypted(path):
    _save(path, lambda c: c["learned_rules"].append(make_rule("Secret Phrase")))
    raw = store.RULE_HISTORY_FILE.read_bytes()
    assert secure_store.is_encrypted(raw) and b"Secret" not in raw


def test_revert_one_entry_keeps_later_edits(path):
    _save(path, lambda c: c["learned_rules"].append(make_rule("first")))
    _save(path, lambda c: c["learned_rules"].append(make_rule("second")))
    first = rule_history.entries()[-1]
    out = rule_history.revert(load_config(path), first)
    assert out["learned_rules"] == [make_rule("second")]
    # reverting twice is a no-op
    assert rule_history.revert(out, first)["learned_rules"] == [make_rule("second")]


def test_revert_a_delete_puts_the_rule_back_in_place(path):
    _save(path, lambda c: c["learned_rules"].extend([make_rule("a"), make_rule("b"), make_rule("c")]))
    _save(path, lambda c: c["learned_rules"].remove(make_rule("b")))
    entry = rule_history.entries()[0]
    assert entry["summary"] == "Deleted: Remove “b”"
    out = rule_history.revert(load_config(path), entry)
    assert out["learned_rules"] == [make_rule("a"), make_rule("b"), make_rule("c")]


def test_shape_settings_and_pauses_are_journaled_and_undone(path):
    shape = rule_shapes.normalize({"kind": "line_start", "text": "Printed by"})
    pair = rule_shapes.build(shape)

    def add(c):
        c.update(rule_meta.add_rule(c, pair, shape=shape, source="builder")[0])
    _save(path, add)
    assert rule_history.entries()[0]["summary"] == "Added: Remove lines starting with “Printed by”"

    def pause(c):
        c.update(rule_meta.set_enabled(c, [pair[0]], False))
    _save(path, pause)
    entry = rule_history.entries()[0]
    assert entry["summary"] == "Paused 1 rule(s)"
    out = rule_history.revert(load_config(path), entry)
    assert rule_meta.is_enabled(out, pair[0]) and rule_meta.entry(out, pair[0])["shape"] == shape
    # undoing the add drops the rule and its settings
    out = rule_history.revert(out, rule_history.entries()[-1])
    assert out["learned_rules"] == [] and rule_meta.meta_map(out) == {}


def test_abbreviations_and_exceptions(path):
    def add(c):
        c["abbreviations"] = {"custom": [{"term": "left side weakness", "replacement": "LSW",
                                          "enabled": True}], "disabled": ["hypertension"]}
        c.setdefault("stage_options", {}).setdefault("boilerplate", {})["exceptions"] = ["keep me"]
    _save(path, add)
    entry = rule_history.entries()[0]
    assert "Added abbreviation “left side weakness” → “LSW”" in entry["summary"]
    assert "Turned off 1 abbreviation(s)" in entry["summary"]
    assert "Never remove “keep me”" in entry["summary"]
    out = rule_history.revert(load_config(path), entry)
    assert "abbreviations" not in out  # the edit created the group, so undo drops it
    assert out["stage_options"]["boilerplate"]["exceptions"] == []


def test_stage_switch_only_counts_with_a_rule_change(path):
    _save(path, lambda c: c.setdefault("stage_options", {}).setdefault("learned_rules", {})
          .update(enabled=False))
    assert rule_history.entries() == []

    def add(c):
        c["learned_rules"].append(make_rule("x"))
        c["stage_options"]["learned_rules"]["enabled"] = True
    _save(path, add)
    out = rule_history.revert(load_config(path), rule_history.entries()[0])
    assert out["stage_options"]["learned_rules"]["enabled"] is False


def test_mark_reverted_get_and_clear(path):
    _save(path, lambda c: c["learned_rules"].append(make_rule("x")))
    entry = rule_history.entries()[0]
    rule_history.mark_reverted(entry["id"], by="undo")
    assert rule_history.get(entry["id"])["reverted"] and rule_history.get("nope") is None
    assert rule_history.clear() == 1 and rule_history.entries() == []


def test_journal_is_capped(path, monkeypatch):
    monkeypatch.setattr(rule_history, "MAX_ENTRIES", 3)
    for i in range(5):
        _save(path, lambda c, i=i: c["learned_rules"].append(make_rule(f"r{i}")))
    assert [e["summary"] for e in rule_history.entries()] == [
        "Added: Remove “r4”", "Added: Remove “r3”", "Added: Remove “r2”"]


def test_delete_all_chart_data_removes_the_journal(path):
    _save(path, lambda c: c["learned_rules"].append(make_rule("x")))
    assert store.RULE_HISTORY_FILE.exists()
    store.delete_all_chart_data()
    assert not store.RULE_HISTORY_FILE.exists()


def test_a_broken_journal_never_blocks_saving(path, monkeypatch):
    monkeypatch.setattr(rule_history, "record", lambda *a, **k: 1 / 0)
    _save(path, lambda c: c["learned_rules"].append(make_rule("x")))
    assert load_config(path)["learned_rules"] == [make_rule("x")]


def test_undoing_the_first_abbreviation_drops_the_group(path):
    cfg = load_config(path)
    cfg.pop("abbreviations", None)
    save_config(cfg, path)
    entry = save_config({**cfg, "abbreviations": {"custom": [{"term": "x y", "replacement": "XY"}]}},
                        path, source="selection")
    assert entry is not None and entry["source"] == "selection"
    assert "abbreviations" not in rule_history.revert(load_config(path), entry)
