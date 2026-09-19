"""Tests for clinical identifier recognizers and checksum validators."""

import pytest
from chartcleaner.clinical_identifiers import (
    find_dea_candidates,
    find_device_and_specimen_identifiers,
    find_npi_candidates,
    is_valid_dea,
    is_valid_npi,
    scan_clinical_identifiers,
)
from chartcleaner.engine import Pipeline, load_default_config


def test_npi_luhn_validation():
    # Valid NPIs (standard CMS format with 80840 prefix Luhn-24 checksum)
    assert is_valid_npi("1234567893") is True
    assert is_valid_npi("1679549745") is True
    assert is_valid_npi("1003823436") is True

    # Invalid NPIs
    assert is_valid_npi("1234567890") is False
    assert is_valid_npi("12345") is False
    assert is_valid_npi("3234567893") is False  # Must start with 1 or 2


def test_find_npi_in_clinical_text():
    text = "Attending Physician NPI: 1679549745 ordered labs. Another random 9999999999 should not match."
    matches = find_npi_candidates(text)
    assert len(matches) == 1
    assert matches[0].value == "1679549745"
    assert matches[0].entity_type == "NPI"
    assert matches[0].confidence >= 0.95


def test_dea_checksum_validation():
    # Valid DEA format: 2 letters, 7 digits
    # Formula: (d1+d3+d5) + 2*(d2+d4+d6) = last digit matches d7
    # Example: AB1234563:
    # d1=1, d3=3, d5=5 -> sum1 = 9
    # d2=2, d4=4, d6=6 -> sum2 = 2*(12) = 24
    # total = 33 -> last digit is 3 (matches d7)
    assert is_valid_dea("AB1234563") is True
    assert is_valid_dea("ab1234563") is True  # case-insensitive

    # Invalid check digit
    assert is_valid_dea("AB1234564") is False
    # Invalid prefix letter
    assert is_valid_dea("ZZ1234563") is False


def test_find_dea_in_clinical_text():
    text = "Prescribing provider DEA# AB1234563. Refills: 0."
    matches = find_dea_candidates(text)
    assert len(matches) == 1
    assert matches[0].value == "AB1234563"
    assert matches[0].entity_type == "DEA_NUMBER"


def test_udi_and_accession_recognition():
    text = "Implanted pacemaker UDI (01)00843210123456(17)261231(10)LOT4567A. Specimen ID: Acc-9874521"
    matches = find_device_and_specimen_identifiers(text)
    types = {m.entity_type for m in matches}
    assert "UDI_DEVICE_ID" in types
    assert "ACCESSION_NUMBER" in types


def test_scan_clinical_identifiers_combined():
    text = (
        "Ordering: Dr. Smith (NPI: 1679549745, DEA: AB1234563)\n"
        "Device: (01)00843210123456(17)261231(10)LOT4567A\n"
    )
    entities = scan_clinical_identifiers(text)
    assert len(entities) == 3
    found_types = [e.entity_type for e in entities]
    assert "NPI" in found_types
    assert "DEA_NUMBER" in found_types
    assert "UDI_DEVICE_ID" in found_types


def test_pipeline_clinical_identifiers_stage():
    cfg = load_default_config()
    cfg["clinical_identifiers"] = {
        "enabled": True,
        "redact_npi": True,
        "redact_dea": True,
        "redact_udi": True,
    }
    pipeline = Pipeline(cfg)
    raw = "Provider NPI 1679549745, DEA AB1234563."
    res = pipeline.run(raw, wrap=False)
    assert "1679549745" not in res.text
    assert "[REDACTED_NPI]" in res.text
    assert "AB1234563" not in res.text
    assert "[REDACTED_DEA_NUMBER]" in res.text
