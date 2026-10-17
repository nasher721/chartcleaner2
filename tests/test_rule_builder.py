"""Rule builder: preview before saving — hits, facts lost, never-remove clashes, imports."""

from __future__ import annotations

import pytest

from chartcleaner import rule_builder, rule_sharing, rule_shapes
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


def test_preview_reports_a_conflicting_replacement(cfg):
    cfg["learned_rules"] = [make_rule("pt", "patient")]
    assert rule_builder.preview(cfg, make_rule("pt", "pt."), "pt seen").conflict


def test_preview_honors_scope(cfg):
    text = "Medications:\nper pharmacy\n\nAssessment and Plan:\nper pharmacy\n"
    result = rule_builder.preview(cfg, {"kind": "text", "text": "per pharmacy"}, text,
                                  scope={"mode": "only", "sections": ["medications"]})
    assert result.hits == 1


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
