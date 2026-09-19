"""Tests for demographic fairness auditing and clinical preservation evaluation."""

import pytest
from chartcleaner import benchmark, evaluate
from chartcleaner.engine import load_default_config


def test_benchmark_has_demographic_cohorts():
    samples = benchmark.generate(n=12, seed=42)
    cohorts_found = {s.get("cohort") for s in samples}
    assert len(cohorts_found) >= 3
    # Check that clinical terms are planted
    assert any(len(s.get("clinical_terms", [])) > 0 for s in samples)


def test_evaluation_reports_fairness_and_preservation():
    samples = benchmark.generate(n=6, seed=123)
    cfg = load_default_config()
    report = evaluate.evaluate(cfg, samples)

    # Basic metrics
    assert "recall" in report
    assert "demographics" in report
    assert "fairness" in report
    assert "clinical_preservation" in report
    assert "f1" in report
    assert "f2" in report

    # Fairness metrics check
    assert "disparate_impact_ratio" in report["fairness"]
    assert "equity_status" in report["fairness"]
    assert report["fairness"]["disparate_impact_ratio"] > 0.0

    # Clinical preservation check
    cp = report["clinical_preservation"]
    assert cp["preservation_rate"] >= 80.0  # Clinical terms should be preserved
