"""Rule builder: preview before saving — hits, facts lost, never-remove clashes, imports."""

from __future__ import annotations

import pytest

from chartcleaner import rule_builder, rule_preview, rule_sharing, rule_shapes
from chartcleaner.engine import load_default_config
from chartcleaner.highlight_rules import make_rule

CHART = """Progress Note
Printed by Epic on 10/14/2026
BP 120/80 HR 72
Printed by Epic on 10/15/2026
Plan: continue lisinopril 10 mg daily
"""
OTHER = "Note 2\nPrinted by Epic on 10/16/2026\nNa 141\n"


@pytest.fixture()
def cfg():
    out = load_default_config()
    out["learned_rules"] = []
    return out


def test_preview_counts_hits_and_samples_here_and_in_recent_charts(cfg):
    shape = {"kind": "line_start", "text": "Printed by Epic"}
    result = rule_builder.preview(cfg, shape, CHART, [CHART, OTHER])
    assert result.hits == 2 and len(result.samples) == 2
    assert result.samples[0]["before"].startswith("Printed by Epic") and result.samples[0]["after"] == ""
    assert result.facts_lost == [] and result.warnings == []
    assert result.impact["charts_checked"] == 1 and result.impact["lines_changed"] == 1
    assert result.headline.startswith("Matches 2 time(s) in this chart. Would also change 1 line(s)")
    assert result.to_dict()["hits"] == 2


def test_preview_warns_about_clinical_values(cfg):
    result = rule_builder.preview(cfg, {"kind": "line", "text": "lisinopril"}, CHART)
    assert result.facts_lost and "Would delete clinical values" in result.warnings[0]


def test_preview_reports_never_remove_clashes(cfg):
    cfg.setdefault("stage_options", {})["learned_rules"] = {"exceptions": ["10/15/2026"]}
    result = rule_builder.preview(cfg, {"kind": "line_start", "text": "Printed by Epic"}, CHART)
    assert result.protected == ["10/15/2026"] and result.hits == 1  # the protected line stays
    assert any("never remove" in w for w in result.warnings)
    struck = [text for w in result.excerpt["windows"] for ln in w["lines"] if ln["kind"] == "change"
              for kind, text in ln["segments"] if kind == "del"]
    assert any("10/14/2026" in text for text in struck)
    assert all("10/15/2026" not in text for text in struck)


def test_preview_reports_a_conflicting_replacement(cfg):
    cfg["learned_rules"] = [make_rule("pt", "patient")]
    assert rule_builder.preview(cfg, make_rule("pt", "pt."), "pt seen").conflict


def test_excerpt_does_not_strike_a_repeated_line_outside_scope(cfg):
    text = "Medications:\nalpha extra\nbeta extra\nAssessment:\nalpha extra\n"
    result = rule_builder.preview(cfg, {"kind": "line", "text": "extra"}, text,
                                  scope={"mode": "only", "sections": ["medications"]})
    assert result.hits == 2
    changes = [ln for w in result.excerpt["windows"] for ln in w["lines"] if ln["kind"] == "change"]
    assert [ln["index"] for ln in changes] == [1, 2]
    struck = [text for ln in changes for kind, text in ln["segments"] if kind == "del"]
    assert struck == ["alpha extra", "beta extra"]
    assert all("Assessment:" not in text for text in struck)


def test_preview_honors_scope(cfg):
    text = "Medications:\nper pharmacy\n\nAssessment and Plan:\nper pharmacy\n"
    result = rule_builder.preview(cfg, {"kind": "text", "text": "per pharmacy"}, text,
                                  scope={"mode": "only", "sections": ["medications"]})
    assert result.hits == 1
    # The medications line is struck. The same words in the plan stay plain.
    changes = [ln for w in result.excerpt["windows"] for ln in w["lines"] if ln["kind"] == "change"]
    assert [ln["index"] for ln in changes] == [1]
    assert changes[0]["segments"] == [("del", "per pharmacy")]


