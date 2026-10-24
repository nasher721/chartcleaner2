"""Speed-ups that must not change results: the lean spaCy pipeline, the NLP
warm-up key, line-by-line fact counting, incremental history reads and the
Review diff built off the event loop."""

from __future__ import annotations

import asyncio
from importlib.util import find_spec
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import clean as clean_page_mod
from app_pages import common
from chartcleaner import benchmark, doctor, fact_check, stage_nlp, store, store_history
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
from chartcleaner.engine import CleanContext, load_default_config, save_config

ROOT = Path(__file__).resolve().parent.parent

needs_nlp = pytest.mark.skipif(
    find_spec("presidio_analyzer") is None or find_spec("en_core_web_sm") is None,
    reason="Presidio / en_core_web_sm not installed")

NLP_TEXT = ("Called patient's daughter Maria Lopez at 555-867-5309 re: discharge.\n"
            "Cell: (617) 555-0199. Email jdoe@example.org. Seen by Dr. Wei Chen.\n"
            "Na 141, K 3.9. Nimodipine 60 mg q4h. Phone number 212 555 0123.\n")


# --- NLP: lean pipeline ------------------------------------------------------

@needs_nlp
def test_lean_pipeline_redacts_exactly_like_the_full_one(monkeypatch):
    cfg = load_default_config()
    corpus = [NLP_TEXT, (ROOT / "sample_chart.txt").read_text(encoding="utf-8")]
    corpus += [d["text"] for d in benchmark.generate(n=6, seed=7)]

    def run(threshold):
        cache = stage_nlp.PresidioEngineCache()
        monkeypatch.setattr(stage_nlp, "_PRESIDIO_CACHE", cache)
        c = {**cfg, "nlp_redaction": {**(cfg.get("nlp_redaction") or {}), "score_threshold": threshold}}
        out = [stage_nlp.run_nlp(t, c, CleanContext(c)) for t in corpus]
        return out, cache._analyzer.nlp_engine.nlp["en"].pipe_names

    # A 0.0 threshold keeps every result, so it redacts what no threshold does —
    # but it keeps the full tagger/lemmatizer pipeline.
    full, full_pipes = run(0.0)
    lean, lean_pipes = run(None)
    assert "tagger" in full_pipes and "lemmatizer" in full_pipes
    assert lean_pipes == ["ner", stage_nlp._LOWER_LEMMA]
    assert lean == full
    assert "Maria Lopez" not in lean[0][0] and "555-867-5309" not in lean[0][0]


# --- NLP: warm-up and Doctor use the clean's engine key ------------------------

class _RecordingCache:
    def __init__(self):
        self.keys = []

    def get_engines(self, *key):
        self.keys.append(key)
        return object(), object()


def test_warm_and_doctor_build_the_engines_a_clean_uses(monkeypatch):
    cfg = {"nlp_redaction": {"phone_regions": ["US"], "score_threshold": 0.5,
                             "entities": {"PERSON": "[N]"}},
           "nlp_allow_list": ["Foley"]}
    cache = _RecordingCache()
    monkeypatch.setattr(stage_nlp, "_PRESIDIO_CACHE", cache)
    monkeypatch.setattr(doctor, "find_spec", lambda name: object())
    stage_nlp._engines(cfg)
    stage_nlp.warm(cfg, analyze=False)
    doctor._spacy_model(True, cfg)
    assert len(cache.keys) == 3 and len(set(cache.keys)) == 1
    assert cache.keys[0][3] == ("US",)  # phone regions are part of the key


# --- fact check: facts are line-local and counted per line ---------------------

def test_line_by_line_counts_equal_whole_text_counts():
    counter = fact_check._LineFactCounts()
    texts = [(ROOT / "sample_chart.txt").read_text(encoding="utf-8")]
    texts += [d["text"] for d in benchmark.generate(n=10, seed=3)]
    texts.append("Na 141\nNa 141\nfull code\nBP 120/80 HR 88\n\n40 mg\n")
    for text in texts:
        assert counter(text) == fact_check.fact_counts(text)


def test_a_fact_never_spans_two_lines():
    assert fact_check.fact_counts("Temp 37\nC/O headache") == fact_check.fact_counts("Temp 37")
    assert not fact_check.fact_counts("pending full\ncode review")
    assert fact_check.fact_counts("Temp 37 C") and fact_check.fact_counts("full code")


