import subprocess
import sys
from pathlib import Path

from scripts.benchmark_clean_stability import percentile, summarize


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


def test_source_command_loads_project_imports():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "benchmark_clean_stability.py"), "--help"],
        cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
