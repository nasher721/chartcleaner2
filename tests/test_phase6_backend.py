"""Phase 6 back end: stage cache, recent charts, rule inbox/preview, known-good
charts, timeline, trend ranges, PHI round-trip and the new AI presets."""

from __future__ import annotations

from pathlib import Path

import pytest

from chartcleaner import recent_charts, regression_set, rule_inbox, service, store
from chartcleaner.engine import Pipeline, load_default_config
from chartcleaner.rule_preview import preview
from chartcleaner.stage_cache import OWNED_KEYS, StageCache, fingerprint
from chartcleaner.summarizer import PRESET_LABELS, SUMMARY_PRESETS, build_prompt
from chartcleaner.timeline import build as build_timeline
from chartcleaner.trends import build as build_trends

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = (ROOT / "sample_chart.txt").read_text(encoding="utf-8")


def cfg() -> dict:
    c = load_default_config()
    c["nlp_redaction"] = {"enabled": False}
    return c


# ---- stage cache ------------------------------------------------------------

def test_cached_run_matches_uncached_and_reuses_unchanged_stages():
    c = cfg()
    plain = Pipeline(c).run(SAMPLE, fact_check=False)
    cache = StageCache()
    first = Pipeline(c).run(SAMPLE, fact_check=False, cache=cache)
    assert first.text == plain.text
    assert cache.hits == 0 and len(cache) > 0
    second = Pipeline(c).run(SAMPLE, fact_check=False, cache=cache)
    assert second.text == plain.text
    assert cache.misses == len(cache)  # every enabled builtin hit the second time
    assert cache.hits > 0


def test_changing_a_late_option_keeps_earlier_stages_cached():
    c = cfg()
    cache = StageCache()
    Pipeline(c).run(SAMPLE, fact_check=False, cache=cache)
    hits_before = cache.hits
    c2 = dict(c, bullets={"enabled": True, "style": "*"})
    out = Pipeline(c2).run(SAMPLE, fact_check=False, cache=cache)
    assert out.text == Pipeline(c2).run(SAMPLE, fact_check=False).text
    assert cache.hits > hits_before


def test_fingerprint_ignores_only_other_stages_groups():
    c = cfg()
    assert fingerprint(c, "metadata_lines") == fingerprint(dict(c, bullets={"x": 1}), "metadata_lines")
    assert fingerprint(c, "bullets") != fingerprint(dict(c, bullets={"x": 1}), "bullets")
    # a shared key (read by several stages) changes every fingerprint
    assert fingerprint(c, "bullets") != fingerprint(dict(c, clinical_headers=["X"]), "bullets")
    owned = [k for keys in OWNED_KEYS.values() for k in keys]
    assert len(owned) == len(set(owned)), "a key may be owned by one stage only"


def test_tracked_changes_survive_the_cache():
    c = cfg()
    c["emr_line_metadata"] = [r"^Printed by .*$"]
    cache = StageCache()
    text = "Printed by Dr. Lee\nPlan: recheck\n"
    a = Pipeline(c).run(text, track_changes=True, cache=cache)
    b = Pipeline(c).run(text, track_changes=True, cache=cache)
    ch = lambda r: next(s for s in r.stages if s.id == "metadata_lines").details.get("changes")
    assert ch(a) == ch(b) and ch(b)
    ch(b).clear()  # callers may mutate details; the cache keeps its own copy
    assert ch(Pipeline(c).run(text, track_changes=True, cache=cache))


# ---- recent charts ----------------------------------------------------------

def test_recent_charts_are_encrypted_capped_and_purgeable():
    prefs = dict(store.DEFAULT_PREFS, recent_charts={"enabled": True, "keep": 3})
    for i in range(5):
        recent_charts.remember(f"chart number {i}\nsecret MRN 12345{i}", prefs=prefs)
    assert recent_charts.count() == 3
    assert recent_charts.texts()[0].startswith("chart number 4")
    raw = next(store.RECENT_DIR.glob("*.enc")).read_bytes()
    assert b"secret" not in raw
    recent_charts.remember("chart number 4\nsecret MRN 123454", prefs=prefs)  # same chart again
    assert recent_charts.count() == 3
    assert store.delete_all_chart_data() >= 3
    assert recent_charts.count() == 0


def test_recent_charts_disabled_keeps_nothing():
    prefs = dict(store.DEFAULT_PREFS, recent_charts={"enabled": False})
    assert recent_charts.remember("text", prefs=prefs) is None
    assert recent_charts.count() == 0


# ---- rule inbox -------------------------------------------------------------

def _charts(n: int) -> list[str]:
    return [f"Printed on 09/1{i}/2026 by Epic Hyperspace build {i}\n"
            f"Subjective: doing well day {i}.\nNa 13{i} mmol/L\n" for i in range(n)]


def test_inbox_suggests_repeated_chrome_but_never_clinical_lines():
    items = rule_inbox.suggestions(cfg(), _charts(5), hidden=set())
    lines = [i for i in items if i.kind == "remove_line"]
    assert any("Printed on" in i.samples[0] for i in lines)
    assert not any("mmol" in s for i in lines for s in i.samples)
    item = next(i for i in lines if "Printed on" in i.samples[0])
    new = rule_inbox.accept(cfg(), item)
    assert item.pattern in new["emr_line_metadata"]
    out = Pipeline(new).run(_charts(1)[0], fact_check=False).text
    assert "Printed on" not in out and "Na 130" in out


