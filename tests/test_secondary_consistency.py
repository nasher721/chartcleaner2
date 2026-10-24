"""Consistency contracts for customization and system pages."""

from __future__ import annotations

import pytest
from nicegui.testing import User

import app as cc_app
from tests.test_phase8_ui import _route, page  # noqa: F401

pytestmark = pytest.mark.nicegui_main_file("")


def test_settings_categories_are_deterministic():
    from app_pages import settings

    assert settings.settings_category("Local AI") == "Output & AI"
    assert settings.settings_category("Stored chart data") == "Privacy & data"
    assert settings.settings_category("Folder watcher — auto-clean as you drop files") == "Automation"
    assert settings.settings_category("Unrecognized custom card") == "System"


async def test_rules_link_to_advanced_pipeline(user: User, page):
    await _route(user, "/rules-pipeline-link", cc_app.text_rules_page)
    user.find(marker="rules-open-pipeline").click()
    await user.should_see("Pipeline")


async def test_pipeline_links_back_to_quick_rules(user: User, page):
    await _route(user, "/pipeline-rules-link", cc_app.pipeline_page)
    user.find(marker="pipeline-open-rules").click()
    await user.should_see("My text rules")


async def test_statistics_keep_summary_before_details(user: User, page, monkeypatch):
    from chartcleaner import store

    monkeypatch.setattr(store, "load_runs", lambda: [])
    await _route(user, "/stats-empty-summary", cc_app.stats_page)
    await user.should_see("No runs recorded yet.")


async def test_statistics_collapse_detailed_activity(user: User, page, monkeypatch):
    from chartcleaner import store

    runs = [
        {"ts": "2026-10-01T09:00:00", "source": "test", "chars_before": 100,
         "chars_after": 60, "stages": [
             {"label": "Boilerplate blocks", "chars_before": 100, "chars_after": 60}]},
        {"ts": "2026-10-02T09:00:00", "source": "test", "chars_before": 80,
         "chars_after": 70, "stages": [
             {"label": "Boilerplate blocks", "chars_before": 80, "chars_after": 70}]},
    ]
    monkeypatch.setattr(store, "load_runs", lambda: runs)
    await _route(user, "/stats-summary-first", cc_app.stats_page)
    await user.should_see("Average reduction across every run")
    await user.should_see(marker="rule-health")
    details = next(iter(user.find(marker="stats-details").elements))
    assert details.value is False
    user.find(marker="stats-details").click()
    await user.should_see("Noise removed by each stage over time")


async def test_doctor_separates_attention_from_ordinary_checks(user: User, page, monkeypatch):
    from chartcleaner import doctor

    checks = [
        doctor.Check("broken", "Broken dependency", "fail", "Repair required", "reinstall"),
        doctor.Check("degraded", "Optional integration", "warn", "Check configuration"),
        doctor.Check("healthy", "Python", "ok", "Ready"),
        doctor.Check("information", "Data folder", "info", "Available"),
    ]
    fixes = []
    monkeypatch.setattr(doctor, "run_checks", lambda **kwargs: checks)
    monkeypatch.setattr(doctor, "apply_fix", lambda fix: fixes.append(fix) or (True, "Fixed"))

    await _route(user, "/doctor-attention-first", cc_app.doctor_page)
    await user.should_see(marker="doctor-broken", retries=100)
    await user.should_see(marker="doctor-degraded")
    failing = next(iter(user.find(marker="doctor-failing-checks").elements))
    broken = next(iter(user.find(marker="doctor-broken").elements))
    ordinary = next(iter(user.find(marker="doctor-ok-checks").elements))
    healthy = next(iter(user.find(marker="doctor-healthy").elements))
    information = next(iter(user.find(marker="doctor-information").elements))
    assert broken in set(failing.descendants())
    assert healthy in set(ordinary.descendants()) and information in set(ordinary.descendants())
    assert ordinary.value is False

    with user.client:
        ordinary.set_value(True)  # NiceGUI's user simulator does not toggle expansions on click.
    assert ordinary.value is True
    await user.should_see(marker="doctor-healthy")
    await user.should_see(marker="doctor-information")

    user.find(marker="doctor-fix-reinstall").click()
    await user.should_see("Fixed", retries=100)
    assert fixes == ["reinstall"]
