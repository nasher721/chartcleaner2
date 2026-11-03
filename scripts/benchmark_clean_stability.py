"""Measure Clean-page stability against a running app; emits numbers only."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402
from scripts.benchmark_validation import synth  # noqa: E402

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8765")


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))]


def summarize(health, typing, clean_ms, progress_seen, reconnects, expected_cycles,
              typing_ws_max_bytes=0, typing_ws_limit_bytes=0, cycle_errors=0):
    complete = len(progress_seen) == expected_cycles
    websocket_ok = typing_ws_limit_bytes == 0 or typing_ws_max_bytes < typing_ws_limit_bytes
    passed = (
        reconnects == 0 and complete and all(progress_seen) and websocket_ok and cycle_errors == 0
        and percentile(health, .95) <= 100 and max(health) <= 1000
        and percentile(typing, .95) <= 100 and max(clean_ms) <= 30000
    )
    return {
        "cycles": len(progress_seen), "reconnects": reconnects, "cycle_errors": cycle_errors,
        "health_p95_ms": percentile(health, .95) if health else 0,
        "health_max_ms": max(health, default=0),
        "typing_p95_ms": percentile(typing, .95) if typing else 0,
        "warm_clean_ms": max(clean_ms, default=0),
        "typing_ws_max_bytes": typing_ws_max_bytes,
        "typing_ws_limit_bytes": typing_ws_limit_bytes,
        "progress_within_1s": complete and all(progress_seen), "passed": passed,
    }


class HealthProbe:
    def __init__(self):
        self.samples = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.perf_counter()
            with urllib.request.urlopen(BASE_URL + "/api/v1/health", timeout=2) as response:
                response.read()
            self.samples.append((time.perf_counter() - started) * 1000)
            self._stop.wait(.05)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join(3)


def assert_exclusive_editor_channel(page) -> None:
    value_listeners = page.evaluate("""
      () => Object.values(mounted_app.elements)
        .filter(element => element.props?.['data-cc-marker'] === 'highlight-source')
        .flatMap(element => element.events)
        .filter(event => event.type.startsWith('update:'))
        .map(event => event.type)
    """)
    if value_listeners:
        raise AssertionError(f"Un-debounced editor listeners: {value_listeners}")


def measure(cycles: int, chars: int) -> dict:
    health, typing, clean_ms, progress_seen = [], [], [], []
    reconnects = 0
    cycle_errors = 0
    typing_ws_max_bytes = 0
    typing_ws_cycles = []
    typing_ws_limit_bytes = max(100000, chars * 2)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_init_script("""
          window.__ccWsBytes = 0;
          const NativeWebSocket = window.WebSocket;
          window.WebSocket = class extends NativeWebSocket {
            send(data) {
              if (typeof data === 'string') window.__ccWsBytes += data.length;
              super.send(data);
            }
          };
        """)
        page = context.new_page()
        page.goto(BASE_URL + "/", wait_until="networkidle")
        for cycle in range(cycles):
            progress_count = len(progress_seen)
            try:
                assert_exclusive_editor_channel(page)
                page.evaluate("""
                  text => {
                    const ta = document.querySelector('[data-cc-marker="highlight-source"]');
                    ta.value = text;
                    ta.dispatchEvent(new Event('input', {bubbles: true}));
                  }
                """, synth(chars) + f"\nCycle {cycle}\n")
                page.evaluate("window.__ccWsBytes = 0")
                samples = page.evaluate("""async () => {
                  const ta = document.querySelector('[data-cc-marker="highlight-source"]');
                  const samples = [];
                  for (let i = 0; i < 20; i++) {
                    const start = performance.now();
                    ta.value += 'x';
                    ta.dispatchEvent(new Event('input', {bubbles: true}));
                    await new Promise(requestAnimationFrame);
                    samples.push(performance.now() - start);
                  }
                  return samples;
                }""")
                typing.extend(samples)
                typing_bytes = page.evaluate("window.__ccWsBytes")
                typing_ws_cycles.append(typing_bytes)
                typing_ws_max_bytes = max(typing_ws_max_bytes, typing_bytes)
                started = time.perf_counter()
                with HealthProbe() as probe:
                    page.locator('[data-cc-marker="run-clean"]').click(force=True)
                    progress = page.locator('[data-cc-marker="clean-progress"]')
                    try:
                        progress.wait_for(state="visible", timeout=1000)
                        progress_seen.append(True)
                    except Exception:
                        progress_seen.append(False)
                    progress.wait_for(state="hidden", timeout=120000)
                    page.wait_for_function(
                        "!document.querySelector('[data-cc-marker=\"run-clean\"]').disabled",
                        timeout=120000)
                health.extend(probe.samples)  # include the final request after the thread joins
                clean_ms.append((time.perf_counter() - started) * 1000)
                reconnects += page.locator('text=/reconnecting/i').filter(visible=True).count()
            except Exception:
                cycle_errors += 1
                if len(progress_seen) == progress_count:
                    progress_seen.append(False)
                try:
                    page.close()
                except Exception:
                    pass
                page = context.new_page()
                page.goto(BASE_URL + "/", wait_until="networkidle")
        browser.close()
    result = summarize(health, typing, clean_ms, progress_seen, reconnects, cycles,
                       typing_ws_max_bytes, typing_ws_limit_bytes, cycle_errors)
    result["samples"] = {"health_ms": health, "typing_ms": typing, "clean_ms": clean_ms,
                         "progress_seen": progress_seen, "typing_ws_bytes": typing_ws_cycles}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--chars", type=int, nargs="+", default=[100000, 250000, 500000])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    sizes = []
    for chars in args.chars:
        result = measure(args.cycles, chars)
        result["characters"] = chars
        sizes.append(result)
    report = {"cycles": args.cycles, "sizes": sizes,
              "passed": all(result["passed"] for result in sizes)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
