"""Playwright end-to-end smoke test for the Chart Cleaner web UI.

Page-Object-Model + artifact-on-failure, following the e2e-testing skill. This
drives a *running* Chart Cleaner instance (NiceGUI serves a web UI) with a real
browser, so it validates the full stack: browser -> websocket -> pipeline ->
rendered output.

Requirements (one time):
    pip install playwright
    playwright install chromium

Usage:
    # terminal 1: start the app on a known port (no auto-open browser)
    python app.py --port 8080 --show false
    # terminal 2: run this suite
    python e2e/run_clean_e2e.py            # defaults to http://localhost:8080
    BASE_URL=http://localhost:8080 python e2e/run_clean_e2e.py

Note: the in-environment, browser-free equivalent that runs under pytest is
tests/test_e2e_ui.py (NiceGUI's in-process User fixture).
"""
from __future__ import annotations

import os
import sys
import time

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

SAMPLE = (
    "Patient Name: John Doe\n"
    "MRN: 1234567\n"
    "DOB: 03/04/1975\n"
    "Phone: (617) 555-0199\n"
    "Plan: continue aspirin 81 mg daily and follow up in clinic.\n"
)


class CleanPage:
    """Page Object for the Clean page."""

    def __init__(self, page):
        self.page = page
        self.input = page.locator('[mark="highlight-source"]')
        self.run = page.locator('[mark="run-clean"]')
        self.output = page.locator('[mark="result-output"]')

    def goto(self):
        self.page.goto(BASE_URL + "/", wait_until="networkidle")
        self.page.wait_for_selector('[mark="highlight-source"]', state="visible")

    def clean(self, text: str) -> str:
        self.input.fill(text)
        self.run.click()
        # The result textarea is populated once the clean finishes.
        self.page.wait_for_function(
            "() => { const el = document.querySelector('[mark=\"result-output\"]'); "
            "return el && el.value && el.value.length > 0; }",
            timeout=30000,
        )
        return self.output.input_value()


def main() -> int:
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(15000)
        clean = CleanPage(page)

        try:
            clean.goto()
            out = clean.clean(SAMPLE)
            assert "John Doe" not in out, "Patient name was not redacted"
            assert "1234567" not in out, "MRN was not redacted"
            assert "(617) 555-0199" not in out, "Phone was not redacted"
            assert len(out) > 0, "Clean produced empty output"
            print("PASS: clean flow redacts planted PHI and yields output")
        except Exception as e:  # noqa: BLE001
            failures.append(f"clean flow: {e}")
            try:
                page.screenshot(path=str(ARTIFACTS / "clean-failure.png"), full_page=True)
            except Exception:
                pass

        browser.close()

    print(f"\nArtifacts: {ARTIFACTS}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("E2E: PASSING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
