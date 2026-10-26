"""Speed and connection stability on long charts (2026-10-25).

A 165 KB multi-note export used to freeze the event loop for ~20 s (thousands
of Review buttons) and drop the websocket; these guard the fixes: opaque
stages fold into local line hunks (so later edits don't map to the whole
chart and spray false "statement lost" findings), the statement set is
patched in one pass, the Review list is paged, long charts build Review and
Insights on first open, busy state never re-sends the chart textarea, a
settled chart is cleaned speculatively, and the socket survives busy moments.
"""

from __future__ import annotations

import asyncio
import random

import pytest
from nicegui.testing import User

import app as cc_app
from app_pages import clean as clean_mod
from app_pages import review_workspace
from app_pages.review_model import FindingRow
from chartcleaner import custom_rules, validation
from chartcleaner.appstate import CLEAN_STATE
from chartcleaner.edit_provenance import EditLedger, SourceSpan, _line_hunks
from chartcleaner.engine import Pipeline
from tests.test_phase8_ui import _route, page  # noqa: F401


# --- provenance: opaque stages fold into local hunks ----------------------------

def _rebuild(old: str, new: str, hunks) -> str:
    out, pos = [], 0
    for cs, ce, as_, ae in hunks:
        assert cs >= pos, "hunks are ordered and never overlap"
        out += [old[pos:cs], new[as_:ae]]
        pos = ce
    return "".join(out) + old[pos:]


def test_line_hunks_rebuild_the_new_text_exactly():
    rnd = random.Random(7)
    for _ in range(2000):
        a = [rnd.choice(["x\n", "y\n", "\n", "z w\n", "q"]) for _ in range(rnd.randint(0, 30))]
        b = list(a)
        for _ in range(rnd.randint(0, 5)):
            at = rnd.randint(0, len(b))
            if rnd.random() < 0.5:
                b.insert(at, rnd.choice(["x\n", "new\n", "\n"]))
            elif b:
                b.pop(min(at, len(b) - 1))
        old, new = "".join(a), "".join(b)
        assert _rebuild(old, new, _line_hunks(old, new)) == new


def test_reconcile_hunks_keep_untouched_lines_exact():
    body = "".join(f"Line {i}: aspirin 81 mg daily\n" for i in range(200))
    source = "Name: Jane Doe\n" + body + "Signed: Dr Smith\n"
    after = "Name: [NAME]\n" + body + "Signed: [NAME]\n"
    ledger = EditLedger(source)
    proposals = ledger.reconcile_hunks("nlp_redaction", after)
    assert len(proposals) == 2 and all(p.mapping == "coarse" for p in proposals)
    ledger.resolve_many([(p, "accept") for p in proposals])
    assert ledger.current == after
    # A later edit in the middle maps exactly to its own source characters,
    # not to everything between the first and last redaction.
    at = ledger.current.index("Line 100")
    p = ledger.propose("literal_replacements", None, at, at + 8, "L100")
    assert p.mapping == "exact"
    assert p.source_spans == (SourceSpan(source.index("Line 100"), source.index("Line 100") + 8),)


def test_reconcile_hunks_match_reconcile_for_one_region():
    source = "Labs:\nSodium 141 mmol/L\nPotassium 4.0 mmol/L\nPlan: PT\n"
    after = "Labs:\nNa 141, K 4.0\nPlan: PT\n"
    one = EditLedger(source).reconcile("lab_compaction", after, decision=None)
    (hunk,) = EditLedger(source).reconcile_hunks("lab_compaction", after)
    assert (hunk.before, hunk.after, hunk.source_spans) == (one.before, one.after, one.source_spans)
    assert EditLedger(after).reconcile_hunks("whitespace", after) == []