def test_tracker_rescans_only_changed_lines(monkeypatch):
    text = "\n".join(f"Na {130 + i} mmol/L" for i in range(50))
    tracker = fact_check.FactTracker(text)
    scanned = []
    real = fact_check.fact_counts
    monkeypatch.setattr(fact_check, "fact_counts", lambda t: scanned.append(t) or real(t))
    tracker.after_stage("whitespace", "Whitespace", text.replace("Na 130 mmol/L", "Na 130"))
    scanned.clear()
    tracker.after_stage("boilerplate", "Boilerplate", text.replace("Na 131 mmol/L", ""))
    # Only the two lines stage 1's output didn't have are scanned; the other 48 are looked up.
    assert sorted(scanned) == ["", "Na 130 mmol/L"]
    assert tracker.current == fact_check.fact_counts(text.replace("Na 131 mmol/L", ""))


# --- history: only appended lines are parsed -----------------------------------

def test_history_reads_only_what_was_appended(monkeypatch):
    for i in range(20):
        store.append_run({"ts": "t", "source": "editor", "i": i})
    assert [r["i"] for r in store.load_runs()] == list(range(20))
    parsed = []
    real = store_history._parse_runs
    monkeypatch.setattr(store_history, "_parse_runs", lambda data: parsed.append(data) or real(data))
    store.append_run({"ts": "t", "source": "editor", "i": 20})
    runs = store.load_runs()
    assert runs[-1]["i"] == 20 and len(runs) == 21
    assert b"".join(parsed).count(b"\n") == 1  # one new line, not the whole file

    runs[0]["source"] = "mutated by a caller"
    assert store.load_runs()[0]["source"] == "editor"


def test_history_cache_notices_rewrites_partial_lines_and_clear():
    for i in range(5):
        store.append_run({"ts": "t", "source": "s", "i": i})
    store.load_runs()
    text = store.STATS_FILE.read_text(encoding="utf-8")
    store.STATS_FILE.write_text(text.replace('"i": 0}', '"i": 7}'), encoding="utf-8")
    assert store.load_runs()[0]["i"] == 7
    with store.STATS_FILE.open("a", encoding="utf-8") as f:
        f.write('{"ts": "t", "source": "half"')
    assert "corrupt" in store.load_runs()[-1]["source"]
    with store.STATS_FILE.open("a", encoding="utf-8") as f:
        f.write(', "i": 5}\n')
    assert store.load_runs()[-1]["source"] == "half"
    assert store.clear_runs() == 6 and store.load_runs() == []
    store.append_run({"ts": "t", "source": "after"})
    assert [r["source"] for r in store.load_runs()] == ["after"]


# --- Clean page: the Review diff is built once, in the worker ------------------

pytestmark_ui = pytest.mark.nicegui_main_file("")


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
    monkeypatch.setattr(cc_app.LocalLlmClient, "is_available", lambda self: False)
    monkeypatch.setattr(cc_app.LocalLlmClient, "list_models", lambda self: [])
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    yield path
    CLEAN_STATE.clear()
    CLEAN_STATE.update(before)
    AUTO_LAST.clear()
    AUTO_LAST.update(auto_before)


@pytestmark_ui
async def test_review_diff_is_built_once_off_the_event_loop(user: User, page, monkeypatch):
    calls = []
    real = clean_page_mod.review_diff_html
    monkeypatch.setattr(clean_page_mod, "review_diff_html",
                        lambda *a: calls.append(a) or real(*a))
    CLEAN_STATE.update(input="Printed by Dr. Lee\nNa 141\nPlan: recheck\n", mode="clean",
                       result=None, result_text="", audit=None, summary=None, qa=[])

    @ui.page("/perf-review")
    @ui.page("/perf-review-reload")
    async def fixture_page():
        await cc_app.clean_page()

    await user.open("/perf-review")
    user.find(marker="run-clean").click()
    for _ in range(200):
        if CLEAN_STATE.get("result") is not None and CLEAN_STATE.get("review_html"):
            break
        await asyncio.sleep(0.02)
    await user.should_see(marker="review-diff")
    assert len(calls) == 1
    cached = CLEAN_STATE["review_html"]
    assert cached[0] is CLEAN_STATE["result"]
    assert cached[4] == cached[3].count("class='cc-diff-change'")

    await user.open("/perf-review-reload")
    await user.should_see(marker="review-diff")
    assert CLEAN_STATE["review_html"] is cached
    assert len(calls) == 1
