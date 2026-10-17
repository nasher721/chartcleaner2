"""Phase 14 tests: context window management (token estimator, 3-tier context
router, Ollama num_ctx, serial-position bookending, ungrounded turn filtering)
and clinical compaction/safety (lab-medication contradictions, vent_summary)."""

from __future__ import annotations

import json
import pytest

from chartcleaner import (
    chart_qa,
    conflicts,
    context_router,
    fact_check,
    local_llm,
    prompt_templates,
    service,
    stage_cache,
    summarizer,
    token_estimator,
)
from chartcleaner.compactors import vent as vent_compactor
from chartcleaner.engine import (
    BUILTIN_STAGE_IDS,
    STAGE_ANCHORS,
    STAGE_KINDS,
    STAGE_LABELS,
    Pipeline,
    load_default_config,
    validate_config,
)


# ---------------------------------------------------------------------------
# Track A — CW1: Clinical Token Estimator & RunResult / Service Telemetry
# ---------------------------------------------------------------------------

def test_token_estimator_empty_and_basic():
    assert token_estimator.estimate_tokens("") == 0
    assert token_estimator.estimate_tokens_fast("") == 0
    assert token_estimator.token_reduction(0, 0) == 0.0
    text = "67 y/o male admitted with right MCA stroke, NIHSS 14, started on nicardipine gtt."
    tok = token_estimator.estimate_tokens(text)
    assert tok >= 15
    summary = token_estimator.format_token_summary(200, 120)
    assert "~200" in summary and "~120" in summary and "+40.0%" in summary


def test_token_estimator_weights_punctuation_and_numbers_higher_than_prose():
    prose = "The patient rested comfortably overnight without any acute clinical events noted"
    flowsheet = "10/12 04:00: BP 142/88 | HR 94 | RR 22 | SpO2 96% | Na 139 | K 4.2 | Cr 1.4"
    # Similar character lengths, but the flowsheet has many more BPE subword splits
    assert abs(len(prose) - len(flowsheet)) <= 6
    assert token_estimator.estimate_tokens(flowsheet) > token_estimator.estimate_tokens(prose)


def test_pipeline_and_service_record_token_telemetry():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    raw = (
        "Author: Jane Doe, MD\n"
        "History of Present Illness:\n"
        "Patient has hypertension and diabetes mellitus with shortness of breath.\n"
    )
    res = Pipeline(cfg).run(raw, wrap=False)
    assert res.tokens_before > 0
    assert res.tokens_after > 0
    assert res.tokens_before >= res.tokens_after
    assert isinstance(res.token_reduction, float)

    hist = res.to_history_dict("test")
    assert hist["tokens_before"] == res.tokens_before
    assert hist["tokens_after"] == res.tokens_after

    payload = service.clean(raw, config=cfg, wrap=False, record=False)
    assert payload["tokens_before"] == res.tokens_before
    assert payload["tokens_after"] == res.tokens_after
    assert "token_reduction" in payload


# ---------------------------------------------------------------------------
# Track A — CW2: 3-Tier Context Router & Ollama num_ctx
# ---------------------------------------------------------------------------

