import subprocess
import sys
import time
from pathlib import Path

from scripts import benchmark_clean_stability as benchmark
from scripts.benchmark_clean_stability import HealthProbe, percentile, summarize


def test_stability_summary_has_no_chart_text_and_checks_targets():
    report = summarize([10, 20, 30], [20, 40, 60], [1000, 2000], [True] * 10, 0,
                       expected_cycles=10)
    assert report["health_p95_ms"] == 30
    assert report["typing_p95_ms"] == 60
    assert report["warm_clean_ms"] == 2000
    assert report["cycles"] == 10
    assert report["reconnects"] == 0
    assert report["passed"] is True
    assert "chart" not in report


def test_percentile_uses_nearest_rank():
    assert percentile([1, 2, 3, 4], 0.95) == 4


def test_stability_summary_reports_websocket_budget_failure():
    report = summarize([10], [20], [1000], [True], 0, expected_cycles=1,
                       typing_ws_max_bytes=200000, typing_ws_limit_bytes=200000)
    assert report["typing_ws_max_bytes"] == 200000
    assert report["typing_ws_limit_bytes"] == 200000
    assert report["passed"] is False


def test_stability_summary_counts_failed_cycles_with_empty_samples():
    report = summarize([], [], [], [False], 0, expected_cycles=1, cycle_errors=1)
    assert report["cycles"] == 1
    assert report["cycle_errors"] == 1
    assert report["health_max_ms"] == 0
    assert report["warm_clean_ms"] == 0
    assert report["passed"] is False


def test_stability_summary_rejects_empty_health_evidence():
    report = summarize([], [20], [1000], [True], 0, expected_cycles=1,
                       health_complete=False)
    assert report["health_complete"] is False
    assert report["health_failures"] == 0
    assert report["passed"] is False


def test_stability_summary_rejects_health_probe_failures():
    report = summarize([10], [20], [1000], [True], 0, expected_cycles=1,
                       health_failures=1)
    assert report["health_failures"] == 1
    assert report["passed"] is False


def test_health_probe_records_urlopen_exceptions(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic probe failure")

    monkeypatch.setattr(benchmark.urllib.request, "urlopen", fail)
    with HealthProbe() as probe:
        time.sleep(0.12)
    assert probe.failures >= 1
    assert probe.samples == []
    assert probe.stalled is False


def test_progress_deadline_returns_only_remaining_milliseconds():
    assert benchmark.remaining_ms(10.0, now=9.75) == 250
    assert benchmark.remaining_ms(10.0, now=10.0) == 0
    assert benchmark.remaining_ms(10.0, now=10.1) == 0


class _FakeProgressPage:
    def __init__(self, text):
        self.text = text

    def wait_for_function(self, script, timeout):
        assert timeout >= 0
        if not self.text.startswith("Stage ") or " of " not in self.text or ": " not in self.text:
            raise TimeoutError("no stage progress")


def test_stage_progress_requires_nonempty_stage_text_within_deadline():
    wait = getattr(benchmark, "wait_for_stage_progress", None)
    assert wait is not None, "benchmark must require stage progress text"
    deadline = time.perf_counter() + 1
    assert wait(_FakeProgressPage("Stage 2 of 8: Learned rules"), deadline) is True
    assert wait(_FakeProgressPage(""), deadline) is False


class _FakeLocator:
    def __init__(self, *, count=0, value=""):
        self._count = count
        self._value = value

    def count(self):
        return self._count

    def input_value(self):
        return self._value


class _FakeResultPage:
    def __init__(self, *, output, errors=0):
        self.output = output
        self.errors = errors

    def locator(self, selector):
        if selector == '[data-cc-marker="result-output"]':
            return _FakeLocator(value=self.output)
        if selector == ".q-notification--negative, .q-dialog .text-red-600":
            return _FakeLocator(count=self.errors)
        raise AssertionError(f"Unexpected selector: {selector}")


def test_handled_worker_failure_cannot_pass_benchmark_summary():
    verify = getattr(benchmark, "verify_clean_result", None)
    assert verify is not None, "benchmark must verify the cleaned result"
    cycle_errors = 0
    try:
        verify(_FakeResultPage(output="Cycle 0\n", errors=1), "Cycle 0")
    except AssertionError:
        cycle_errors += 1
    report = summarize([10], [20], [1000], [True], 0, expected_cycles=1,
                       cycle_errors=cycle_errors)
    assert cycle_errors == 1
    assert report["passed"] is False


def test_clean_result_must_contain_the_cycle_marker():
    verify = getattr(benchmark, "verify_clean_result", None)
    assert verify is not None, "benchmark must verify the cleaned result"
    try:
        verify(_FakeResultPage(output="previous result only\n"), "Cycle 7")
    except AssertionError as exc:
        assert "Cycle 7" in str(exc)
    else:
        raise AssertionError("stale output was accepted as a successful clean")


def test_source_command_loads_project_imports():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "benchmark_clean_stability.py"), "--help"],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
