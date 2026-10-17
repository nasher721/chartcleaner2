from chartcleaner.fact_check import FactLoss, FactReport
from chartcleaner.review_state import review_findings, review_status, redaction_candidates


def test_critical_findings_override_clear_pattern_scan():
    report = FactReport(1, [FactLoss('dose', '1,000 mcg', 1, 'whitespace', 'Whitespace',
                                   'unexpected', ['cyanocobalamin 1,000 mcg tab'])])
    assert review_status(report, None) == 'blocked'
    items = review_findings(report, None)
    assert items[0]['severity'] == 'blocked'
    assert '1,000 mcg' in items[0]['title']
    assert items[0]['before'] == 'cyanocobalamin 1,000 mcg tab'


def test_missing_verification_never_claims_clean():
    assert review_status(None, None) == 'review'


def test_redaction_candidates_identify_individual_occurrences():
    before = 'John Smith\nJane Doe'
    after = '[REDACTED_NAME]\n[REDACTED_NAME]'
    items = redaction_candidates(before, after)
    assert [i['original'] for i in items] == ['John Smith', 'Jane Doe']
    assert after[items[1]['start']:items[1]['end']] == '[REDACTED_NAME]'


def test_wrapped_output_redaction_mapping():
    items = redaction_candidates('John Smith\nDose 5 mg', '<patient_chart>\n[REDACTED_NAME]\nDose 5 mg\n</patient_chart>')
    assert items[0]['original'] == 'John Smith'


def test_audit_location_uses_real_line_instead_of_truncated_excerpt():
    from chartcleaner.audit import AuditResult, Finding
    text = 'Call patient@example.com for the appointment.'
    audit = AuditResult(findings=[Finding('email', 1, 'EMAIL: patient@…', 'id')])
    item = review_findings(FactReport(0, []), audit, source=text, output=text)[0]
    assert item['before'] == text
    assert item['after'] == text
