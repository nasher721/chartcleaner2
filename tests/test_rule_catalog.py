"""Rules page back end: rule list rows, filters/sorts, archiving dead rules, merging,
abbreviation level and read-back, rule_health's paused status."""

from __future__ import annotations

import pytest

from chartcleaner import rule_catalog, rule_health, rule_meta, rule_shapes
from chartcleaner.abbreviations import abbreviate, read_back
from chartcleaner.engine import load_default_config, validate_config
from chartcleaner.highlight_rules import make_rule
from chartcleaner.stage_core import rule_id


@pytest.fixture()
def cfg():
    out = load_default_config()
    out["learned_rules"] = []
    return out


def _runs(hits: dict[str, int], n: int = 6) -> list[dict]:
    return [{"ts": f"2026-10-{10 + i:02d}T08:00:00", "lines_before": 50,
             "stages": [{"id": "learned_rules", "details": {"rule_hits": dict(hits)}}]}
            for i in range(n)]


def _with(cfg, *pairs):
    cfg["learned_rules"] = [list(p) for p in pairs]
    return cfg


def test_rows_join_settings_and_run_history(cfg):
    used, dead = make_rule("Printed on"), make_rule("Page 1 of 2")
    cfg = _with(cfg, used, dead)
    cfg = rule_meta.update(cfg, used[0], note="footer", tags=["epic"], source="selection",
                           added="2026-10-01T09:00:00")
    rows = rule_catalog.rows(cfg, _runs({rule_id(used[0]): 2}))
    assert [r["text"] for r in rows] == ["Printed on", "Page 1 of 2"]
    assert rows[0]["hits"] == 12 and rows[0]["status"] == "ok" and rows[0]["tags"] == ["epic"]
    assert rows[0]["source_label"] == "Highlighted on Clean" and rows[0]["label"] == "Remove “Printed on”"
    assert rows[1]["status"] == "never matched"
    assert rule_catalog.tags(cfg) == ["epic"]


def test_filters_and_sorts(cfg):
    a, b, c = make_rule("alpha"), make_rule("beta", "B"), make_rule("gamma")
    cfg = _with(cfg, a, b, c)
    cfg = rule_meta.update(cfg, c[0], added="2026-10-05T00:00:00", source="quick_add")
    cfg = rule_meta.set_enabled(cfg, [a[0]], False)
    rows = rule_catalog.rows(cfg, _runs({rule_id(b[0]): 3}))
    texts = lambda rs: [r["text"] for r in rs]  # noqa: E731
    assert texts(rule_catalog.filter_rows(rows, kind="replace")) == ["beta"]
    assert texts(rule_catalog.filter_rows(rows, status="paused")) == ["alpha"]
    assert texts(rule_catalog.filter_rows(rows, source="quick_add")) == ["gamma"]
    assert texts(rule_catalog.filter_rows(rows, query="GAM")) == ["gamma"]
    assert texts(rule_catalog.filter_rows(rows, sort="hits"))[0] == "beta"
    assert texts(rule_catalog.filter_rows(rows, sort="added"))[0] == "gamma"
    assert texts(rule_catalog.filter_rows(rows, sort="text")) == ["alpha", "beta", "gamma"]


def test_archive_unused_pauses_only_dead_rules(cfg):
    used, dead = make_rule("Printed on"), make_rule("Page 1 of 2")
    cfg = _with(cfg, used, dead)
    out, archived = rule_catalog.archive_unused(cfg, _runs({rule_id(used[0]): 1}))
    assert archived == [dead[0]]
    assert rule_meta.entry(out, dead[0]) == {"enabled": False, "archived": True}
    assert rule_meta.is_enabled(out, used[0])
    assert rule_catalog.archive_unused(cfg, [])[1] == []  # no history: nothing is "dead"


def test_paused_rules_are_not_dead_weight(cfg):
    dead = make_rule("Page 1 of 2")
    cfg = rule_meta.set_enabled(_with(cfg, dead), [dead[0]], False)
    [row] = [r for r in rule_health.report(cfg, _runs({})) if r["stage"] == "learned_rules"]
    assert row["status"] == "paused"


def test_merge_suggestions_fold_look_alike_literals(cfg):
    a = make_rule("Electronically signed by Jane Doe on 10/14/2026")
    b = make_rule("Electronically signed by John Smith on 10/15/2026")
    keep = make_rule("Printed on")
    cfg = _with(cfg, keep, a, b)
    [sug] = rule_catalog.merge_suggestions(cfg)
    assert sug["shape"]["text"] == "Electronically signed by {name} on {date}"
    assert set(sug["patterns"]) == {a[0], b[0]}
    out = rule_catalog.merge(cfg, sug)
    pair = rule_shapes.build(sug["shape"])
    assert out["learned_rules"] == [keep, pair]
    assert rule_meta.entry(out, pair[0])["note"] == "Merged from 2 rules"
    assert validate_config(out)[0] == []


def test_merge_ignores_singletons_paused_and_replacements(cfg):
    a = make_rule("Signed by Jane Doe on 10/14/2026")
    b = make_rule("Signed by John Smith on 10/15/2026", "signed")
    c = make_rule("Signed by Ann Lee on 10/16/2026")
    cfg = rule_meta.set_enabled(_with(cfg, a, b, c), [c[0]], False)
    assert rule_catalog.merge_suggestions(cfg) == []


def test_replace_keeps_settings_and_position(cfg):
    a, b = make_rule("alpha"), make_rule("beta")
    cfg = rule_meta.update(_with(cfg, a, b), a[0], note="n", tags=["t"])
    shape = rule_shapes.normalize({"kind": "line", "text": "alpha"})
    out = rule_meta.replace(cfg, a[0], rule_shapes.build(shape), shape=shape)
    new = rule_shapes.build(shape)
    assert out["learned_rules"] == [new, b]
    info = rule_meta.entry(out, new[0])
    assert info["note"] == "n" and info["tags"] == ["t"] and info["shape"] == shape
    assert rule_meta.entry(out, a[0]) == {}
    with pytest.raises(ValueError):
        rule_meta.replace(out, "gone", b)


# ---- abbreviations -----------------------------------------------------------

TEXT = "hypertension and chronic kidney disease, left side weakness, with respect to"


@pytest.mark.parametrize("level, expect", [
    ("standard", "HTN and CKD, LSW, w respect to"),
    ("conservative", "HTN and CKD, LSW, with respect to"),
    ("custom_only", "hypertension and chronic kidney disease, LSW, with respect to"),
])
def test_abbreviation_levels(level, expect):
    cfg = {"abbreviations": {"level": level,
                             "custom": [{"term": "left side weakness", "replacement": "LSW"}]}}
    assert abbreviate(TEXT, cfg)[0] == expect


def test_abbreviation_level_is_validated(cfg):
    cfg["abbreviations"] = {"level": "loud"}
    assert any("abbreviations.level" in e for e in validate_config(cfg)[0])
    cfg["abbreviations"] = {"level": "conservative"}
    assert validate_config(cfg)[0] == []


def test_read_back_names_other_meanings():
    assert "multiple sclerosis" in read_back("mental status", "MS")["message"]
    assert read_back("left side weakness", "LSW")["message"] == ""
