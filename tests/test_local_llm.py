"""Tests for local LLM client and clinical grounding verification."""

import pytest
from chartcleaner.local_llm import (
    GroundingResult,
    LocalLlmClient,
    verify_clinical_grounding,
)


def test_verify_clinical_grounding_grounded():
    source = (
        "Patient started on Metoprolol 25 mg daily.\n"
        "Vitals: BP 138/76, HR 72 on 10/12/2026.\n"
        "Potassium 4.2, Creatinine 1.1."
    )
    # Fully grounded summary
    summary = (
        "Assessment: Hypertensive patient.\n"
        "Vitals show BP 138/76 with HR 72 on 10/12/2026.\n"
        "Labs: Potassium 4.2, Creatinine 1.1.\n"
        "Plan: Metoprolol 25 mg daily."
    )

    res = verify_clinical_grounding(source, summary)
    assert res.is_safe is True
    assert res.grounding_score >= 90.0
    assert len(res.ungrounded_entities) == 0


def test_verify_clinical_grounding_hallucination_detected():
    source = (
        "Patient started on Metoprolol 25 mg daily.\n"
        "Vitals: BP 138/76, HR 72 on 10/12/2026."
    )
    # Hallucinated summary: fabricated 50 mg, HR 110, date 12/25/2026
    hallucinated_summary = (
        "Patient has HR 110 bpm on 12/25/2026.\n"
        "Increased Metoprolol to 50 mg daily."
    )

    res = verify_clinical_grounding(source, hallucinated_summary, threshold=80.0)
    assert res.is_safe is False
    assert len(res.ungrounded_entities) > 0
    assert any("50 mg" in u or "50" in u for u in res.ungrounded_entities)


def test_local_llm_availability_when_offline():
    # Points to non-existent port
    client = LocalLlmClient(base_url="http://127.0.0.1:59999", timeout=0.2)
    assert client.is_available() is False
    assert client.list_models() == []
