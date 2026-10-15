"""Safety header and stale copied-forward lines — the modules and their
service / API / MCP / CLI / note-template exposure."""

from __future__ import annotations

import io
import sys

from chartcleaner import note_templates, safety, service, stale

CHART = """Date of Service: 09/13/2026
Allergies: NKDA
Code status: full code
Weight: 72 kg
Plan:
- Plan to extubate tomorrow.
- MRI pending.
- Continue nimodipine 60 mg q4h.

Date of Service: 09/14/2026
Allergies: NKDA
Code status: full code
Plan:
- Plan to extubate tomorrow.
- MRI pending.
- Continue nimodipine 60 mg q4h.

Date of Service: 09/15/2026
Allergies
Penicillin (rash)
Sulfa

Code status: DNR/DNI after family meeting
Contact precautions for MRSA.
Wt 70.4 kg, Ht 172 cm.
Heparin gtt per nomogram, aspirin 81 mg held.
Plan:
- Plan to extubate tomorrow.
- MRI pending.
- Continue nimodipine 60 mg q4h.
"""


def _items(report):
    return {i.name: i for i in report.items}


# --- safety header -----------------------------------------------------------------

def test_safety_takes_the_latest_statement_and_shows_what_changed():
    items = _items(safety.build(CHART))
    allergies = items["Allergies"]
    assert allergies.status == safety.CHANGED
    assert allergies.line == "Allergies: Penicillin (rash); Sulfa"
    assert allergies.earlier == ["Allergies: NKDA"]
    code = items["Code status"]
    assert code.status == safety.CHANGED and code.value == "DNR/DNI"
    assert code.line == "Code status: DNR/DNI after family meeting"
    assert code.earlier == ["Code status: full code"]


def test_safety_isolation_weight_height_and_antithrombotics():
    items = _items(safety.build(CHART))
    assert items["Isolation"].line == "Contact precautions for MRSA."
    assert items["Weight"].value == "Wt 70.4 kg" and items["Weight"].status == safety.FOUND
    assert items["Height"].value == "Ht 172 cm"
    assert "Heparin gtt" in items["Antithrombotics"].line


def test_safety_flags_missing_allergies_and_code_status_only():
    rep = safety.build("Note 09/15/2026\nAlert, on room air. Diet: regular.\n")
    items = _items(rep)
    assert items["Allergies"].status == safety.NOT_STATED
    assert items["Code status"].status == safety.NOT_STATED
    assert "Weight" not in items and "Isolation" not in items
    assert rep.empty and rep.to_text() == ""


def test_safety_same_fact_reworded_is_not_a_change():
    text = ("Date of Service: 09/14/2026\nCode status: Full Code.\n\n"
            "Date of Service: 09/15/2026\nRemains full code per family.\n")
    code = _items(safety.build(text))["Code status"]
    assert code.status == safety.FOUND and code.value == "full code"


def test_safety_ignores_history_only_allergy_words_and_prophylactic_heparin():
    items = _items(safety.build("PMH: allergic rhinitis.\nHeparin 5000 units SQ q8h.\n"))
    assert items["Allergies"].status == safety.NOT_STATED
    assert "Antithrombotics" not in items


def test_safety_text_does_not_repeat_the_label():
    text = safety.build(CHART).to_text()
    assert text.startswith("Safety header\nAllergies: Penicillin (rash); Sulfa\n  (earlier: Allergies: NKDA)")
    assert "Code status: Code status" not in text


def test_safety_reads_abbreviated_output():
    text = "ALLG\nPenicillin (rash)\n\nHep gtt per nomogram.\n"
    items = _items(safety.build(text))
    assert items["Allergies"].line == "ALLG: Penicillin (rash)"
    assert items["Antithrombotics"].line == "Hep gtt per nomogram."
    assert "Allergies: ALLG" not in safety.build(text).to_text()


def test_safety_lines_removed_by_cleaning():
    removed = ["Code status: DNR/DNI after family meeting", "Pager: 1234"]
    rep = safety.build("Allergies: NKDA\n", removed=removed)
    code = _items(rep)["Code status"]
    assert code.status == safety.REMOVED and code.removed == [removed[0]]
    # the removed line never went through redaction — it stays out of the text block
    assert "family meeting" not in rep.to_text()
    assert "Code status: removed by a cleaning rule" in rep.to_text()
    assert code in rep.flagged


def test_safety_found_but_another_line_removed():
    rep = safety.build("Code status: full code\n", removed=["Code Status: DNR"])
    code = _items(rep)["Code status"]
    assert code.status == safety.FOUND and code.removed == ["Code Status: DNR"]
    assert "a cleaning rule also removed" in rep.to_text()


def test_removed_lines_from_a_fact_report():
    from chartcleaner.engine import Pipeline, load_default_config
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    # Live code-status statements survive cleaning (Phase 12 T1; "meeting"
    # may be abbreviated to "mtg" by the abbreviations stage).
    result = Pipeline(cfg).run("Code status: DNR/DNI after family meeting\nAllergies: NKDA\n", wrap=False)
    assert "Code status: DNR/DNI after family" in result.text
    assert not any("Code status" in ln for ln in safety.removed_lines(result.fact_check))
    assert safety.removed_lines(None) == []