def test_opaque_stage_at_both_ends_does_not_flag_middle_replacements(tmp_path):
    # A custom script (opaque) touches the first and last line; a literal
    # replacement in the middle must be judged on its own line — it used to
    # map to the whole chart and report every statement as lost.
    script = tmp_path / "touch_ends.py"
    script.write_text('LABEL = "Touch ends"\nDESCRIPTION = "x"\nPLACEHOLDER = False\n'
                      'def clean(text, ctx):\n'
                      '    lines = text.split("\\n")\n'
                      '    lines[0] = lines[0].upper()\n'
                      '    lines[-2] = lines[-2].upper()\n'
                      '    return "\\n".join(lines)\n', encoding="utf-8")
    custom_rules.trust_rule(script)
    notes = "".join(f"Day {i}: history of hypertension. Na 141 mmol/L. Plan: aspirin 81 mg.\n"
                    for i in range(30))
    chart = "Progress note\n" + notes + "signed\n"
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [["hypertension", "HTN"]], "clinical_headers": [],
           "nlp_redaction": {"enabled": False}, "wrap_output": False,
           "duplicate_note_detection": {"enabled": False}, "fuzzy_dedup": {"enabled": False},
           "stage_order": ["custom:touch_ends", "literal_replacements"],
           "cleaning_validation": {"mode": "shadow", "schema_version": 1}}
    result = Pipeline(cfg, custom_dir=tmp_path).run(chart)
    assert "HTN" in result.text and "PROGRESS NOTE" in result.text
    lits = [p for p in result.ledger.proposals if p.stage_id == "literal_replacements"]
    assert lits and all(p.enclosing.length < 200 for p in lits)
    assert not [i for i in result.validation.issues
                if i.stage_id == "literal_replacements" and i.code == "statement_lost"]


def test_statement_set_apply_many_matches_sequential_apply():
    source = "".join(f"Day {i}: Na {130 + i} mmol/L, K 4.{i} mmol/L. No fever.\n" for i in range(10))
    ledger = EditLedger(source)
    edits = []
    for i in (1, 4, 7):
        at = source.index(f"Day {i}:")
        edits.append(ledger.propose("s", None, at, at + len(f"Day {i}:"), f"D{i} Na 150 mmol/L"))
    batch, seq = validation.StatementSet(source), validation.StatementSet(source)
    batch.apply_many(edits)
    for p in edits:
        seq.apply(p)
    key = [(s.kind, s.source_spans) for s in seq.statements]
    assert [(s.kind, s.source_spans) for s in batch.statements] == key
    assert batch.version == seq.version


# --- UI: paged findings, lazy tabs, busy state, speculation -----------------------

def _rows(n: int, occurrences: int) -> list[FindingRow]:
    occ = tuple({"before": f"s{i}", "after": ""} for i in range(occurrences))
    return [FindingRow("statement_lost", "review", f"finding {i}", "detail", "", None, occ)
            for i in range(n)]


async def test_findings_are_paged_and_locate_buttons_capped(user: User, page, monkeypatch):
    rows = _rows(review_workspace.ROW_PAGE + 5, 50)
    monkeypatch.setattr(review_workspace, "build_finding_rows", lambda *a, **kw: rows)

    def fixture_page():
        from types import SimpleNamespace as NS
        review_workspace.render_review_workspace(
            result=NS(), audit=None, source="source", output="output",
            decision=NS(status="ready"), sections=review_workspace.ReviewSections(
                diff=lambda: None, removed=lambda: None, abbreviations=lambda: None,
                diagnostics=lambda: None), locate=lambda _item: None)

    await _route(user, "/resp-findings", fixture_page)
    user.find(marker="review-findings-tab").click()
    await user.should_see(marker="findings-more")
    assert len(user.find(marker="finding-row").elements) == review_workspace.ROW_PAGE
    assert len(user.find(marker="locate-finding").elements) == \
        review_workspace.ROW_PAGE * review_workspace.LOCATE_LIMIT
    await user.should_see("+45 more")
    user.find(marker="findings-more").click()
    await user.should_not_see(marker="findings-more")
    assert len(user.find(marker="finding-row").elements) == len(rows)


async def _clean(user: User, route: str, text: str) -> None:
    CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, route, cc_app.clean_page)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("clean did not finish")


async def test_long_chart_builds_review_on_first_open(user: User, page, monkeypatch):
    monkeypatch.setattr(clean_mod, "LAZY_TABS_MIN_CHARS", 0)
    calls = []
    real = review_workspace.render_review_workspace
    monkeypatch.setattr(review_workspace, "render_review_workspace",
                        lambda **kw: (calls.append(1), real(**kw)))
    await _clean(user, "/resp-lazy", "Printed by Dr. Lee\nPlan: recheck K 4.1 tomorrow\n")
    await user.should_see(marker="result-output")
    assert calls == [], "Review is not built until it is opened"
    user.find(marker="review-tab").click()
    await user.should_see(marker="review-findings-tab")
    assert calls == [1]


async def test_busy_state_never_touches_the_chart_textarea(user: User, page):
    await _route(user, "/resp-busy", cc_app.clean_page)
    textarea = next(iter(user.find(marker="highlight-source").elements))
    shell = next(iter(user.find(marker="input-shell").elements))
    sent = []
    textarea.update = lambda: sent.append(1)  # every update re-sends the whole chart
    CLEAN_STATE.update(input="Plan: recheck K 4.1\n")
    textarea.set_value("Plan: recheck K 4.1\n")
    sent.clear()
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    assert sent == []
    assert "inert" not in shell.props


