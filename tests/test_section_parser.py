"""Tests for clinical section parser and structured format transformers."""

import json
import pytest
from chartcleaner.section_parser import parse_clinical_sections


def test_parse_clinical_sections_basic():
    chart_text = (
        "Patient seen in cardiology clinic for routine follow-up.\n\n"
        "Chief Complaint: Exertional shortness of breath\n\n"
        "History of Present Illness:\n"
        "72yo male reports progressive dyspnea on exertion over the past 3 weeks.\n\n"
        "Current Medications:\n"
        "- Lisinopril 20 mg daily\n"
        "- Atorvastatin 40 mg daily\n\n"
        "Labs:\n"
        "BNP 450, Creatinine 1.1, Potassium 4.2.\n\n"
        "Assessment & Plan:\n"
        "1. Decompensated heart failure with preserved ejection fraction.\n"
        "2. Add Lasix 20 mg PO daily."
    )

    parsed = parse_clinical_sections(chart_text)
    assert len(parsed.sections) == 5

    cc = parsed.get_section("chief_complaint")
    assert cc is not None
    assert "Exertional shortness of breath" in cc.content

    hpi = parsed.get_section("history_of_present_illness")
    assert hpi is not None
    assert "72yo male reports" in hpi.content

    meds = parsed.get_section("medications")
    assert meds is not None
    assert "Lisinopril" in meds.content

    plan = parsed.get_section("assessment_and_plan")
    assert plan is not None
    assert "Decompensated heart failure" in plan.content


def test_section_export_markdown():
    chart_text = (
        "Chief Complaint: Chest pain\n\n"
        "Subjective: Sudden onset substernal pressure.\n\n"
        "Assessment & Plan: Rule out ACS."
    )
    parsed = parse_clinical_sections(chart_text)
    md = parsed.to_markdown()
    assert "## Chief Complaint" in md
    assert "## Subjective" in md
    assert "## Assessment & Plan" in md


def test_section_export_json():
    chart_text = (
        "Chief Complaint: Cough\n\n"
        "Vitals: BP 120/80, HR 70\n\n"
        "Plan: Supportive care"
    )
    parsed = parse_clinical_sections(chart_text)
    json_str = parsed.to_json()
    data = json.loads(json_str)
    assert "domains" in data
    assert "chief_complaint" in data["domains"]
    assert "vitals" in data["domains"]
    assert "assessment_and_plan" in data["domains"]


def test_section_export_llm_xml():
    chart_text = (
        "Chief Complaint: Fever\n\n"
        "Medications: Tylenol 650mg\n\n"
        "Assessment & Plan: Viral syndrome"
    )
    parsed = parse_clinical_sections(chart_text)
    xml = parsed.to_llm_xml()
    assert "<patient_chart>" in xml
    assert "<chief_complaint>" in xml
    assert "<active_medications>" in xml
    assert "<assessment_and_plan>" in xml
    assert "</patient_chart>" in xml
