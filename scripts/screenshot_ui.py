"""Dev helper: screenshot every Chart Cleaner page at desktop + mobile widths.

Not part of the test suite. Run against a live app:
    .venv/bin/python app.py --port 8899 --no-browser &
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 scripts/screenshot_ui.py [page ...] [--light]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

CHROME = ("/Users/Nash/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/"
          "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
PAGES = [("/", "clean"), ("/batch", "batch"), ("/rules", "rules"), ("/pipeline", "pipeline"),
         ("/stats", "stats"), ("/scripts", "scripts"), ("/settings", "settings"),
         ("/doctor", "doctor"), ("/census", "census")]
OUT = Path(__file__).resolve().parent.parent / ".pytest_tmp" / "shots"
BASE = "http://127.0.0.1:8899"
VIEWPORTS = tuple((width, 900, str(width)) for width in (360, 768, 1199, 1200, 1440))


async def main() -> None:
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    light = "--light" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        for width, height, tag in VIEWPORTS:
            ctx = await browser.new_context(viewport={"width": width, "height": height},
                                            device_scale_factor=2)
            page = await ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE + "/", wait_until="networkidle")
            if light:
                await page.wait_for_timeout(1200)
                await page.click(".cc-headbtn[aria-label='Toggle light or dark mode']")
                await page.wait_for_timeout(700)
            suffix = "-light" if light else ""
            for path, name in PAGES:
                if only and name not in only:
                    continue
                await page.goto(BASE + path, wait_until="networkidle")
                await page.wait_for_timeout(1900)
                await page.screenshot(path=str(OUT / f"{tag}-{name}{suffix}.png"), full_page=True)
                print(f"{tag}-{name}{suffix}: ok")
            if errors:
                print("  console errors:", errors[:6])
            await ctx.close()
        await browser.close()


asyncio.run(main())
