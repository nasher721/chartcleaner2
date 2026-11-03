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
              typing_ws_max_bytes=0, typing_ws_limit_bytes=0, cycle_errors=0,
              health_complete=True, health_failures=0, health_stalls=0):
    complete = len(progress_seen) == expected_cycles
    evidence_complete = health_complete and bool(health)
    health_ok = (
        evidence_complete and health_failures == 0 and health_stalls == 0
        and percentile(health, .95) <= 100 and max(health) <= 1000
    )
    websocket_ok = typing_ws_limit_bytes == 0 or typing_ws_max_bytes < typing_ws_limit_bytes
    passed = (
        health_ok and reconnects == 0 and complete and all(progress_seen)
        and websocket_ok and cycle_errors == 0
        and percentile(typing, .95) <= 100 and max(clean_ms) <= 30000
    )
    return {
        "cycles": len(progress_seen), "reconnects": reconnects, "cycle_errors": cycle_errors,
        "health_complete": evidence_complete, "health_failures": health_failures,
        "health_stalls": health_stalls,
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
        self.failures = 0
        self.stalled = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(BASE_URL + "/api/v1/health", timeout=2) as response:
                    response.read()
            except Exception:
                self.failures += 1
            else:
                self.samples.append((time.perf_counter() - started) * 1000)
            self._stop.wait(.05)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join(3)
        self.stalled = self._thread.is_alive()


def remaining_ms(deadline, *, now=None):
    current = time.perf_counter() if now is None else now
    return max(0, int((deadline - current) * 1000))


def wait_for_stage_progress(page, deadline) -> bool:
    remaining = remaining_ms(deadline)
    if remaining <= 0:
        return False
    try:
        page.wait_for_function("""
          () => {
            const el = document.querySelector('[data-cc-marker="clean-progress"]');
            return el && el.checkVisibility() && /^Stage \\d+ of \\d+: \\S/.test(el.textContent || '');
          }
        """, timeout=remaining)
    except Exception:
        return False
    return True


def verify_clean_result(page, cycle_marker: str) -> None:
    errors = page.locator(".q-notification--negative, .q-dialog .text-red-600")
    if errors.count():
        raise AssertionError("Clean displayed an error")
    output = page.locator('[data-cc-marker="result-output"]').input_value()
    if cycle_marker not in output:
        raise AssertionError(f"Clean result is missing {cycle_marker}")


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
    health_by_cycle, health_failures_by_cycle, health_stalls_by_cycle = [], [], []
    reconnect_cycles = []
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
        context.add_init_script((ROOT / "scripts" / "clean_stability_reconnect_observer.js").read_text())
        page = context.new_page()
        page.goto(BASE_URL + "/", wait_until="networkidle")
        for cycle in range(cycles):
            progress_count = len(progress_seen)
            probe = HealthProbe()
            reconnects_before = 0
            cycle_failed = False
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
                reconnects_before = page.evaluate("window.__ccReconnects")
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
                with probe:
                    started = time.perf_counter()
                    progress_deadline = started + 1.0
                    page.locator('[data-cc-marker="run-clean"]').click()
                    progress = page.locator('[data-cc-marker="clean-progress"]')
                    progress_seen.append(wait_for_stage_progress(page, progress_deadline))
                    progress.wait_for(state="hidden", timeout=120000)
                    page.wait_for_function(
                        "!document.querySelector('[data-cc-marker=\"run-clean\"]').disabled",
                        timeout=120000)
                    verify_clean_result(page, f"Cycle {cycle}")
                clean_ms.append((time.perf_counter() - started) * 1000)
            except Exception:
                cycle_failed = True
            finally:
                cycle_health = list(probe.samples)
                health_by_cycle.append(cycle_health)
                health.extend(cycle_health)
                health_failures_by_cycle.append(probe.failures)
                health_stalls_by_cycle.append(int(probe.stalled))
                try:
                    reconnects_after = page.evaluate("window.__ccReconnects")
                    reconnect_cycles.append(max(0, reconnects_after - reconnects_before))
                except Exception:
                    reconnect_cycles.append(0)
                    cycle_failed = True
                if cycle_failed:
                    cycle_errors += 1
                    if len(progress_seen) == progress_count:
                        progress_seen.append(False)
                    try:
                        page.close()
                        page = context.new_page()
                        page.goto(BASE_URL + "/", wait_until="networkidle")
                    except Exception:
                        pass
        browser.close()
    health_complete = (
        len(health_by_cycle) == cycles and all(health_by_cycle)
        and len(health_failures_by_cycle) == cycles
        and len(health_stalls_by_cycle) == cycles
    )
    result = summarize(
        health, typing, clean_ms, progress_seen, sum(reconnect_cycles), cycles,
        typing_ws_max_bytes, typing_ws_limit_bytes, cycle_errors,
        health_complete=health_complete,
        health_failures=sum(health_failures_by_cycle),
        health_stalls=sum(health_stalls_by_cycle))
    result["samples"] = {
        "health_ms": health, "health_ms_by_cycle": health_by_cycle,
        "health_failures": health_failures_by_cycle, "health_stalls": health_stalls_by_cycle,
        "typing_ms": typing, "clean_ms": clean_ms, "progress_seen": progress_seen,
        "typing_ws_bytes": typing_ws_cycles, "reconnects": reconnect_cycles,
    }
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
