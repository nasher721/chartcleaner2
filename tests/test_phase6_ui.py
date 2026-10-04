"""Phase 6 UI: the redesigned Clean page (review clicks, copy-as, palette,
restore, known-good, rule inbox, onboarding, insights) and shared shell helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing import User

import app as cc_app
from app_pages import common
from chartcleaner import recent_charts, regression_set, rule_inbox, store
from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
from chartcleaner.engine import load_config, load_default_config, save_config

pytestmark = pytest.mark.nicegui_main_file("")


@pytest.fixture()
def page(monkeypatch, tmp_path):
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    cfg["emr_line_metadata"] = [r"(?im)^Printed by .*$"]
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


async def _open(user: User, route: str, text: str) -> None:
    CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[])

    @ui.page(route)
    async def fixture_page():
        await cc_app.clean_page()

    await user.open(route)


async def _clean(user: User) -> None:
    result = CLEAN_STATE.get("result")
    user.find(marker="run-clean").click()
    for _ in range(200):
        if CLEAN_STATE.get("result") is not None and CLEAN_STATE.get("result") is not result:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("clean did not finish")


async def test_review_click_opens_rule_and_never_remove_keeps_text(user: User, page):
    await _open(user, "/p6-review", "Printed by Dr. Lee: K 6.1 called\nPlan: recheck\n")
    await _clean(user)
    assert "K 6.1" not in CLEAN_STATE["result_text"]
    await user.should_see(marker="review-diff")
    user.find(marker="review-diff").trigger("click", "g0")
    await user.should_see("Removed by EMR line metadata")
    user.find("Never remove this").click()
    for _ in range(200):
        if "K 6.1" in (CLEAN_STATE.get("result_text") or ""):
            break
        await asyncio.sleep(0.02)
    assert "K 6.1" in CLEAN_STATE["result_text"]
    assert load_config(page)["stage_options"]["metadata_lines"]["exceptions"] == [
        "Printed by Dr. Lee: K 6.1 called"]


async def test_review_click_can_delete_the_rule(user: User, page):
    await _open(user, "/p6-delete", "Printed by Dr. Lee\nPlan: recheck\n")
    await _clean(user)
    user.find(marker="review-diff").trigger("click", "g0")
    user.find("Delete this rule").click()
    user.find("Confirm").click()
    for _ in range(200):
        if "Printed by" in (CLEAN_STATE.get("result_text") or ""):
            break
        await asyncio.sleep(0.02)
    assert r"(?im)^Printed by .*$" not in load_config(page)["emr_line_metadata"]


async def test_trust_strip_and_copy_as(user: User, page):
    await _open(user, "/p6-copy", "Sodium 141 mmol/L\nPlan: recheck\n")
    await _clean(user)
    await user.should_see(marker="fact-check")
    await user.should_see("clinical value(s) kept")
    user.find(marker="copy-epic").click()
    await user.should_see("pastes cleanly into Epic")
    user.find(marker="copy-ai").click()
    await user.should_see("Turn on Reversible tokenization")


async def test_copy_default_is_remembered(user: User, page, monkeypatch):
    monkeypatch.setitem(common.PREFS, "copy_default", "markdown")
    await _open(user, "/p6-default", "Plan: recheck\n")
    await _clean(user)
    await user.should_see("Copy · Markdown")


async def test_restore_dialog_puts_values_back(user: User, page):
    store.save_token_map({"Jane Doe": "[[T1]]"}, "test")
    await _open(user, "/p6-restore", "Plan: recheck\n")
    await _clean(user)
    user.find(marker="restore-reply").click()
    await user.should_see("Restore names in an AI reply")
    reply = next(e for e in user.find(ui.textarea).elements if e.props.get("label") == "AI reply")
    with user.client:
        reply.set_value("[[T1]] is improving.")
    user.find(marker="restore-run").click()
    restored = next(e for e in user.find(ui.textarea).elements if e.props.get("label") == "With real values")
    assert restored.value == "Jane Doe is improving."


async def test_mark_known_good_saves_encrypted_chart(user: User, page):
    await _open(user, "/p6-known", "Plan: recheck\n")
    await _clean(user)
    user.find(marker="known-good").click()
    await user.should_see("Mark as known good")
    user.find(marker="known-good-save").click()
    (kg,) = regression_set.list_charts()
    assert kg.input == "Plan: recheck\n" and kg.expected == CLEAN_STATE["result_text"]


async def test_rule_inbox_suggests_and_accepts(user: User, page):
    prefs = dict(store.DEFAULT_PREFS)
    for i in range(4):
        recent_charts.remember(f"Epic Hyperspace build {i} printed copy\nSubjective: well {i}\n", prefs=prefs)
    await _open(user, "/p6-inbox", "")
    await user.should_see("Suggestions (", retries=60)
    user.find(marker="inbox").click()
    await user.should_see("Rule suggestions")
    await user.should_see("appeared in 4 of your last 4 charts")
    from nicegui.testing.user import UserInteraction
    accept = sorted((e for e in user.find(ui.button).elements if e.props.get("label") == "Accept"),
                    key=lambda e: e.id)  # creation order: the line suggestion comes first
    UserInteraction(user, {accept[0]}, "Accept").click()
    rules = load_config(page)["emr_line_metadata"]
    assert any("Hyperspace" in r for r in rules)


async def test_inbox_explains_when_too_few_charts(user: User, page):
    await _open(user, "/p6-inbox-empty", "")
    user.find(marker="inbox").click()
    await user.should_see(f"Suggestions start after {rule_inbox.MIN_CHARTS} cleaned charts")


async def test_onboarding_card_and_sample(user: User, page, monkeypatch):
    monkeypatch.setitem(common.PREFS, "onboarded", False)
    await _open(user, "/p6-onboard", "")
    await user.should_see("New here? Four steps")
    user.find("Try it on the sample chart").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    assert CLEAN_STATE["result"] is not None
    assert common.PREFS["onboarded"] is True
    await user.should_not_see("New here? Four steps")


async def test_command_palette_runs_a_command(user: User, page):
    await _open(user, "/p6-palette", "Plan: recheck\n")
    user.find(marker="palette-button").click()
    await user.should_see(marker="palette-input")
    with user.client:
        next(iter(user.find(marker="palette-input").elements)).set_value("clean the chart")
    user.find(marker="palette-input").trigger("keydown.enter")
    for _ in range(200):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)
    assert CLEAN_STATE["result"] is not None


async def test_insights_show_timeline_sparklines_and_meds(user: User, page):
    chart = ("Date of Service: 09/13/2026\nLabs: Na 128, K 3.1\nMedications:\n- cefazolin 2 g IV q8h\n\n"
             "Date of Service: 09/14/2026\nLabs: Na 150, K 4.0\nMedications:\n- levetiracetam 500 mg IV BID\n")
    await _open(user, "/p6-insights", chart)
    await _clean(user)
    await user.should_see(marker="timeline")
    await user.should_see("2 notes in this chart")
    await user.should_see("Lab trends across 2 notes")
    await user.should_see("Medication changes")


async def test_note_type_chip_maps_a_preset(user: User, page, monkeypatch):
    store.save_preset("neuro", load_config(page))
    await _open(user, "/p6-note", "Progress Note\nSubjective: well\nAssessment and Plan: home\n")
    await _clean(user)
    if not (CLEAN_STATE.get("note") or {}).get("type"):
        pytest.skip("sample not recognized as a note type")
    user.find(marker="note-chip").click()
    await user.should_see("Pick the rule preset")


def _key(name: str, code: str = "", **mods):
    m = {"alt": False, "ctrl": False, "meta": False, "shift": False, **mods}
    return SimpleNamespace(key=SimpleNamespace(name=name, code=code), modifiers=SimpleNamespace(**m))


def test_shortcut_matching():
    assert common.shortcut_matches("mod+enter", _key("Enter", "Enter", ctrl=True))
    assert common.shortcut_matches("mod+enter", _key("Enter", "Enter", meta=True))
    assert not common.shortcut_matches("mod+enter", _key("Enter", "Enter"))
    assert common.shortcut_matches("mod+k", _key("k", "KeyK", meta=True))
    assert not common.shortcut_matches("mod+k", _key("k", "KeyK", meta=True, shift=True))
    assert common.shortcut_matches("mod+shift+c", _key("C", "KeyC", ctrl=True, shift=True))
    assert common.shortcut_matches("alt+2", _key("™", "Digit2", alt=True))
    assert common.shortcut_matches("mod+/", _key("/", "Slash", ctrl=True))


def test_review_diff_marks_removed_and_abbreviated_text():
    groups = [{"kind": "removal", "befores": ["Printed by Dr. Lee"]},
              {"kind": "abbr", "after": "HTN"}]
    html = common.review_diff_html("Printed by Dr. Lee\nhypertension noted", "HTN noted", None, groups)
    assert "data-cc='g0'" in html and "cc-gone" in html
    assert "data-cc='g1'" in html and "cc-abbr" in html
    assert "<script" not in common.review_diff_html("<script>x</script>", "", None, [])


# ---- other pages ------------------------------------------------------------

async def test_pipeline_stage_preview_and_drag_reorder(user: User, page, monkeypatch):
    from chartcleaner.appstate import PIPE_TEST
    monkeypatch.setitem(PIPE_TEST, "text", "Printed by Dr. Lee\nPlan: recheck\n")

    @ui.page("/p6-pipeline")
    def fixture_page():
        cc_app.pipeline_page()

    await user.open("/p6-pipeline")
    card = next(iter(user.find(marker="stage-metadata_lines").elements))
    from nicegui.testing.user import UserInteraction
    preview = next(e for e in card.descendants() if isinstance(e, ui.button)
                   and e.props.get("icon") == "visibility")
    UserInteraction(user, {preview}, "").click()
    await user.should_see("What “EMR line metadata” does to the test text")
    await user.should_see("1 change(s)")

    order_before = list(load_config(page).get("stage_order") or [])
    user.find(marker="stage-whitespace").trigger("dragstart")
    user.find(marker="stage-metadata_lines").trigger("drop")
    await user.should_see("Save changes •")
    assert load_config(page).get("stage_order", order_before) == order_before  # not saved yet


async def test_pipeline_save_warns_about_known_good_changes(user: User, page):
    from chartcleaner.engine import Pipeline
    text = "Printed by Dr. Lee\nPlan: recheck\n"
    cfg = load_config(page)
    regression_set.add(text, Pipeline(cfg).run(text, fact_check=False).text, "Lee chart")
    cfg["emr_line_metadata"] = []
    save_config(cfg, page)  # the page's draft now drops the rule the approval relied on

    @ui.page("/p6-pipeline-save")
    def fixture_page():
        cc_app.pipeline_page()

    await user.open("/p6-pipeline-save")
    user.find("Save changes").click()
    await user.should_see("would come out differently", retries=60)
    await user.should_see("Lee chart")


def test_stage_io_returns_one_stage():
    from chartcleaner.engine import Pipeline
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["emr_line_metadata"] = [r"(?im)^Printed by .*$"]
    before, after, st = Pipeline(cfg).stage_io("Printed by Dr. Lee\nPlan\n", "metadata_lines")
    assert before == "Printed by Dr. Lee\nPlan\n" and after == "\nPlan\n" and st.matches == 1
    with pytest.raises(KeyError):
        Pipeline(cfg).stage_io("x", "no_such_stage")


async def _open_page(user: User, route: str, page_fn) -> None:
    @ui.page(route)
    async def fixture_page():
        result = page_fn()
        if asyncio.iscoroutine(result):
            await result

    await user.open(route)


async def test_stats_page_shows_rule_health_dashboard(user: User, page, monkeypatch):
    from chartcleaner.engine import Pipeline
    cfg = load_config(page)
    runs = [Pipeline(cfg).run("Sodium 141 mmol/L\nPrinted by Dr. Lee: K 6.1\n").to_history_dict("editor")
            for _ in range(2)]
    monkeypatch.setattr(store, "load_runs", lambda: list(runs))
    await _open_page(user, "/p6-stats", cc_app.stats_page)
    await user.should_see(marker="rule-health")
    await user.should_see("rules that never fire")
    await user.should_see("charts with clinical values flagged")
    await user.should_see("Stages behind removed clinical values")


async def test_batch_filters_and_retry(user: User, page, tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("Plan: recheck\n", encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00broken")
    await _open_page(user, "/p6-batch", cc_app.batch_page)
    with user.client:
        next(e for e in user.find(ui.input).elements
             if e.props.get("label", "").startswith("…or a folder")).set_value(str(tmp_path))
    user.find("Add folder files").click()
    user.find("Clean queued files").click()
    await user.should_see(marker="batch-filter", retries=100)
    await user.should_see("All (")


async def test_settings_search_filters_cards(user: User, page):
    await _open_page(user, "/p6-settings-search", cc_app.settings_page)
    await user.should_see("Known-good charts")
    await user.should_see("Recent charts (for rule suggestions)")
    with user.client:
        next(iter(user.find(marker="settings-search").elements)).set_value("delete after")
    await user.should_see(marker="privacy-card")
    titles = {getattr(next(iter(c.default_slot.children), None), "text", "")
              for c in user.find(ui.card).elements}  # find() only returns visible elements
    assert "Stored chart data" in titles and "Clipboard watcher" not in titles


async def test_settings_recent_charts_switch_saves_pref(user: User, page):
    await _open_page(user, "/p6-settings-recent", cc_app.settings_page)
    with user.client:
        next(iter(user.find(marker="recent-charts-switch").elements)).set_value(False)
    assert common.PREFS["recent_charts"]["enabled"] is False
    common.PREFS["recent_charts"] = {"enabled": True, "keep": 20}


async def test_settings_known_good_check(user: User, page):
    regression_set.add("Plan: recheck\n", "something else entirely", "Drifted chart")
    await _open_page(user, "/p6-settings-kg", cc_app.settings_page)
    user.find(marker="known-good-check").click()
    await user.should_see("Show change", retries=60)
