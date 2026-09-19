"""Tests for clinical whitelist and false-positive entity protection."""

import pytest
from chartcleaner.clinical_whitelist import (
    CLINICAL_WHITELIST,
    filter_clinical_false_positives,
    is_clinical_term,
)
from chartcleaner.engine import Pipeline, load_default_config


def test_clinical_term_detection():
    # Medications
    assert is_clinical_term("Plavix") is True
    assert is_clinical_term("levophed") is True
    assert is_clinical_term("Tamar") is True
    assert is_clinical_term("Lisinopril") is True
    assert is_clinical_term("Metoprolol") is True

    # Anatomical terms
    assert is_clinical_term("Colon") is True
    assert is_clinical_term("appendix") is True
    assert is_clinical_term("Spleen") is True
    assert is_clinical_term("Femur") is True

    # Non-clinical terms / actual human names
    assert is_clinical_term("John") is False
    assert is_clinical_term("Smith") is False
    assert is_clinical_term("Maria") is False
    assert is_clinical_term("Anderson") is False


def test_filter_clinical_false_positives():
    entities = [
        {"value": "Plavix", "type": "PERSON"},
        {"value": "John Doe", "type": "PERSON"},
        {"value": "Levophed", "type": "PERSON"},
        {"value": "Sarah Smith", "type": "PERSON"},
    ]
    filtered = filter_clinical_false_positives(entities)
    remaining_values = [e["value"] for e in filtered]
    assert "John Doe" in remaining_values
    assert "Sarah Smith" in remaining_values
    assert "Plavix" not in remaining_values
    assert "Levophed" not in remaining_values


def test_nlp_stage_preserves_clinical_terms():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {
        "enabled": True,
        "protect_clinical_terms": True,
    }
    pipeline = Pipeline(cfg)

    # In clinical notes, medications or anatomical terms might be capitalized at sentence start
    # or inside plan items where a naive NER model thinks it is a PERSON.
    text = (
        "Patient John Doe was admitted for chest pain.\n"
        "Started on Plavix 75 mg and Levophed drip.\n"
        "CT of the Colon showed no acute perforation."
    )
    res = pipeline.run(text, wrap=False)

    # John Doe should be redacted
    assert "John Doe" not in res.text
    # Clinical medications and anatomical terms must be preserved
    assert "Plavix" in res.text
    assert "Levophed" in res.text
    assert "Colon" in res.text