def test_inbox_needs_enough_charts_and_respects_dismissals():
    assert rule_inbox.suggestions(cfg(), _charts(2)) == []
    items = rule_inbox.suggestions(cfg(), _charts(5))
    rule_inbox.dismiss(items[0].id)
    assert items[0].id not in {i.id for i in rule_inbox.suggestions(cfg(), _charts(5))}
    assert "Printed" not in store.INBOX_STATE_FILE.read_text()  # ids only, no chart text


def test_inbox_skips_lines_current_rules_already_remove():
    c = cfg()
    c["emr_line_metadata"] = [r"(?im)^Printed on .*$"]
    items = rule_inbox.suggestions(c, _charts(5), hidden=set())
    assert not any(i.kind == "remove_line" and "Printed on" in i.samples[0] for i in items)


def test_inbox_abbreviation_reject_goes_to_rejected_suggestions():
    out = rule_inbox.reject_phrase(cfg(), "left side weakness")
    assert "left side weakness" in out["abbreviations"]["rejected_suggestions"]


# ---- rule preview -----------------------------------------------------------

def test_preview_reports_other_charts_only():
    texts = ["Printed by Dr. Lee\nPlan A", "Printed by Dr. Kim\nPlan B", "Nothing here"]
    impact = preview(cfg(), "learned_rules", ["Printed by Dr\\. \\w+", ""], texts,
                     skip_text=texts[0])
    assert impact.charts_checked == 2
    assert impact.charts_changed == 1 and impact.lines_changed == 1
    assert impact.samples == [{"before": "Printed by Dr. Kim", "after": ""}]
    assert "1 line(s) in 1 of your 2" in impact.headline


def test_preview_flags_broken_learned_examples():
    c = cfg()
    c["learned_rules"] = [["foo", "bar"]]
    examples = [{"pattern": "foo", "replacement": "bar", "text": "foo", "expected": "bar"}]
    impact = preview(c, "learned_rules", ["bar", "baz"], [], examples=examples)
    assert impact.example_failures and impact.example_failures[0]["now"] == "baz"


# ---- known-good charts ------------------------------------------------------

def test_known_good_detects_and_approves_changes():
    c = cfg()
    text = "Printed by Dr. Lee\nPlan: recheck K\n"
    kg = regression_set.add(text, Pipeline(c).run(text, fact_check=False).text, "K check")
    assert not any(r.changed for r in regression_set.check(c))
    assert b"recheck" not in next(store.KNOWN_GOOD_DIR.glob("*.enc")).read_bytes()
    c2 = dict(c, emr_line_metadata=list(c["emr_line_metadata"]) + [r"(?im)^Printed by .*$"])
    (reg,) = regression_set.check(c2)
    assert reg.changed and any(line.startswith("-Printed by") for line in reg.diff)
    regression_set.approve(kg.id, Pipeline(c2).run(text, fact_check=False).text)
    assert not regression_set.check(c2)[0].changed
    assert regression_set.remove(kg.id) and regression_set.list_charts() == []


# ---- timeline, trends, round-trip, presets ----------------------------------

MULTI = """Date of Service: 09/13/2026
Labs: Na 128, K 3.1
Date of Service: 09/14/2026
Labs: Na 150, K 4.0
Date of Service: 09/15/2026
Labs: Na 138
"""


def test_timeline_offsets_point_at_each_note():
    notes = build_timeline(MULTI)
    assert [n.date for n in notes] == ["09/13/2026", "09/14/2026", "09/15/2026"]
    for n in notes[1:]:
        assert MULTI[n.start:].startswith("Date of Service")
    assert notes[0].preview == "Labs: Na 128, K 3.1"
    assert build_timeline("just one note") == []
    assert len(service.timeline(MULTI)) == 3


def test_trend_flags_and_med_rows():
    report = build_trends(MULTI)
    na = next(t for t in report.labs if t.name == "Na")
    assert na.numbers() == [128.0, 150.0, 138.0]
    assert na.flags() == ["L", "H", ""]
    d = report.to_dict()
    assert d["labs"][0]["flags"] == ["L", "H", ""]
    assert d["med_rows"] == []


def test_restore_round_trip_uses_newest_map():
    store.save_token_map({"Jane Doe": "[[T1]]"}, "test")
    out = service.restore("Reply about [[T1]] and [[T9]].")
    assert out["text"] == "Reply about Jane Doe and [[T9]]." and out["restored"] == 1
    assert service.restore("x", {"A": "[[T1]]"})["map"] == "given"


def test_restore_without_map_explains():
    out = service.restore("[[T1]]")
    assert out["restored"] == 0 and "token map" in out["error"]


def test_new_presets_are_grounded_and_labelled():
    for key in ("one_liner", "problem_list", "handoff"):
        assert "Use ONLY facts" in SUMMARY_PRESETS[key]
        assert key in PRESET_LABELS
        assert build_prompt(key, "", "chart").endswith("Chart:\nchart")
    assert set(SUMMARY_PRESETS) | {"custom"} == set(PRESET_LABELS)