def test_rank_scores_generalize_candidates(cfg):
    cands = rule_shapes.generalize("Printed by Epic on 10/14/2026")
    scores = rule_builder.rank(cfg, [c.shape for c in cands], CHART, [OTHER, CHART])
    by_text = {c.shape["text"]: s for c, s in zip(cands, scores)}
    assert by_text["Printed by Epic on 10/14/2026"] == {"hits": 1, "charts": 0, "lines": 0}
    assert by_text["Printed by Epic on {date}"] == {"hits": 2, "charts": 1, "lines": 1}


def test_check_shape(cfg):
    assert rule_builder.check_shape({"kind": "line", "text": "x"})[0]["kind"] == "line"
    assert rule_builder.check_shape({"kind": "line", "text": ""}) == (None, "Enter some text for the rule first.")


def test_import_impact_shows_what_new_rules_remove(cfg):
    other = load_default_config()
    other["learned_rules"] = [rule_shapes.build({"kind": "line", "text": "lisinopril"})]
    payload = rule_sharing.payload_from_config(other)
    impact = rule_sharing.import_impact(cfg, payload, [CHART, OTHER])
    assert impact["charts_checked"] == 2 and impact["charts_changed"] == 1
    assert impact["samples"][0]["before"].startswith("Plan: continue lisinopril")
    assert impact["facts_lost"]


def test_config_impact_ignores_identical_configs(cfg):
    assert rule_builder.config_impact(cfg, cfg, [CHART])["charts_changed"] == 0


def _joined(lines: list[str]) -> str:
    return "\n".join(lines)


def _without(lines: list[str], indexes: set[int]) -> str:
    return _joined(ln for i, ln in enumerate(lines) if i not in indexes)


def test_excerpt_strikes_a_removed_line_with_two_lines_of_context():
    before = ["a", "b", "DROP", "c", "d"]
    excerpt = rule_preview.excerpt_windows(_joined(before), _without(before, {2}))
    assert excerpt["omitted"] == 0
    assert excerpt["windows"] == [{
        "gap_before": 0,
        "lines": [
            {"kind": "context", "index": 0, "segments": [("plain", "a")]},
            {"kind": "context", "index": 1, "segments": [("plain", "b")]},
            {"kind": "change", "index": 2, "segments": [("del", "DROP")]},
            {"kind": "context", "index": 3, "segments": [("plain", "c")]},
            {"kind": "context", "index": 4, "segments": [("plain", "d")]},
        ],
    }]


def test_excerpt_shows_a_replacement_on_the_same_line():
    before = ["a", "b", "foo BAR baz", "c", "d"]
    after = ["a", "b", "foo QUUX baz", "c", "d"]
    excerpt = rule_preview.excerpt_windows(_joined(before), _joined(after))
    change = next(ln for w in excerpt["windows"] for ln in w["lines"] if ln["kind"] == "change")
    assert change["segments"] == [
        ("plain", "foo "), ("del", "BAR"), ("ins", "QUUX"), ("plain", " baz"),
    ]
    assert change["index"] == 2


def test_excerpt_merges_nearby_changes_and_counts_the_gap():
    close = ["A", "b", "c", "D", "e", "f", "g", "h"]
    excerpt = rule_preview.excerpt_windows(_joined(close), _without(close, {0, 3}))
    assert len(excerpt["windows"]) == 1 and excerpt["windows"][0]["gap_before"] == 0
    assert [ln["index"] for ln in excerpt["windows"][0]["lines"] if ln["kind"] == "change"] == [0, 3]

    far = [f"L{i}" for i in range(11)]
    far[0], far[8] = "H0", "H8"
    excerpt = rule_preview.excerpt_windows(_joined(far), _without(far, {0, 8}))
    assert [w["gap_before"] for w in excerpt["windows"]] == [0, 3]
    assert [ln["index"] for ln in excerpt["windows"][1]["lines"] if ln["kind"] == "change"] == [8]


def test_excerpt_clips_context_at_the_note_and_groups_a_deleted_run():
    top = ["DROP", "one", "two", "three"]
    excerpt = rule_preview.excerpt_windows(_joined(top), _without(top, {0}))
    lines = excerpt["windows"][0]["lines"]
    assert lines[0]["kind"] == "change" and lines[0]["index"] == 0
    assert all(ln["kind"] == "context" for ln in lines[1:])

    run = ["a", "b", "D1", "D2", "c", "d"]
    excerpt = rule_preview.excerpt_windows(_joined(run), _without(run, {2, 3}))
    assert len(excerpt["windows"]) == 1
    assert [ln["kind"] for ln in excerpt["windows"][0]["lines"]] == [
        "context", "context", "change", "change", "context", "context"]
    assert [ln["segments"] for ln in excerpt["windows"][0]["lines"] if ln["kind"] == "change"] == [
        [("del", "D1")], [("del", "D2")]]


