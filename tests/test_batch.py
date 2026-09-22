"""Tests for the batch cleaning service (filesystem only, no UI)."""

from chartcleaner.batch import BatchResult, run_batch
from chartcleaner.engine import load_default_config

# default config wraps output in <patient_chart> tags, so tiny inputs can grow;
# reduction sign is therefore not asserted on toy charts.
_CHART = (
    "Patient Note\n"
    "MRN: 1234567\n"
    "Patient is a 65 y/o man here for chest pain.\n"
    "Followup of hypertension and diabetes mellitus type 2, reviewed with the care team.\n"
)


def test_run_batch_cleans_files_in_order(tmp_path):
    a = tmp_path / "a.txt"; a.write_text(_CHART.replace("1234567", "1111111"), encoding="utf-8")
    b = tmp_path / "b.md"; b.write_text(_CHART.replace("1234567", "2222222"), encoding="utf-8")
    results = run_batch([a, b], load_default_config())
    assert [r.status for r in results] == ["ok", "ok"]
    assert [r.name for r in results] == ["a.txt", "b.md"]
    for r in results:
        assert r.chars_before > 0 and r.chars_after > 0
        assert isinstance(r.reduction, float)
        assert r.phi_total >= 1          # the MRN was redacted
        assert r.elapsed_ms >= 0
        assert r.cleaned                 # text came back
        assert "1234567" not in r.cleaned and "1111111" not in r.cleaned \
            and "2222222" not in r.cleaned
        assert "[REDACTED_ID]" in r.cleaned


def test_run_batch_isolates_errors(tmp_path):
    good = tmp_path / "good.txt"; good.write_text(_CHART, encoding="utf-8")
    bad = tmp_path / "bad.xyz"; bad.write_text("not a chart", encoding="utf-8")
    results = run_batch([good, bad], load_default_config())
    assert [r.status for r in results] == ["ok", "error"]
    assert results[1].error            # message present
    assert results[1].cleaned == ""


def test_run_batch_empty_file_is_ok(tmp_path):
    empty = tmp_path / "empty.txt"; empty.write_text("", encoding="utf-8")
    (r,) = run_batch([empty], load_default_config())
    assert r.status == "ok"
    assert r.chars_before == 0
    assert r.reduction == 0.0
    assert "<patient_chart>" in r.cleaned  # structuring wrapper is part of output


def test_batch_result_defaults():
    r = BatchResult(name="x.txt", path="/x.txt", status="error", error="boom")
    assert r.chars_after == 0 and r.findings == 0 and r.cleaned == ""