async def test_settled_chart_is_cleaned_speculatively(user: User, page, monkeypatch):
    monkeypatch.setattr(clean_mod, "SPECULATE", True)
    monkeypatch.setattr(clean_mod, "SPECULATE_PAUSE", 0.0)
    monkeypatch.setattr(clean_mod, "SPECULATE_MIN_CHARS", 1)
    monkeypatch.setitem(clean_mod.SPECULATION_STATS, "hits", 0)
    monkeypatch.setattr(clean_mod, "SPECULATION", {"task": None, "inputs": None})
    runs = []
    real_run = Pipeline.run
    monkeypatch.setattr(Pipeline, "run", lambda self, *a, **kw: (runs.append(1), real_run(self, *a, **kw))[1])
    text = "Printed by Dr. Lee\nPlan: recheck K 4.1 tomorrow\n"
    # An earlier chart's result is on screen (the editor mirrors into
    # CLEAN_STATE["input"] while typing — that must not count as "cleaned").
    await _clean(user, "/resp-spec", "Plan: earlier chart\n")
    previous = CLEAN_STATE["result"]
    runs.clear()
    textarea = next(iter(user.find(marker="highlight-source").elements))
    textarea.set_value(text)
    for _ in range(300):  # the 0.5 s timer starts the speculative run
        if runs:
            break
        await asyncio.sleep(0.02)
    assert runs == [1], "a settled chart is cleaned before Clean is pressed"
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not previous:
            break
        await asyncio.sleep(0.02)
    assert CLEAN_STATE["input"] == text
    assert clean_mod.SPECULATION_STATS["hits"] == 1
    assert runs == [1], "Clean reused the speculative result instead of cleaning again"
    assert "K 4.1" in CLEAN_STATE["result_text"]


async def test_speculation_is_discarded_when_config_changes(user: User, page, monkeypatch):
    monkeypatch.setattr(clean_mod, "SPECULATE", True)
    monkeypatch.setattr(clean_mod, "SPECULATE_PAUSE", 0.0)
    monkeypatch.setattr(clean_mod, "SPECULATE_MIN_CHARS", 1)
    monkeypatch.setitem(clean_mod.SPECULATION_STATS, "hits", 0)
    monkeypatch.setattr(clean_mod, "SPECULATION", {"task": None, "inputs": None})
    runs = []
    real_run = Pipeline.run
    monkeypatch.setattr(Pipeline, "run", lambda self, *a, **kw: (runs.append(1), real_run(self, *a, **kw))[1])
    CLEAN_STATE.update(input="", mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, "/resp-spec-stale", cc_app.clean_page)
    user.find(marker="highlight-source").type("Printed by Dr. Lee\nPlan: recheck\n")
    for _ in range(300):
        if runs:
            break
        await asyncio.sleep(0.02)
    from app_pages import common
    from chartcleaner.engine import load_config, save_config
    cfg = load_config(common.CONFIG_PATH)
    cfg["literal_replacements"] = [["recheck", "RECHECK"]]
    save_config(cfg, common.CONFIG_PATH)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    assert clean_mod.SPECULATION_STATS["hits"] == 0
    assert "RECHECK" in CLEAN_STATE["result_text"]


def test_pipeline_stop_abandons_the_run_between_stages():
    from chartcleaner.engine import PipelineCancelled, load_default_config

    seen = []

    def stop() -> bool:
        seen.append(1)
        return len(seen) >= 2

    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    with pytest.raises(PipelineCancelled):
        Pipeline(cfg).run("Printed by Dr. Lee\nPlan: recheck\n", stop=stop)
    assert len(seen) == 2, "polled once per finished stage, stopped at the second"


