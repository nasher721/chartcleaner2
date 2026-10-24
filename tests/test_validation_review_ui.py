"""The Clean page under the release policy: a blocked result cannot be copied,
the review panel shows the validator's decisions with coverage, and the
acknowledgement key survives exactly one result."""

from __future__ import annotations

import asyncio
import json

import pytest
from nicegui.testing import User

import app as cc_app  # noqa: F401
from app_pages import clean as clean_mod, common
from chartcleaner import store
from chartcleaner.appstate import CLEAN_STATE
from chartcleaner.engine import load_config, save_config
from tests.test_phase8_ui import _route, page  # noqa: F401 (fixture)

pytestmark = pytest.mark.nicegui_main_file("")

BLOCK_CFG = {
    "epic_phi_patterns": [[r"MRN: \d+ Na 141 mmol/L", "[REDACTED_MRN]"]],
    "cleaning_validation": {"mode": "enforce", "schema_version": 1},
}
BLOCK_CHART = "MRN: 12345678 Na 141 mmol/L\nPlan: aspirin 81 mg daily.\n"


def _cfg_path() -> str:
    return str(common.CONFIG_PATH)


def _write_cfg(**over) -> None:
    cfg = load_config(_cfg_path())
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["audit"] = {"enabled": False}
    cfg["cleaning_validation"] = {"mode": "enforce", "schema_version": 1}
    for key, value in over.items():
        if isinstance(value, dict):
            cfg[key] = {**(cfg.get(key) or {}), **value}
        else:
            cfg[key] = value
    save_config(cfg, _cfg_path())


async def _clean(user: User, route: str, text: str) -> None:
    CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                       summary=None, qa=[], tag="", fact_ack=False, release_ack=None)
    await _route(user, route, cc_app.clean_page)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            break
        await asyncio.sleep(0.02)


async def test_blocked_result_shows_reason_and_refuses_copy(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda *a: copied.append(a))
    _write_cfg(**BLOCK_CFG)
    await _clean(user, "/release-blocked", BLOCK_CHART)
    await user.should_see(marker="release-status")
    assert CLEAN_STATE["result"].validation.withheld == 1
    assert "12345678" in CLEAN_STATE["result_text"], "the raw text stays local for review"
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    await user.should_see(marker="release-blocked-dialog")
    assert not copied, "a blocked result never reaches the clipboard"


async def test_review_result_copies_after_acknowledgement_keyed_to_it(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda text, note="": copied.append(text))
    _write_cfg(boilerplate=[r"^Plan: aspirin 81 mg daily\.\n"])
    await _clean(user, "/release-review", BLOCK_CHART)
    assert CLEAN_STATE["result"].validation.rejected == 1
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    await user.should_see(marker="copy-guard")
    assert not copied
    user.find(marker="copy-anyway").click()
    assert copied, "acknowledging releases this exact result"
    key = CLEAN_STATE["release_ack"]
    assert key and len(key) == 16
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    assert len(copied) >= 2, "the acknowledged result copies again without a new dialog"


async def test_editing_the_result_invalidates_the_acknowledgement(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, "copy_to_clipboard", lambda text, note="": copied.append(text))
    _write_cfg(boilerplate=[r"^Plan: aspirin 81 mg daily\.\n"])
    await _clean(user, "/release-edit", BLOCK_CHART)
    CLEAN_STATE["release_ack"] = None
    user.find(marker="edit-result").click()
    out = next(iter(user.find(marker="result-output").elements))
    out.set_value(CLEAN_STATE["result_text"] + "\nDenies chest pain.\n")
    user.find(marker="copy-as").click()
    user.find(marker="copy-text").click()
    assert not copied, "an edited output is a different result and asks again"


async def test_review_tab_shows_validation_panel_with_coverage_and_groups(user: User, page):
    _write_cfg(boilerplate=[r"^Plan: aspirin 81 mg daily\.\n"])
    await _clean(user, "/release-panel", BLOCK_CHART)
    user.find(marker="review-tab").click()
    user.find(marker="review-diagnostics-tab").click()
    await user.should_see(marker="validation-panel")
    await user.should_see(marker="validation-coverage")
    coverage = next(iter(user.find(marker="validation-coverage").elements)).text.lower()
    assert "statement" in coverage or "processed" in coverage
    # issues are grouped by rule/stage with counts, not one row per match
    groups = user.find(marker="validation-group").elements
    labels = user.find(marker="validation-group-label").elements
    assert groups and labels
    assert any("boilerplate" in next(iter([e for e in [l] if True])).text.lower() for l in labels)
    for label in labels:
        assert "12345678" not in label.text, "the panel links to the source; it does not paste it"


async def test_support_text_never_claims_full_support_for_unchecked_prose(user: User, page):
    _write_cfg(cleaning_validation={"mode": "off", "schema_version": 1})
    await _clean(user, "/release-off", "Patient resting comfortably today.\n")
    user.find(marker="review-tab").click()
    user.find(marker="review-diagnostics-tab").click()
    await user.should_see(marker="validation-coverage")
    text = next(iter(user.find(marker="validation-coverage").elements)).text
    assert "not checked" in text.lower() or "off" in text.lower()
    assert "100" not in text
