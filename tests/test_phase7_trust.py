"""Phase 7 group 2: AI-output fact check, meaning guard, introduced and
impossible values, --check-known-good."""

from __future__ import annotations

import sys

import pytest

from chartcleaner import regression_set
from chartcleaner.chart_qa import ask_chart
from chartcleaner.engine import clean_text, load_default_config
from chartcleaner.fact_check import FactReport, meaning_changes, verify_output
from chartcleaner.plausibility import check as plausible
from chartcleaner.summarizer import summarize

SOURCE = ("Patient started on metoprolol 25 mg daily.\nVitals: BP 138/76, HR 72.\n"
          "Na 141, K 4.2. Allergies: NKDA. Full code.")


class FakeClient:
    def __init__(self, response):
        self.response = response

    def is_available(self):
        return True

    def list_models(self):
        return ["llama3.1"]

    def generate(self, prompt, model="", system=None):
        return self.response


# --- AI output check -------------------------------------------------------

def test_verify_output_accepts_values_worded_differently():
    check = verify_output(SOURCE, "On metoprolol 25 mg; sodium 141; HR 72; BP 138/76. NKDA.")
    assert check.ok and check.total >= 4 and check.score == 100.0


def test_verify_output_flags_invented_values_with_spans():
    text = "Metoprolol 50 mg daily, started lisinopril 10 mg. DNR."
    check = verify_output(SOURCE, text)
    shown = {u.display for u in check.unsupported}
    assert "50 mg" in shown and "lisinopril" in shown and "DNR" in shown
    assert "10 mg" in shown
    for u in check.unsupported:
        assert text[u.start:u.end].strip()
    assert not check.ok and check.score < 100 and "not in the chart" in check.headline()
    assert check.to_dict()["unsupported"][0]["start"] >= 0


def test_verify_output_with_no_values():
    check = verify_output(SOURCE, "The patient is doing well.")
    assert check.ok and check.total == 0 and check.headline() == "No clinical values to check"


def test_summary_and_answer_carry_the_fact_check():
    res = summarize(SOURCE, {}, client=FakeClient("Metoprolol 75 mg daily."))
    assert res.facts is not None and not res.facts.ok
    assert res.facts.unsupported[0].display == "75 mg"
    qa = ask_chart("What dose?", SOURCE, {}, client=FakeClient("Metoprolol 25 mg daily."))
    assert qa.facts.ok


def test_service_ask_reports_facts():
    from chartcleaner import service
    out = service.ask("dose?", SOURCE, config={}, client=FakeClient("metoprolol 100 mg"))
    assert out["facts"]["ok"] is False and out["facts"]["unsupported"][0]["display"] == "100 mg"


# --- meaning guard -----------------------------------------------------------

def test_dropped_negation_is_flagged():
    found = meaning_changes("Patient denies chest pain.\nNo fever.", "Patient chest pain.\nfever.")
    assert [m.kind for m in found] == ["negation", "negation"]


def test_side_switch_is_flagged_but_abbreviated_side_is_not():
    assert [m.kind for m in meaning_changes("Left MCA infarct", "Right MCA infarct")] == ["laterality"]
    assert meaning_changes("left MCA infarct", "L MCA infarct") == []
    assert meaning_changes("bilateral infiltrates", "b/l infiltrates") == []
    assert [m.kind for m in meaning_changes("Left leg weak", "leg weak")] == ["laterality"]


def test_removed_lines_are_not_meaning_changes():
    assert meaning_changes("No fever.\nStable.", "Stable.") == []


def test_abbreviations_keep_negation():
    from chartcleaner.abbreviations import abbreviate
    for phrase in ("No acute distress", "Do not resuscitate", "CT head without contrast",
                   "No growth to date"):
        out = abbreviate(phrase)[0]
        assert meaning_changes(phrase, out, "medical_abbreviations", "Abbr") == [], (phrase, out)


def test_pipeline_rule_that_drops_a_negation_needs_review():
    cfg = load_default_config()
    cfg["literal_replacements"] = [[r"\bdenies ", ""]]
    res = clean_text("Patient denies chest pain at rest today.\n", cfg, wrap=False)
    report = res.fact_check
    assert [m.kind for m in report.meaning] == ["negation"]
    assert report.meaning[0].category == "rule" and report.status == "review"
    assert "changed meaning" in report.headline()
    assert report.summary()["meaning_flags"] == 1


