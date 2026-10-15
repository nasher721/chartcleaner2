"""Known fixes for shipped regex rules, and the content they protect."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

import app as cc_app
from app_pages import common
from chartcleaner import pattern_fixes, store
from chartcleaner.engine import (
    clean_text,
    load_config,
    load_default_config,
    save_config,
    validate_config,
)

ROOT = Path(__file__).resolve().parent.parent


def _old_config() -> dict:
    """The default config as it shipped before the fixes."""
    cfg = load_default_config()
    for fix in pattern_fixes.known_fixes():
        entries = cfg[fix["key"]]
        if fix["new"] is None:
            entries.append(fix["old"])
            continue
        for i, entry in enumerate(entries):
            if (entry[0] if isinstance(entry, list) else entry) == fix["new"]:
                entries[i] = [fix["old"], entry[1]] if isinstance(entry, list) else fix["old"]
    return cfg


@pytest.mark.parametrize("path", [ROOT / "config.json", None])
def test_shipped_configs_need_no_fixes(path):
    cfg = load_config(path) if path else load_default_config()
    assert pattern_fixes.pending(cfg) == []


def test_fixes_upgrade_an_old_config_to_the_shipped_rules():
    old = _old_config()
    assert pattern_fixes.pending(old)
    fixed, n = pattern_fixes.apply(old)
    assert n == len(pattern_fixes.known_fixes())
    assert validate_config(fixed)[0] == []
    new = load_default_config()
    for key in ("emr_line_metadata", "boilerplate", "epic_phi_patterns"):
        assert fixed[key] == new[key]
    assert pattern_fixes.apply(fixed) == (fixed, 0)  # idempotent


def test_fixes_leave_edited_rules_and_replacements_alone():
    old = _old_config()
    phi = next(f for f in pattern_fixes.known_fixes() if f["key"] == "epic_phi_patterns")
    index = next(i for i, e in enumerate(old["epic_phi_patterns"]) if e[0] == phi["old"])
    old["epic_phi_patterns"][index][1] = "[MY_LABEL]"
    edited = old["emr_line_metadata"][0] + "  "  # a user's own variant
    old["emr_line_metadata"][0] = edited
    snapshot = copy.deepcopy(old)

    fixed, _ = pattern_fixes.apply(old)

    assert old == snapshot  # the input isn't mutated
    assert fixed["epic_phi_patterns"][index] == [phi["new"], "[MY_LABEL]"]
    assert fixed["emr_line_metadata"][0] == edited


def test_frozen_migration_applies_the_fixes():
    migrated = store._migrate_config_schema(_old_config())
    assert pattern_fixes.pending(migrated) == []


def _clean(text: str) -> str:
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    return clean_text(text, cfg, wrap=False, fact_check=False).text


@pytest.mark.parametrize("line,kept", [
    ("CXR [normal]", "CXR [normal]"),
    ("UA nitrite [negative]", "UA nitrite [neg]"),  # the abbreviation stage shortens it
    ("Blood cx [pending]", "Blood cx [pending]"),
    ("K [CRITICAL]", "K [CRITICAL]"),
    ("Eye drops (OD, OS) bid", "Eye drops (OD, OS) bid"),
    ("Meds (PO, IV) as tolerated", "Meds (PO, IV) as tolerated"),
    ("vitals.hr[1]", "vitals.hr[1]"),
])
def test_clinical_content_is_kept(line, kept):
    assert kept in _clean(f"Results\n{line}\nPlan")


@pytest.mark.parametrize("chrome", ["[SQLAB123]", "[ZZDEPTXYZ]", "(CMS/HCC)", "(HCC)", "(RN, MD)",
                                    "@LASTLABS@", ".LASTLABS[24"])
def test_epic_chrome_is_still_removed(chrome):
    assert chrome not in _clean(f"Hypertension {chrome} stable\nPlan")


def test_removing_a_metadata_line_keeps_the_paragraph_break_above_it():
    assert _clean("Plan per protocol.\n\nCosign Required: Yes\nNext para") == \
        "Plan per protocol.\n\nNext para"


def test_partial_editor_line_leaves_no_blank_line():
    assert _clean("Status: ok\nEditor: Fake, A. MD (09/15/2026 0745)\nNext") == "Status: ok\nNext"


@pytest.mark.nicegui_main_file("")
async def test_rules_page_offers_and_applies_the_fixes(user, monkeypatch, tmp_path):
    from nicegui import ui

    path = tmp_path / "config.json"
    save_config(_old_config(), path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)

    @ui.page("/rules")
    def fixture_page():
        cc_app.text_rules_page()

    await user.open("/rules")
    await user.should_see(marker="pattern-fixes")
    user.find(marker="apply-pattern-fixes").click()
    await user.should_see("A backup was saved first.")
    assert pattern_fixes.pending(load_config(path)) == []
    await user.should_not_see(marker="pattern-fixes")
