"""Phase 8: ICU bundle check, to-do / pending list, local model comparison —
plus their service / API / MCP / CLI / note-template exposure."""

from __future__ import annotations

import io
import sys

import pytest

from chartcleaner import bundle, model_compare, note_templates, pending, service
from chartcleaner.summarizer import LlmUnavailableError, NoModelError

CHART = """Date of Service: 09/14/2026
Assessment & Plan:
1. SAH, Hunt-Hess 3.
   - Nimodipine 60 mg q4h.
   - Pantoprazole 40 mg daily.
MRI pending.

Date of Service: 09/15/2026
Overnight events:
- Febrile to 38.6, blood cultures sent.

Intubated 9/13, on the vent, PEEP 5, FiO2 40%. Propofol gtt at 30 mcg/kg/min.
Foley in place since 9/11.
O2 sat 95%. PT 12.1, INR 1.1.
Cefepime started 9/12 for VAP. Vancomycin day 3 of 7.
Urine cx pending.
Neuro: follows simple commands.

Assessment & Plan:
1. SAH, Hunt-Hess 3, post-coiling day 5.
   - Nimodipine 60 mg q4h.
   - TCDs daily; follow up vasospasm read.
   - If Na < 133, start salt tabs 2 g TID.
   - Repeat CTH in AM.
2. Respiratory failure
   - SBT this morning, wean FiO2.
3. Prophylaxis: heparin 5000 units SQ q8h, SCDs.
   - Tube feeds at goal via OGT, insulin sliding scale.
   - Neurosurgery consult following, appreciate recs.
   - Consider PICC for long-term access.
   - Code status: full code.
"""


# --- ICU bundle check ----------------------------------------------------------

def _items(report):
    return {i.name: i for i in report.items}


def test_bundle_reads_the_latest_note_only():
    rep = bundle.build(CHART)
    items = _items(rep)
    # pantoprazole is only in the earlier note — the latest note never mentions GI ppx
    assert items["GI prophylaxis"].status == bundle.MISSING
    assert items["VTE prophylaxis"].status == bundle.ADDRESSED
    assert "heparin 5000" in items["VTE prophylaxis"].line
    assert items["Nutrition"].status == bundle.ADDRESSED
    assert items["Glucose control"].status == bundle.ADDRESSED
    assert items["Code status"].status == bundle.ADDRESSED
    assert items["Bowel regimen"].status == bundle.MISSING
    assert items["Head of bed"].status == bundle.MISSING


def test_bundle_vent_items_apply_only_when_ventilated_or_sedated():
    rep = bundle.build(CHART)
    assert rep.ventilated and rep.sedated
    items = _items(rep)
    assert items["Breathing trial (SBT)"].status == bundle.ADDRESSED
    assert items["Sedation vacation (SAT)"].status == bundle.MISSING

    quiet = bundle.build("Note 09/15/2026\nAlert, on room air. Diet: regular.\n")
    q = _items(quiet)
    assert not quiet.ventilated and not quiet.sedated
    assert q["Breathing trial (SBT)"].status == bundle.NOT_APPLICABLE
    assert q["Sedation vacation (SAT)"].status == bundle.NOT_APPLICABLE


def test_bundle_short_abbreviations_need_capitals_and_context():
    # "O2 sat" is not a sedation vacation, the PT/INR lab is not physical therapy
    items = _items(bundle.build(CHART))
    assert items["Mobility"].status == bundle.MISSING
    assert "sat" not in items["Sedation vacation (SAT)"].line.lower()
    ok = _items(bundle.build("Note 09/15/2026\nIntubated.\nSAT and SBT this AM. PT eval today.\n"))
    assert ok["Sedation vacation (SAT)"].status == bundle.ADDRESSED
    assert ok["Mobility"].status == bundle.ADDRESSED


def test_bundle_reviews_stale_lines_and_open_ended_antibiotics():
    items = _items(bundle.build(CHART))
    assert items["Lines / catheters"].status == bundle.REVIEW
    assert any("Foley day 5" in d for d in items["Lines / catheters"].details)
    abx = items["Antibiotic duration"]
    assert abx.status == bundle.REVIEW
    # cefepime (day 4, no duration) is flagged; vancomycin "day 3 of 7" states one
    assert any(d.startswith("cefepime day 4") for d in abx.details)
    assert not any("vancomycin" in d for d in abx.details)