MULTI_NOTE_CHART = """Date of Service: 10/10/2026
Allergies: Penicillin (anaphylaxis)
Code status: Full code
HPI: 68 y/o female admitted with subarachnoid hemorrhage Hunt-Hess 3 Fisher 4.
Hospital Course: EVD placed at 10 cm H2O. Started on nimodipine 60 mg PO q4h.
Labs: Na 138, K 4.1, Cr 0.9, WBC 11.2, Hgb 11.8, Plt 210.
Assessment & Plan:
# Subarachnoid hemorrhage: continue nimodipine 60 mg PO q4h, neuro checks q1h, transcranial Dopplers daily.
# Hydrocephalus: maintain EVD at 10 cm H2O, monitor ICP and hourly output.
# Prophylaxis: sequential compression devices, famotidine 20 mg PO BID.

Date of Service: 10/11/2026
Allergies: Penicillin (anaphylaxis)
Code status: Full code
HPI: 68 y/o female admitted with subarachnoid hemorrhage Hunt-Hess 3 Fisher 4.
Hospital Course: EVD placed at 10 cm H2O. Started on nimodipine 60 mg PO q4h.
Labs: Na 138, K 4.1, Cr 0.9, WBC 11.2, Hgb 11.8, Plt 210.
Assessment & Plan:
# Subarachnoid hemorrhage: continue nimodipine 60 mg PO q4h, neuro checks q1h, transcranial Dopplers daily.
# Hydrocephalus: maintain EVD at 10 cm H2O, monitor ICP and hourly output.
# Prophylaxis: sequential compression devices, famotidine 20 mg PO BID.

Date of Service: 10/12/2026
Allergies: Penicillin (anaphylaxis)
Code status: Full code
HPI: 68 y/o female admitted with subarachnoid hemorrhage Hunt-Hess 3 Fisher 4.
Hospital Course: EVD placed at 10 cm H2O. Started on nimodipine 60 mg PO q4h.
Labs: Na 131, K 4.0, Cr 0.9, WBC 10.4, Hgb 11.5, Plt 195.
Assessment & Plan:
# Subarachnoid hemorrhage: mild right MCA vasospasm on TCDs; started norepinephrine to keep SBP 140-160.
# Hyponatremia: cerebral salt wasting, started 3% NaCl at 30 mL/h and fludrocortisone 0.1 mg PO BID.
# Hydrocephalus: EVD weaning trial to 15 cm H2O today.
"""


def test_context_router_tier1_full():
    packed = context_router.pack_context(MULTI_NOTE_CHART, budget_tokens=4000)
    assert packed.tier == "full"
    assert not packed.was_compacted
    assert packed.text == MULTI_NOTE_CHART
    assert packed.tier_label().startswith("Context: full")


def test_context_router_tier2_delta_has_no_synthetic_numbers():
    full_tok = token_estimator.estimate_tokens(MULTI_NOTE_CHART)
    # Budget smaller than full chart, but big enough for the deduplicated delta view
    packed = context_router.pack_context(MULTI_NOTE_CHART, budget_tokens=int(full_tok * 0.72))
    assert packed.tier == "delta"
    assert packed.was_compacted
    assert packed.tokens_after < packed.tokens_before
    # Every line in packed.text must come verbatim from MULTI_NOTE_CHART (no synthetic % headers)
    assert "Redundant Text Pruned" not in packed.text
    for line in packed.text.splitlines():
        if line.strip():
            assert line.strip() in MULTI_NOTE_CHART


def test_context_router_tier3_routed_prioritizes_query_and_preserves_order():
    # Force Tier 3 with a tight budget and ask about sodium / hyponatremia
    packed = context_router.pack_context(
        MULTI_NOTE_CHART,
        budget_tokens=110,
        query="Why is the patient on 3% NaCl and what is the sodium?",
    )
    assert packed.tier == "routed"
    assert packed.was_compacted
    assert "3% NaCl" in packed.text or "Na 131" in packed.text
    # Selected lines must appear in the same relative order as in MULTI_NOTE_CHART
    positions = [MULTI_NOTE_CHART.find(p) for p in packed.text.split("\n\n") if p.strip()]
    assert all(pos >= 0 for pos in positions)
    assert positions == sorted(positions)


def test_local_llm_client_passes_num_ctx(monkeypatch):
    captured: dict = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"response": "ok"}).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    monkeypatch.setattr(local_llm, "_urlopen", fake_urlopen)
    client = local_llm.LocalLlmClient("http://127.0.0.1:11434", num_ctx=16384)
    out = client.generate("hello", model="llama3.1")
    assert out == "ok"
    assert captured["body"]["options"] == {"num_ctx": 16384}


def test_config_validator_checks_context_tokens():
    cfg = load_default_config()
    cfg["local_llm"] = {"context_tokens": 16384}
    errs, _ = validate_config(cfg)
    assert errs == []

    cfg["local_llm"] = {"context_tokens": 512}
    errs, _ = validate_config(cfg)
    assert any("local_llm.context_tokens" in e for e in errs)

    cfg["local_llm"] = {"context_tokens": True}
    errs, _ = validate_config(cfg)
    assert any("local_llm.context_tokens" in e for e in errs)


