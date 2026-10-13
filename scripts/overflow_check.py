"""Dev helper: scan every page for horizontal overflow at several widths.

Not part of the test suite. Run against a live app on port 8899:
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \\
        scripts/overflow_check.py
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
         ("/doctor", "doctor")]
BASE = "http://127.0.0.1:8899"
WIDTHS = [int(w) for w in sys.argv[1:]] or [390, 414, 768, 1024]
HEIGHT = 900


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        for width in WIDTHS:
            ctx = await browser.new_context(
                viewport={"width": width, "height": HEIGHT}, device_scale_factor=1)
            page = await ctx.new_page()
            for path, name in PAGES:
                await page.goto(BASE + path, wait_until="networkidle")
                await page.wait_for_timeout(900)
                # document-level horizontal overflow
                doc = await page.evaluate(
                    "() => ({sw: document.documentElement.scrollWidth, "
                    "cw: document.documentElement.clientWidth})")
                worst = 0
                worst_sel = ""
                # also probe elements wider than the viewport
                offenders = await page.evaluate(
                    """() => {
                        const vw = document.documentElement.clientWidth;
                        const out = [];
                        document.querySelectorAll('*').forEach(el => {
                            const r = el.getBoundingClientRect();
                            if (r.right > vw + 1 && r.width > 0) {
                                out.push({
                                    tag: el.tagName.toLowerCase(),
                                    cls: (el.className && el.className.toString
                                          ? el.className.toString() : '') .slice(0,60),
                                    right: Math.round(r.right),
                                    w: Math.round(r.width)
                                });
                            }
                        });
                        return out.sort((a,b)=>b.right-a.right).slice(0,4);
                    }""")
                if doc["sw"] > doc["cw"]:
                    worst = doc["sw"]
                    worst_sel = "document"
                if worst > width:
                    print(f"[{width}] OVERFLOW: {name}({worst}>{width}) "
                          f"offenders={offenders}")
                else:
                    print(f"[{width}] OK: {name} (sw={doc['sw']})")
            await ctx.close()
        await browser.close()


asyncio.run(main())
