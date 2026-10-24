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
         ("/doctor", "doctor"), ("/census", "census")]
BASE = "http://127.0.0.1:8899"
WIDTHS = [int(w) for w in sys.argv[1:]] or [360, 768, 1199, 1200, 1440]
HEIGHT = 900


async def check_clean_split_transitions(browser) -> None:
    """A resized desktop split must not leak into stacked or mobile layouts."""
    ctx = await browser.new_context(viewport={"width": 1440, "height": HEIGHT})
    page = await ctx.new_page()
    await page.goto(BASE + "/", wait_until="networkidle")
    source = page.get_by_label("Chart text (paste an Epic export, drop a file, or load the sample)")
    output = page.locator("textarea[aria-label='Cleaned chart text']")
    original_input = "Plan: recheck\n"
    await source.fill(original_input)
    await page.get_by_role("button", name="Clean", exact=True).click()
    await page.wait_for_selector(".cc-result-header", state="visible")
    original_output = await output.input_value()
    handle = page.locator(".cc-split-handle")
    await handle.focus()
    for _ in range(3):
        await handle.press("ArrowRight")
    assert await handle.get_attribute("aria-valuenow") == "65"

    for width in (1199, 767):
        await page.set_viewport_size({"width": width, "height": HEIGHT})
        await page.wait_for_timeout(150)
        layout = page.locator(".cc-clean-workspace")
        columns = await layout.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ')")
        assert len(columns) == 1, f"{width}: resized desktop tracks leaked: {columns}"
        root_width = await layout.evaluate("el => el.getBoundingClientRect().width")
        if width == 767:
            assert await page.locator(".cc-result-pane").is_visible()
            await page.locator(".cc-mobile-workspace-switch").get_by_text("Input", exact=True).click()
        for selector in (".cc-input-pane", ".cc-result-pane"):
            pane = page.locator(selector)
            if await pane.is_visible():
                pane_width = await pane.evaluate("el => el.getBoundingClientRect().width")
                assert abs(pane_width - root_width) <= 1, (
                    f"{width}: {selector} is {pane_width}px inside {root_width}px workspace")
        assert await source.input_value() == original_input
        assert await output.input_value() == original_output
        print(f"split transition {width}: one column, full-width panes, content unchanged")

    await page.set_viewport_size({"width": 1440, "height": HEIGHT})
    await page.wait_for_timeout(150)
    columns = await page.locator(".cc-clean-workspace").evaluate(
        "el => getComputedStyle(el).gridTemplateColumns.split(' ')")
    assert len(columns) == 3
    assert await handle.get_attribute("aria-valuenow") == "65"
    assert await source.input_value() == original_input
    assert await output.input_value() == original_output
    print("split transition 1440: desktop split and content preserved")

    root_box = await page.locator(".cc-clean-workspace").bounding_box()
    for event_name in ("pointercancel", "lostpointercapture"):
        await handle.press("Home")
        handle_box = await handle.bounding_box()
        y = handle_box["y"] + 40
        await page.mouse.move(handle_box["x"] + handle_box["width"] / 2, y)
        await page.mouse.down()
        await page.mouse.move(root_box["x"] + root_box["width"] * 0.70, y, steps=4)
        assert await handle.get_attribute("aria-valuenow") == "70"
        await handle.dispatch_event(event_name, {"pointerId": 1})
        await page.mouse.move(root_box["x"] + root_box["width"] * 0.40, y, steps=4)
        assert await handle.get_attribute("aria-valuenow") == "70", (
            f"{event_name}: interrupted gesture kept resizing")
        await page.mouse.up()
        print(f"{event_name}: move listener cleaned up")
    await ctx.close()


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        await check_clean_split_transitions(browser)
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