async def test_newer_text_cancels_a_stale_speculative_run(user: User, page, monkeypatch):
    monkeypatch.setattr(clean_mod, "SPECULATE", True)
    monkeypatch.setattr(clean_mod, "SPECULATE_PAUSE", 0.0)
    monkeypatch.setattr(clean_mod, "SPECULATE_MIN_CHARS", 1)
    monkeypatch.setitem(clean_mod.SPECULATION_STATS, "hits", 0)
    monkeypatch.setattr(clean_mod, "SPECULATION", {"task": None, "inputs": None})
    ran = []
    real_run = Pipeline.run
    monkeypatch.setattr(Pipeline, "run",
                        lambda self, text, *a, **kw: (ran.append(text), real_run(self, text, *a, **kw))[1])
    CLEAN_STATE.update(input="", mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, "/resp-spec-cancel", cc_app.clean_page)
    textarea = next(iter(user.find(marker="highlight-source").elements))
    first, second = "Plan: first chart\n", "Plan: second chart K 4.1\n"
    # Hold the pipeline lock so the first speculative run is still waiting
    # when newer text arrives: it must give up rather than clean stale text.
    clean_mod.COMPUTE_LOCK.acquire()
    try:
        textarea.set_value(first)
        await asyncio.sleep(0.8)
        textarea.set_value(second)
        await asyncio.sleep(0.8)
    finally:
        clean_mod.COMPUTE_LOCK.release()
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    assert "K 4.1" in CLEAN_STATE["result_text"]
    assert first not in ran, "the stale speculative run was abandoned"
    assert ran == [second] and clean_mod.SPECULATION_STATS["hits"] == 1


async def test_clean_joins_an_in_flight_speculative_run(user: User, page, monkeypatch):
    monkeypatch.setattr(clean_mod, "SPECULATE", True)
    monkeypatch.setattr(clean_mod, "SPECULATE_PAUSE", 0.0)
    monkeypatch.setattr(clean_mod, "SPECULATE_MIN_CHARS", 1)
    monkeypatch.setitem(clean_mod.SPECULATION_STATS, "hits", 0)
    monkeypatch.setattr(clean_mod, "SPECULATION", {"task": None, "inputs": None})
    gate = __import__("threading").Event()
    ran = []
    real_run = Pipeline.run

    def run(self, text, *a, **kw):
        ran.append(text)
        gate.wait(5)  # the speculative run is still going when Clean is pressed
        return real_run(self, text, *a, **kw)

    monkeypatch.setattr(Pipeline, "run", run)
    CLEAN_STATE.update(input="", mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, "/resp-spec-join", cc_app.clean_page)
    text = "Plan: recheck K 4.1 tomorrow\n"
    next(iter(user.find(marker="highlight-source").elements)).set_value(text)
    for _ in range(300):
        if ran:
            break
        await asyncio.sleep(0.02)
    user.find(marker="run-clean").click()
    await asyncio.sleep(0.3)
    gate.set()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    assert ran == [text], "Clean joined the running speculation instead of starting over"
    assert clean_mod.SPECULATION_STATS["hits"] == 1
    assert "K 4.1" in CLEAN_STATE["result_text"]


async def test_idle_page_does_not_reclaim_speculation(user: User, page, monkeypatch):
    # Two tabs (or a closed page inside its reconnect window): the idle one
    # must not keep re-speculating its own text and cancelling the other's.
    monkeypatch.setattr(clean_mod, "SPECULATE", True)
    monkeypatch.setattr(clean_mod, "SPECULATE_PAUSE", 0.0)
    monkeypatch.setattr(clean_mod, "SPECULATE_MIN_CHARS", 1)
    monkeypatch.setattr(clean_mod, "SPECULATION", {"task": None, "inputs": None})
    ran = []
    real_run = Pipeline.run
    monkeypatch.setattr(Pipeline, "run",
                        lambda self, text, *a, **kw: (ran.append(text), real_run(self, text, *a, **kw))[1])
    CLEAN_STATE.update(input="", mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="")
    await _route(user, "/resp-spec-idle", cc_app.clean_page)
    next(iter(user.find(marker="highlight-source").elements)).set_value("Plan: recheck K 4.1\n")
    await asyncio.sleep(1.6)  # three timer ticks
    assert ran == ["Plan: recheck K 4.1\n"], "speculated once for the edit, not every tick"
    clean_mod.SPECULATION["inputs"] = ("another tab's chart", "clean", "", None)
    await asyncio.sleep(1.1)
    assert clean_mod.SPECULATION["inputs"][0] == "another tab's chart"
    assert len(ran) == 1


# --- connection and network --------------------------------------------------------

def test_socket_heartbeat_tolerates_busy_moments():
    # NiceGUI pings every 0.8x and times out at 0.4x reconnect_timeout; at the
    # 3 s default a few busy seconds dropped the page and rebuilt it.
    assert cc_app.RECONNECT_TIMEOUT >= 20


def test_email_recognizer_never_downloads_the_suffix_list():
    tldextract = pytest.importorskip("tldextract")
    from chartcleaner import stage_nlp

    stage_nlp._offline_tldextract()
    assert tldextract.extract.suffix_list_urls == ()
    assert tldextract.extract("jane@example.co.uk").suffix == "co.uk"
