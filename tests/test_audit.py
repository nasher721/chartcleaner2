"""Tests for the post-run audit (chartcleaner/audit.py)."""

import pytest

from chartcleaner.audit import (
    DEFAULT_AUDIT_CONFIG,
    AuditResult,
    ensure_audit_section,
    get_audit_config,
    run_audit,
    suggestion_for_signature,
)

BASE_CONFIG = {
    "emr_line_metadata": [],
    "boilerplate": [],
    "epic_phi_patterns": [],
    "literal_replacements": [],
    "clinical_headers": ["Subjective"],
}


def audit(text: str, audit_section: dict | None = None, **extra) -> AuditResult:
    cfg = dict(BASE_CONFIG)
    if audit_section is not None:
        cfg["audit"] = audit_section
    cfg.update(extra)
    return run_audit(text, cfg)


def sigs(result: AuditResult) -> set[str]:
    return {f.signature for f in result.findings}


def count(result: AuditResult, check: str) -> int:
    """counts includes zero entries for every enabled check."""
    return result.counts.get(check, 0)


# -- long digits --------------------------------------------------------------

def test_long_digits_flagged():
    r = audit("MRN 84920173 on file\nAccession 559301882")
    assert r.counts.get("long_digits") == 2
    assert "long_digits" in sigs(r)


def test_long_digits_safe_negatives():
    r = audit("In 2024 the guidelines changed.\nHR 72, BP 138/76\nTemp 36.8 C\nNote Type: Progress")
    assert count(r, "long_digits") == 0


def test_long_digits_min_digits_option():
    r = audit("MRN 12345", {"checks": {"long_digits": {"enabled": True, "min_digits": 4}}})
    assert r.counts.get("long_digits") == 1
    r2 = audit("MRN 12345", {"checks": {"long_digits": {"enabled": True, "min_digits": 6}}})
    assert count(r2, "long_digits") == 0


# -- date-like ----------------------------------------------------------------

def test_dob_line_flagged():
    r = audit("DOB: 03/14/1968")
    assert r.counts.get("date_like") == 1
    assert "date_like" in sigs(r)


def test_non_birth_dates_ignored():
    r = audit("Procedure Date: 09/14/2026 at 22:10\nDate of Service: 09/15/2026")
    assert count(r, "date_like") == 0


def test_dob_without_digits_ignored():
    r = audit("DOB: unknown")
    assert count(r, "date_like") == 0


# -- phone / email ------------------------------------------------------------

def test_phone_and_email_flagged():
    r = audit("call (555) 201-8834 or write jane.doe@example.com")
    assert r.counts.get("phone_email") == 2
    assert {"phone_email:phone", "phone_email:email"} <= sigs(r)


def test_vital_signs_not_flagged_as_phone():
    r = audit("BP 138/76, RR 16, SpO2 98% on room air\nTemp 36.8 C")
    assert count(r, "phone_email") == 0


def test_fluid_balance_numbers_not_flagged_as_phone():
    r = audit("Fluid Balance: 1250 500 1243\nIntake 1000 Output 500 Net +500")
    assert count(r, "phone_email") == 0


# -- label names ----------------------------------------------------------------

def test_label_value_flagged():
    r = audit("Patient: John Smith\nNext of Kin: DOE, JOHN (spouse)")
    assert r.counts.get("label_names") == 2
    assert "label_names:patient" in sigs(r)


def test_redacted_or_junk_label_values_ignored():
    r = audit("Patient: [REDACTED_NAME]\nName: 123456\nContact: unknown\nDOB: 03/14/1968")
    assert count(r, "label_names") == 0


def test_label_names_custom_labels():
    r = audit("Guarantor: SMITH, ROBERT",
              {"checks": {"label_names": {"enabled": True, "labels": ["Guarantor"]}}})
    assert r.counts.get("label_names") == 1


# -- residual chrome ------------------------------------------------------------

def test_chrome_lines_flagged():
    r = audit("See notes below.\nVersion 2 of 3\nEditor: Fake, Doctor A. MD")
    assert r.counts.get("residual_chrome") == 2


def test_chrome_rule_suggestion_is_a_working_regex():
    r = audit("Filed: 09/15/2026 0712")
    f = r.findings[0]
    assert f.suggested_stage == "emr_line_metadata"
    import re
    assert re.search(f.suggested_regex, "Filed: 01/02/2003 0405")
    assert not re.search(f.suggested_regex, "totally ordinary clinical text")


# -- structure & safety -----------------------------------------------------------

def test_cap_limits_findings_but_counts_stay_true():
    text = "\n".join(f"MRN {800000 + i}" for i in range(60))
    r = audit(text)
    assert r.counts["long_digits"] == 60
    assert len(r.findings) == DEFAULT_AUDIT_CONFIG["max_findings"]


def test_one_broken_check_does_not_break_others():
    r = audit("MRN 84920173", {"checks": {
        "residual_chrome": {"enabled": True, "patterns": ["(?im)^Version[unclosed"]},
    }})
    assert r.counts.get("long_digits") == 1
    assert any("residual_chrome" in e or "chrome" in e.lower() for e in r.errors)


def test_audit_disabled_skips_everything():
    r = audit("MRN 84920173", {"enabled": False})
    assert r.skipped and not r.findings


def test_individual_check_disabled():
    r = audit("MRN 84920173\nDOB: 03/14/1968",
              {"checks": {"long_digits": {"enabled": False}}})
    assert count(r, "long_digits") == 0
    assert r.counts.get("date_like") == 1


def test_empty_text_skips():
    r = audit("   \n  ")
    assert r.skipped and not r.findings


# -- config merging -----------------------------------------------------------------

def test_get_audit_config_merges_over_defaults():
    merged = get_audit_config({"audit": {"enabled": False,
                                         "checks": {"long_digits": {"min_digits": 9}}}})
    assert merged["enabled"] is False
    assert merged["checks"]["long_digits"]["min_digits"] == 9
    assert merged["checks"]["long_digits"]["enabled"] is True  # default kept
    assert merged["checks"]["date_like"]["enabled"] is True


def test_get_audit_config_ignores_garbage():
    merged = get_audit_config({"audit": {"enabled": "yes", "max_findings": -3,
                                         "checks": {"bogus": {"enabled": True}}}})
    assert merged["enabled"] is True
    assert merged["max_findings"] == DEFAULT_AUDIT_CONFIG["max_findings"]
    assert "bogus" not in merged["checks"]


def test_ensure_audit_section_populates_for_ui():
    cfg: dict = {}
    section = ensure_audit_section(cfg)
    assert set(section["checks"]) == set(DEFAULT_AUDIT_CONFIG["checks"])
    assert cfg["audit"] is section


# -- suggestions ------------------------------------------------------------------

@pytest.mark.parametrize("sig,stage", [
    ("long_digits", "phi_patterns"),
    ("date_like", "phi_patterns"),
    ("phone_email:phone", "phi_patterns"),
    ("phone_email:email", "phi_patterns"),
    ("label_names:patient name", "phi_patterns"),
    ("residual_chrome:filed: 9/9/9 9", "emr_line_metadata"),
    ("something_new", "phi_patterns"),
])
def test_suggestion_for_signature_covers_known_shapes(sig, stage):
    payload = suggestion_for_signature(sig)
    assert payload["stage"] == stage
    assert payload["title"]
