"""'Never remove this' exceptions and the Clean page's Removed tab."""

import asyncio

import pytest
from nicegui.testing import User

from chartcleaner.engine import clean_text, validate_config

from app_pages import common

def base(**over) -> dict:
    cfg = {"emr_line_metadata": [r"^Printed by .*$"], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}}}
    cfg.update(over)
    return cfg


TEXT = "Printed by Epic\nPrinted by Dr. Lee: K 6.1 called to team\nPlan: recheck\n"


def test_exception_keeps_matching_text_and_is_not_counted():
    plain = clean_text(TEXT, base(), wrap=False)
    assert "K 6.1" not in plain.text
    cfg = base()
    cfg["stage_options"]["metadata_lines"] = {"exceptions": ["K 6.1 called"]}
    kept = clean_text(TEXT, cfg, wrap=False, track_changes=True)
    assert "Printed by Dr. Lee: K 6.1 called to team" in kept.text
    assert "Printed by Epic" not in kept.text
    stage = next(s for s in kept.stages if s.id == "metadata_lines")
    assert stage.matches == 1 and len(stage.details["changes"]) == 1


def test_exceptions_are_case_insensitive_and_validated():
    cfg = base()
    cfg["stage_options"]["metadata_lines"] = {"exceptions": ["k 6.1"]}
    assert "K 6.1" in clean_text(TEXT, cfg, wrap=False).text
    cfg["stage_options"]["metadata_lines"] = {"exceptions": "K 6.1"}
    assert any("exceptions" in e for e in validate_config(cfg)[0])


async def test_removed_tab_never_remove_saves_exception(user: User, monkeypatch, tmp_path):
    from chartcleaner import store
    from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
    from chartcleaner.engine import load_config, load_default_config, save_config

    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    cfg["emr_line_metadata"] = [r"^Printed by .*$"]
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(store, "append_run", lambda _r: None)
    monkeypatch.setattr(store, "load_prefs", lambda: dict(store.DEFAULT_PREFS, auto_clean=False))
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    CLEAN_STATE.update(input="Printed by Dr. Lee: K 6.1 called\nPlan: recheck\n", mode="clean",
                       result=None, result_text="", audit=None)
    try:
        await user.open("/")
        user.find(marker="run-clean").click()
        await user.should_see("Removed (1)", retries=50)
        user.find(marker="never-remove").click()
        for _ in range(100):
            if "K 6.1" in CLEAN_STATE["result_text"]:
                break
            await asyncio.sleep(0.05)
        assert "K 6.1" in CLEAN_STATE["result_text"]
        saved = load_config(path)["stage_options"]["metadata_lines"]["exceptions"]
        assert saved == ["Printed by Dr. Lee: K 6.1 called"]
    finally:
        CLEAN_STATE.clear()
        CLEAN_STATE.update(before)
        AUTO_LAST.clear()
        AUTO_LAST.update(auto_before)