# ---------------------------------------------------------------------------
# Track A — CW3: Serial-Position Bookending, Ungrounded History Filter & Citations
# ---------------------------------------------------------------------------

def test_bookending_in_summary_and_qa_prompts():
    short_prompt = summarizer.build_prompt("clinical", "", "Na 138")
    assert short_prompt.endswith("Na 138")

    bookended = summarizer.build_prompt("clinical", "", "Na 138", bookend=True)
    assert not bookended.endswith("Na 138")
    assert "Reminder —" in bookended and "ONLY facts explicitly present" in bookended

    qa_short = chart_qa.build_qa_prompt("What is the Na?", "Na 138")
    assert qa_short.endswith("Chart:\nNa 138")

    qa_bookended = chart_qa.build_qa_prompt("What is the Na?", "Na 138", bookend=True)
    assert not qa_bookended.endswith("Chart:\nNa 138")
    assert qa_bookended.count("Question: What is the Na?") == 2
    assert "Answer using ONLY facts explicitly present in the chart above" in qa_bookended


def test_filter_grounded_history_drops_ungrounded_turns():
    turns = [
        chart_qa.QaTurn("What is the Na?", "Na is 138.", ungrounded=False),
        chart_qa.QaTurn("What is the Cr?", "Cr is 9.9.", ungrounded=True),
        chart_qa.QaTurn("What is the K?", "K is 4.1.", ungrounded=False),
    ]
    prompt = chart_qa.build_qa_prompt("Any change in electrolytes?", "Na 138, K 4.1", history=turns)
    assert "Na is 138." in prompt
    assert "K is 4.1." in prompt
    assert "Cr is 9.9." not in prompt


def test_summarize_and_ask_chart_citations_point_to_original_chart_under_routing():
    class FakeClient:
        def is_available(self):
            return True

        def list_models(self):
            return ["llama3.1"]

        def generate(self, prompt, model="", system=None):
            return "Na 131 on 10/12/2026; started 3% NaCl at 30 mL/h."

    # Force Tier 2/3 compaction via small context_tokens
    cfg = {"local_llm": {"model": "llama3.1", "context_tokens": 1024}}
    s_res = summarizer.summarize(MULTI_NOTE_CHART, cfg, client=FakeClient())
    assert s_res.context is not None
    assert s_res.grounding.is_safe
    assert s_res.citations and s_res.citations[0].found
    c = s_res.citations[0]
    assert MULTI_NOTE_CHART[c.start:c.end] == c.source

    q_res = chart_qa.ask_chart("What is the sodium plan?", MULTI_NOTE_CHART, cfg, client=FakeClient())
    assert q_res.context is not None
    assert q_res.grounding.is_safe
    assert q_res.citations and q_res.citations[0].found
    qc = q_res.citations[0]
    assert MULTI_NOTE_CHART[qc.start:qc.end] == qc.source


def test_prompt_templates_render_with_budget_tokens():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    full = prompt_templates.render("Sign-out / handoff", MULTI_NOTE_CHART, cfg)
    compacted = prompt_templates.render("Sign-out / handoff", MULTI_NOTE_CHART, cfg, budget_tokens=180)
    assert len(compacted) < len(full)
    srv = service.prompt(MULTI_NOTE_CHART, "Sign-out / handoff", config=cfg, clean_first=False, record=False, budget_tokens=180)
    assert srv["text"] == compacted


# ---------------------------------------------------------------------------
# Track B — CL1: Lab ↔ Active Medication Contradictions
# ---------------------------------------------------------------------------

def test_lab_medication_contradictions_all_four_classes():
    text = (
        "Date of Service: 10/15/2026\n"
        "Labs: K 6.1, Plt 28, INR 4.4, Na 123\n"
        "Meds:\n"
        "- KCl 40 mEq PO BID\n"
        "- Heparin gtt at 1200 units/hr\n"
        "- Warfarin 5 mg PO daily\n"
        "- D5W at 100 mL/h\n"
    )
    rep = conflicts.build(text)
    lab_meds = [c for c in rep.conflicts if c.kind == "lab-medication"]
    assert len(lab_meds) == 4
    whys = " | ".join(c.why for c in lab_meds)
    assert "hyperkalemia" in whys
    assert "thrombocytopenia" in whys
    assert "INR" in whys
    assert "hyponatremia" in whys