def test_bundle_text_and_dict():
    rep = bundle.build(CHART)
    text = rep.to_text()
    assert text.startswith("ICU bundle check (latest note)")
    assert "Not mentioned: GI prophylaxis" in text and "Review — Lines / catheters" in text
    d = rep.to_dict()
    assert "GI prophylaxis" in d["gaps"] and d["ventilated"] is True
    assert bundle.build("").empty and bundle.build("").to_text() == ""


# --- to-do / pending -------------------------------------------------------------

def test_pending_groups_latest_note_lines_verbatim():
    rep = pending.build(CHART)
    groups = {g: [i.text for i in items] for g, items in rep.grouped().items()}
    assert groups["Pending results"] == ["Febrile to 38.6, blood cultures sent.", "Urine cx pending."]
    assert "MRI pending." not in str(groups)            # earlier note only
    assert groups["Consults"] == ["Neurosurgery consult following, appreciate recs."]
    assert groups["If / then"] == ["If Na < 133, start salt tabs 2 g TID."]
    assert "TCDs daily; follow up vasospasm read." in groups["Follow up"]
    assert "Repeat CTH in AM." in groups["Follow up"]
    assert groups["Consider"] == ["Consider PICC for long-term access."]
    # neuro exam wording is not a to-do
    assert not any("commands" in t for items in groups.values() for t in items)


def test_pending_time_hints_dedup_and_negatives():
    rep = pending.build(CHART)
    repeat = next(i for i in rep.items if i.text.startswith("Repeat CTH"))
    assert repeat.when == "in AM"
    twice = pending.build("Note\nUrine cx pending.\n- Urine cx pending.\nNo labs pending. Nothing pending.\n")
    assert [i.text for i in twice.items] == ["Urine cx pending."]
    assert pending.build("Alert and oriented.").empty


def test_pending_text_is_a_checklist():
    text = pending.build(CHART).to_text()
    assert text.startswith("To do / pending (latest note)")
    assert "- [ ] Urine cx pending." in text
    assert "- [ ] Repeat CTH in AM.  [in AM]" in text
    assert pending.build(CHART).to_dict()["groups"]["Consider"] == ["Consider PICC for long-term access."]


# --- exposure: service, format_output, templates, API, MCP, CLI ------------------

def test_service_insights_include_pending_and_bundle():
    out = service.insights(CHART, ["pending", "bundle"])
    assert list(out) == ["pending", "bundle"]
    assert out["bundle"]["gaps"] and out["pending"]["items"]
    assert "pending" in service.INSIGHTS and "bundle" in service.INSIGHTS


def test_format_output_insights_order():
    out, _ = service.format_output(CHART, insights=True)
    assert out.index("Overnight events") < out.index("To do / pending") \
        < out.index("ICU bundle check") < out.index("Problem-oriented view")
    assert out.endswith(CHART)


def test_note_template_placeholders():
    cfg = {"note_templates": [{"name": "Rounds", "template": "{{pending}}\n\n{{bundle}}"}]}
    text = note_templates.render("Rounds", CHART, cfg)
    assert text.startswith("To do / pending") and "ICU bundle check" in text


