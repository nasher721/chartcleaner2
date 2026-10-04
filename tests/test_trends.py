"""Lab trends and medication changes across notes (chartcleaner/trends.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from chartcleaner import service
from chartcleaner.engine import load_default_config
from chartcleaner.trends import build, labs_in

ROOT = Path(__file__).resolve().parent.parent

CHART = """Date of Service: 09/13/2026
Labs: Na 128, K 3.1, Cr 1.4, WBC 12.1
Medications:
- nimodipine 60 mg PO q4h
- cefazolin 2 g IV q8h
- metoprolol 25 mg PO BID

Date of Service: 09/14/2026
Labs
Sodium   132   L   135 - 145 mmol/L
Potassium  3.9    3.5 - 5.1 mmol/L
WBC 10.0
Medications:
- nimodipine 60 mg PO q4h
- metoprolol 50 mg PO BID
- levetiracetam 500 mg IV BID
Pt 2 days post coiling. PT to see.

Date of Service: 09/15/2026
BMP: Na 138 → 135 (L), K 4.2, Cr 1.1
Plan: continue.
"""


def cfg() -> dict:
    c = load_default_config()
    c["nlp_redaction"] = {"enabled": False}
    return c


def test_lab_trends_follow_each_note():
    report = build(CHART)
    assert report.notes == ["09/13", "09/14", "09/15"]
    lines = {t.name: t.to_text() for t in report.labs}
    assert lines["Na"] == "Na: 128 → 132 → 135 ↑"
    assert lines["K"] == "K: 3.1 → 3.9 → 4.2 ↑"
    assert lines["Cr"] == "Cr: 1.4 → — → 1.1 ↓"
    assert [t.name for t in report.labs] == ["Na", "K", "Cr", "WBC"]  # panel order


def test_medication_changes_between_notes():
    (change,) = build(CHART).meds
    assert (change.before, change.after) == ("09/13", "09/14")
    assert change.started == ["levetiracetam 500 mg IV BID"]
    assert change.stopped == ["cefazolin 2 g IV q8h"]
    assert change.changed == [("metoprolol 25 mg PO BID", "metoprolol 50 mg PO BID")]


def test_prose_lookalikes_are_not_labs():
    assert labs_in("Pt 2 days post op. NA 138. ca 2 cm mass. K 4.0 on 10/03 at 06:00") == {
        "Na": "138", "K": "4.0"}


def test_single_note_has_no_trends():
    report = build("Labs: Na 140, K 4.0\nMedications:\n- aspirin 81 mg PO daily\n")
    assert report.empty and report.to_text() == ""


def test_notes_without_dates_are_numbered():
    text = ("Progress Notes by A\nNa 130\n\nProgress Notes by B\nNa 134\n")
    assert build(text).notes == ["Note 1", "Note 2"]


def test_service_and_format_output():
    out = service.clean(CHART, config=cfg(), trends=True, record=False, wrap=True)
    assert out["text"].startswith("Lab trends (09/13 → 09/14 → 09/15)\nNa: 128 → 132 → 135 ↑")
    assert out["trends"]["labs"][0]["name"] == "Na"
    plain = service.clean(CHART, config=cfg(), record=False)
    assert "trends" not in plain and not plain["text"].startswith("Lab trends")


def test_cli_trends_flag(tmp_path):
    proc = subprocess.run([sys.executable, str(ROOT / "medical_cleaner.py"), "--stdin", "--stdout",
                           "--trends"], input=CHART, capture_output=True, text=True, cwd=ROOT,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("Lab trends (09/13")


async def test_clean_page_trends_tab(user, monkeypatch, tmp_path):
    from app_pages import common
    from chartcleaner import store
    from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
    from chartcleaner.engine import save_config

    c = cfg()
    c["audit"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(c, path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "append_run", lambda _r: None)
    monkeypatch.setattr(store, "load_prefs", lambda: dict(store.DEFAULT_PREFS, auto_clean=False))
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    CLEAN_STATE.update(input=CHART, mode="clean", result=None, result_text="", audit=None)
    try:
        await user.open("/")
        user.find(marker="run-clean").click()
        await user.should_see("Lab trends across", retries=50)
        await user.should_see("Copy trends")
        assert CLEAN_STATE["trends"].labs
    finally:
        CLEAN_STATE.clear()
        CLEAN_STATE.update(before)
        AUTO_LAST.clear()
        AUTO_LAST.update(auto_before)