def test_live_code_status_survives_but_history_is_removed():
    from chartcleaner.engine import Pipeline, load_default_config
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    live = "Code status: DNR/DNI after family meeting\n"
    assert "Code status: DNR/DNI after family" in Pipeline(cfg).run(live, wrap=False).text
    history = "Code Status History: Full 10/01 \u2192 DNR 10/03\n"
    assert "Code Status History" not in Pipeline(cfg).run(history, wrap=False).text
    changed = "Code Status: Full 10/01 \u2192 DNR 10/03\n"
    assert "Code Status" not in Pipeline(cfg).run(changed, wrap=False).text
    for plain in ("Code status: Full code\n", "Code status: DNR\n"):
        assert plain.strip() in Pipeline(cfg).run(plain, wrap=False).text


def test_safety_reports_live_code_status_as_stated():
    from chartcleaner.engine import Pipeline, load_default_config
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    result = Pipeline(cfg).run("Code status: DNR/DNI after family meeting\nAllergies: NKDA\n", wrap=False)
    rep = safety.build(result.text, removed=safety.removed_lines(result.fact_check))
    code = _items(rep)["Code status"]
    assert code.status in (safety.FOUND, safety.CHANGED)
    assert "DNR/DNI" in code.line and code.line.startswith("Code status:")


# --- stale copied-forward lines -------------------------------------------------------

def test_stale_flags_time_bound_lines_copied_through_three_notes():
    rep = stale.build(CHART)
    assert rep.notes_found == 3
    texts = {s.text: s for s in rep.lines}
    assert set(texts) == {"Plan to extubate tomorrow.", "MRI pending."}
    s = texts["Plan to extubate tomorrow."]
    assert s.notes == 3 and s.since == "09/13/2026"
    # a standing order with no time-bound word is fine to carry forward
    assert "Continue nimodipine 60 mg q4h." not in texts


def test_stale_needs_an_unbroken_run_ending_at_the_latest_note():
    text = ("Date of Service: 09/13/2026\nRepeat CT in AM.\n\n"
            "Date of Service: 09/14/2026\nCT stable.\n\n"
            "Date of Service: 09/15/2026\nRepeat CT in AM.\n")
    assert stale.build(text).empty
    assert stale.build(text, min_notes=2).empty  # the note right before doesn't carry it


def test_stale_day_counters_that_never_advance():
    notes = "".join(f"Date of Service: 09/{d}/2026\nSAH, POD #2 s/p coiling.\n\n" for d in (13, 14, 15))
    rep = stale.build(notes)
    assert [s.word for s in rep.lines] == ["POD #2"]


def test_stale_reads_abbreviated_time_words():
    notes = "".join(f"Date of Service: 09/{d}/2026\nExtubate tmrw if SBT passes.\n\n" for d in (13, 14, 15))
    assert [s.word for s in stale.build(notes).lines] == ["tmrw"]


def test_stale_single_or_two_notes_report_nothing():
    assert stale.build("Plan to extubate tomorrow.").empty
    two = "Date of Service: 09/14/2026\nMRI pending.\n\nDate of Service: 09/15/2026\nMRI pending.\n"
    assert stale.build(two).empty
    assert stale.build("").to_text() == ""


# --- exposure ------------------------------------------------------------------------

def test_service_insights_include_safety_and_stale():
    assert "safety" in service.INSIGHTS and "stale" in service.INSIGHTS
    out = service.insights(CHART, ["safety", "stale"])
    assert out["safety"]["changed"] == ["Allergies", "Code status"]
    assert len(out["stale"]["lines"]) == 2


def test_format_output_puts_safety_first():
    out, _ = service.format_output(CHART, insights=True)
    assert out.startswith("Safety header")
    assert out.index("Possibly stale") < out.index("ICU bundle check")
    assert out.endswith(CHART)


def test_note_template_placeholders():
    cfg = {"note_templates": [{"name": "Top", "template": "{{safety}}\n\n{{stale}}"}]}
    text = note_templates.render("Top", CHART, cfg)
    assert text.startswith("Safety header") and "Possibly stale" in text


def test_api_and_mcp_accept_safety_and_stale(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from chartcleaner import api, mcp_server, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    app = FastAPI()
    api.register(app)
    client = TestClient(app, client=("127.0.0.1", 50000))
    client.headers["Authorization"] = f"Bearer {api.get_token()}"
    res = client.post("/api/v1/insights", json={"text": CHART, "which": ["safety", "stale"]})
    assert res.status_code == 200 and list(res.json()) == ["safety", "stale"]
    assert list(mcp_server.chart_insights(CHART, "safety,stale")) == ["safety", "stale"]


def test_cli_insights_show_safety(monkeypatch, capsys, tmp_path):
    import medical_cleaner
    from chartcleaner import store
    from chartcleaner.engine import load_default_config, save_config
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(CHART))
    monkeypatch.setattr(sys, "argv", ["clean-chart", "--stdin", "--stdout", "--insights", "--no-wrap"])
    medical_cleaner.main()
    out = capsys.readouterr().out
    assert "Safety header" in out