def test_excerpt_omits_changed_lines_past_twelve_windows():
    stride = 6
    lines = ["keep"] * (stride * 12 + 1)
    for n in range(13):
        lines[n * stride] = f"HIT {n}"
    excerpt = rule_preview.excerpt_windows(_joined(lines), _without(lines, set(range(0, stride * 13, stride))))
    assert len(excerpt["windows"]) == 12
    assert excerpt["omitted"] == 1


def test_excerpt_places_an_inserted_line_beside_the_nearest_change():
    excerpt = rule_preview.excerpt_windows("hello world\nnext", "hello\nworld\nnext")
    lines = excerpt["windows"][0]["lines"]
    assert lines[0]["index"] == 0
    assert lines[0]["segments"] == [("plain", "hello"), ("del", " world")]
    assert lines[1] == {"kind": "change", "index": None, "segments": [("ins", "world")]}
    assert lines[2] == {"kind": "context", "index": 1, "segments": [("plain", "next")]}


def test_excerpt_ignores_blank_lines_that_are_not_changes():
    unchanged = rule_preview.excerpt_windows("a\n\nb", "a\n\nb")
    assert unchanged == {"windows": [], "omitted": 0}
    excerpt = rule_preview.excerpt_windows("a\n\nDROP\nb", "a\n\nb")
    changes = [ln for w in excerpt["windows"] for ln in w["lines"] if ln["kind"] == "change"]
    assert changes == [{"kind": "change", "index": 2, "segments": [("del", "DROP")]}]
    blanks = [ln for w in excerpt["windows"] for ln in w["lines"]
              if ln["kind"] == "context" and ln["segments"] == [("plain", "")]]
    assert len(blanks) == 1


def test_excerpt_keeps_whole_segments_and_clips_the_visible_line():
    long = "Q" * 300
    excerpt = rule_preview.excerpt_windows(f"above\n{long}\nbelow", "above\nbelow")
    change = next(ln for w in excerpt["windows"] for ln in w["lines"] if ln["kind"] == "change")
    assert change["segments"] == [("del", long)]
    shown = rule_preview.visible_line(change["segments"])
    assert shown == ("Q" * 240) + "…"
    assert len(shown) == 241


def test_preview_fills_excerpt_from_the_same_alignment_as_samples(cfg):
    result = rule_builder.preview(cfg, {"kind": "line_start", "text": "Printed by Epic"}, CHART)
    assert result.excerpt["omitted"] == 0 and result.excerpt["windows"]
    struck = [text for w in result.excerpt["windows"] for ln in w["lines"] if ln["kind"] == "change"
              for kind, text in ln["segments"] if kind == "del"]
    assert [s["before"] for s in result.samples] == struck
    assert result.samples[0]["after"] == ""
    assert result.warnings == []
    assert result.headline.startswith("Matches 2 time(s) in this chart")
    assert result.to_dict()["excerpt"]["windows"] == result.excerpt["windows"]


def test_preview_with_no_matches_has_an_empty_excerpt(cfg):
    result = rule_builder.preview(cfg, {"kind": "text", "text": "not-in-this-chart"}, CHART)
    assert result.hits == 0 and result.samples == []
    assert result.excerpt == {"windows": [], "omitted": 0}


def test_preview_keeps_samples_when_the_excerpt_cannot_be_built(cfg, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("excerpt failed")

    monkeypatch.setattr(rule_builder, "excerpt_windows", boom)
    result = rule_builder.preview(cfg, {"kind": "line_start", "text": "Printed by Epic"}, CHART)
    assert result.excerpt is None
    assert result.samples[0]["before"].startswith("Printed by Epic") and result.samples[0]["after"] == ""
    assert result.headline.startswith("Matches 2 time(s)")
    assert result.warnings == []