def test_sample_chart_has_no_meaning_flags():
    from pathlib import Path
    from chartcleaner.engine import load_config
    root = Path(__file__).resolve().parent.parent
    res = clean_text((root / "sample_chart.txt").read_text(), load_config(root / "config.json"))
    assert res.fact_check.meaning == [] and res.fact_check.introduced == []


# --- introduced and impossible values ----------------------------------------

def test_values_new_to_the_chart_are_reported():
    cfg = load_default_config()
    cfg["literal_replacements"] = [[r"sodium normal", "Na 141"]]
    res = clean_text("Labs: sodium normal today, reviewed.\n", cfg, wrap=False)
    report = res.fact_check
    assert [x.display for x in report.introduced] == ["Na 141"]
    assert report.introduced[0].category == "rule"
    assert report.summary()["introduced"] == 1


def test_plausibility_flags_impossible_values_and_doses():
    found = plausible("Na 1410\nK 4.1\nGCS 17\nTemp 37.2\nTemp 98.6\nnimodipine 600 mg q4h\n"
                      "heparin 1200 units/hr\nSpO2 96%")
    shown = {(x.kind, x.label, x.value) for x in found}
    assert ("value", "Na", "1410") in shown and ("value", "GCS", "17") in shown
    assert ("dose", "nimodipine", "600") in shown
    assert not any(x.label in ("K", "T", "SpO2", "heparin") for x in found)
    assert "decimal" in next(x for x in found if x.kind == "dose").message


def test_impossible_value_already_in_the_chart_does_not_alert():
    res = clean_text("Na 1410 on the morning panel, repeat pending.\n", load_default_config(), wrap=False)
    report = res.fact_check
    assert report.implausible and not report.implausible_new
    assert report.status == "ok" and "look impossible" in report.headline()


def test_impossible_value_made_by_cleaning_alerts():
    cfg = load_default_config()
    cfg["literal_replacements"] = [[r"Na 141\b", "Na 1410"]]
    res = clean_text("Na 141 this morning, stable.\n", cfg, wrap=False)
    assert res.fact_check.implausible_new and res.fact_check.status == "alert"


def test_report_defaults_stay_backward_compatible():
    rep = FactReport(total=3, losses=[])
    assert rep.status == "ok" and rep.meaning == [] and rep.to_dict()["implausible_values"] == []


# --- --check-known-good --------------------------------------------------------

def test_cli_check_known_good(monkeypatch, capsys, tmp_path):
    import medical_cleaner
    from chartcleaner import store
    cfg_path = tmp_path / "config.json"
    from chartcleaner.engine import save_config
    save_config(load_default_config(), cfg_path)
    monkeypatch.setattr(store, "CONFIG_PATH", cfg_path)

    monkeypatch.setattr(sys, "argv", ["clean-chart", "--check-known-good"])
    with pytest.raises(SystemExit) as done:
        medical_cleaner.main()
    assert done.value.code == 0 and "No known-good charts" in capsys.readouterr().out

    text = "Patient Note\nPatient is stable today and eating well.\n"
    good = clean_text(text, load_default_config()).text
    regression_set.add(text, good, "stable")
    other = text + "Walking in the hall.\n"
    regression_set.add(other, clean_text(other, load_default_config()).text + "\nextra", "drifted")
    with pytest.raises(SystemExit) as done:
        medical_cleaner.main()
    out = capsys.readouterr().out
    assert done.value.code == 1
    assert "✓ stable" in out and "✕ drifted" in out and "1 of 2" in out


def test_meaning_guard_stays_fast_on_long_repetitive_charts():
    import time
    before = "\n".join(["Denies chest pain.", "Stable overnight.", "Plan unchanged."] * 1300)
    after = before.replace("Denies chest pain.", "chest pain.", 1)
    started = time.perf_counter()
    found = meaning_changes(before, after)
    assert [m.kind for m in found] == ["negation"]
    assert time.perf_counter() - started < 2.0