def test_api_and_mcp_accept_the_new_insights(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from chartcleaner import api, mcp_server, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    app = FastAPI()
    api.register(app)
    client = TestClient(app, client=("127.0.0.1", 50000))
    client.headers["Authorization"] = f"Bearer {api.get_token()}"
    res = client.post("/api/v1/insights", json={"text": CHART, "which": ["bundle", "pending"]})
    assert res.status_code == 200 and list(res.json()) == ["bundle", "pending"]
    assert list(mcp_server.chart_insights(CHART, "pending")) == ["pending"]


def test_cli_insights_show_todo_and_bundle(monkeypatch, capsys, tmp_path):
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
    assert "To do / pending" in out and "ICU bundle check" in out


# --- local model comparison --------------------------------------------------------

SUMMARY_CHART = "Na 131. Nimodipine 60 mg q4h. Cefepime for VAP.\nGCS 14.\n"


class FakeClient:
    """Answers per model: 'good' repeats the chart, 'bad' invents a dose, 'slow' fails."""

    def __init__(self, models=("good", "bad", "broken"), available=True):
        self.models = list(models)
        self.available = available
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def list_models(self) -> list[str]:
        return list(self.models)

    def generate(self, prompt: str, model: str = "", system: str | None = None, **kw) -> str:
        self.calls.append(model)
        if model == "broken":
            raise RuntimeError("model crashed")
        if model == "bad":
            return "Na 128. Nimodipine 90 mg q2h. Started meropenem.\nGCS 9."
        return "Na 131. Nimodipine 60 mg q4h. Cefepime for VAP.\nGCS 14."


def test_compare_ranks_models_by_values_not_in_the_chart():
    client = FakeClient()
    progress = []
    res = model_compare.compare({}, charts=[("A", SUMMARY_CHART), ("B", SUMMARY_CHART)],
                                client=client, on_progress=lambda d, t, m: progress.append((d, t, m)))
    assert [s.model for s in res.scores] == ["good", "bad", "broken"]
    good, bad, broken = res.scores
    assert res.best is good and good.unsupported == 0 and good.grounding == 100.0
    assert bad.unsupported_per_summary > 0 and bad.grounding < 100.0
    assert "90 mg" in " ".join(bad.examples()) or "meropenem" in " ".join(bad.examples()).lower()
    assert broken.errors == 2 and not broken.ok_trials
    assert good.citation_coverage == 100.0
    assert client.calls.count("good") == 2       # every model × every chart
    assert progress[0] == (0, 6, "good") and progress[-1] == (6, 6, "")


def test_compare_text_and_dict():
    res = model_compare.compare({}, ["good", "bad"], [("A", SUMMARY_CHART)], client=FakeClient())
    text = res.to_text()
    assert "Best on these charts: good" in text and text.splitlines()[2].startswith("good")
    d = res.to_dict()
    assert d["best"] == "good" and d["scores"][0]["model"] == "good"
    assert d["scores"][1]["trials"][0]["unsupported"]


def test_compare_uses_the_preset_and_leaves_config_alone():
    cfg = {"local_llm": {"model": "good", "prompt_preset": "brief", "custom_prompt": "custom!"}}
    seen = []

    class Spy(FakeClient):
        def generate(self, prompt, model="", system=None, **kw):
            seen.append(prompt)
            return super().generate(prompt, model, system)

    model_compare.compare(cfg, ["good"], [("A", SUMMARY_CHART)], preset="one_liner", client=Spy())
    assert "one-liner" in seen[0] and "custom!" not in seen[0]
    assert cfg["local_llm"]["prompt_preset"] == "brief"


def test_compare_errors_when_nothing_to_compare():
    with pytest.raises(LlmUnavailableError):
        model_compare.compare({}, charts=[("A", SUMMARY_CHART)], client=FakeClient(available=False))
    with pytest.raises(NoModelError):
        model_compare.compare({}, charts=[("A", SUMMARY_CHART)], client=FakeClient(models=()))
    with pytest.raises(ValueError):
        model_compare.compare({}, charts=[], client=FakeClient())
    all_fail = model_compare.compare({}, ["broken"], [("A", SUMMARY_CHART)], client=FakeClient())
    assert all_fail.best is None and "—" in all_fail.to_text()


def test_compare_refuses_non_loopback_urls():
    with pytest.raises(LlmUnavailableError):
        model_compare.compare({"local_llm": {"base_url": "http://example.com:11434"}},
                              charts=[("A", SUMMARY_CHART)])


def test_default_charts_prefer_known_good_then_sample():
    from chartcleaner import regression_set
    charts = model_compare.default_charts()
    assert charts and charts[0][0] == "Sample chart"
    regression_set.add("raw chart", "cleaned chart text", "Bed 4")
    assert model_compare.default_charts() == [("Bed 4", "cleaned chart text")]


def test_cli_compare_models(monkeypatch, capsys, tmp_path):
    import medical_cleaner
    from chartcleaner import store
    from chartcleaner.engine import load_default_config, save_config
    path = tmp_path / "config.json"
    save_config(load_default_config(), path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    real = model_compare.compare
    monkeypatch.setattr(model_compare, "compare", lambda cfg, models=None, **kw: real(
        cfg, models, [("A", SUMMARY_CHART)], client=FakeClient(), **kw))
    monkeypatch.setattr(sys, "argv", ["clean-chart", "--compare-models", "good", "bad",
                                      "--summary-preset", "brief"])
    with pytest.raises(SystemExit) as exc:
        medical_cleaner.main()
    assert exc.value.code == 0
    out = capsys.readouterr()
    assert "preset 'brief'" in out.out and "Best on these charts: good" in out.out
    assert "[1/2] good" in out.err

    monkeypatch.setattr(model_compare, "compare", lambda *a, **k: (_ for _ in ()).throw(
        LlmUnavailableError("http://127.0.0.1:11434")))
    with pytest.raises(SystemExit) as exc:
        medical_cleaner.main()
    assert exc.value.code == 2