# ---------------------------------------------------------------------------
# Track B — CL2: Ventilator & ABG Compactor (stage vent_summary)
# ---------------------------------------------------------------------------

def test_vent_summary_compacts_abg_and_vent_blocks():
    chart = (
        "ABGs:\n"
        "02:00 ABG: 7.26 / 58 / 55 / 24\n"
        "06:00 ABG: 7.34 / 46 / 78 / 24\n"
        "10:00 ABG: 7.41 / 40 / 94 / 25\n"
        "\n"
        "Vent Settings:\n"
        "02:00 AC/VC RR 20 TV 450 FiO2 60% PEEP 10\n"
        "06:00 AC/VC RR 16 TV 450 FiO2 40% PEEP 8\n"
        "10:00 PSV PS 10 PEEP 5 FiO2 35% SBT passed\n"
    )
    cfg = {"vent_summary": {"enabled": True, "abg": True, "settings": True}}
    out, n, details = vent_compactor.run(chart, cfg)
    assert n == 6
    assert details == {"readings_condensed": 6}
    assert "ABG (3, 02:00–10:00): pH 7.26 → 7.34 → 7.41 (acidemia: pH 7.26)" in out
    assert "pCO2 58 → 46 → 40" in out
    assert "pO2 55 → 78 → 94 (hypoxemia: pO2 55)" in out
    assert "HCO3 24 → 25" in out
    assert "Vent (3, 02:00–10:00): mode AC/VC → PSV, FiO2 60% → 40% → 35%, PEEP 10 → 8 → 5" in out
    assert "(SBT passed)" in out


def test_vent_summary_labeled_abg_and_single_line_untouched():
    labeled = (
        "01:00 VBG pH 7.31, pCO2 50, pO2 38, HCO3 25, Lactate 2.4\n"
        "05:00 VBG pH 7.38, pCO2 44, pO2 42, HCO3 25, Lactate 1.2\n"
    )
    out, n, _ = vent_compactor.run(labeled, {"vent_summary": {"enabled": True}})
    assert n == 2
    assert "VBG (2, 01:00–05:00): pH 7.31 → 7.38, pCO2 50 → 44" in out
    assert "Lactate 2.4 → 1.2" in out

    # Single line is never rewritten
    single = "02:00 ABG: 7.40 / 40 / 92 / 24\nPatient resting comfortably on vent."
    out_single, n_single, _ = vent_compactor.run(single, {"vent_summary": {"enabled": True}})
    assert n_single == 0
    assert out_single == single


def test_vent_summary_registration_and_fact_check():
    assert "vent_summary" in BUILTIN_STAGE_IDS
    assert STAGE_ANCHORS["vent_summary"] == "flowsheet_tables"
    assert STAGE_LABELS["vent_summary"] == "Ventilator & ABG summary"
    assert STAGE_KINDS["vent_summary"] == "vent"
    assert "vent_summary" in stage_cache.OWNED_KEYS
    assert "vent_summary" in fact_check.BY_DESIGN_STAGES

    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["vent_summary"] = {"enabled": True, "abg": True, "settings": True}
    errs, _ = validate_config(cfg)
    assert errs == []

    chart = (
        "02:00 AC/VC RR 20 TV 450 FiO2 60% PEEP 10\n"
        "06:00 AC/VC RR 16 TV 450 FiO2 40% PEEP 8\n"
        "10:00 AC/VC RR 14 TV 450 FiO2 35% PEEP 5\n"
    )
    res = Pipeline(cfg).run(chart, wrap=False)
    assert "Vent (3, 02:00–10:00):" in res.text
    # Any intermediate value dropped by condensing 3+ readings is classified as by_design, never unexpected
    assert res.fact_check is not None
    assert not [loss for loss in res.fact_check.losses if loss.category == "unexpected"]
