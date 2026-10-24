from types import SimpleNamespace as NS
from app_pages.review_model import build_finding_rows, default_review_view, filter_findings


def _result(validation=None, fact_check=None):
    return NS(validation=validation, fact_check=fact_check, stages=[], text="output")


def test_blocking_findings_sort_first_and_duplicates_keep_occurrences():
    report = NS(losses=[
        NS(category="unexpected", display="K 6.1", stage_label="Rules", lines=["K 6.1"]),
        NS(category="unexpected", display="K 6.1", stage_label="Rules", lines=["K 6.1 again"]),
    ], meaning=[
        NS(category="review", message="Meaning changed", stage_label="Rules",
           before="No AKI", after="AKI"),
    ], introduced=[], implausible=[], implausible_in_source=set())
    rows = build_finding_rows(_result(fact_check=report), None, source="K 6.1", output="")
    assert [row.severity for row in rows] == ["blocked", "review"]
    assert rows[0].count == 2
    assert len(rows[0].occurrences) == 2
    assert default_review_view(rows) == "findings"
    assert filter_findings(rows, "blocking") == rows[:1]
    assert filter_findings(rows, "review") == rows
    assert filter_findings(rows, "all") == rows


def test_validation_coverage_and_retained_edits_are_review_not_release_logic():
    issue = NS(blocking=False, code="scope_changed", stage_id="rules", rule_key="r1",
               message="Edit exceeds its declared scope", source_spans=[])
    proposal = NS(decision="retain_original", stage_id="rules", rule_key="r1",
                  mapping="exact", source_spans=(), before="Plan: aspirin", after="")
    coverage = NS(status="partial", reasons=())
    validation = NS(issues=[issue], coverage=coverage)
    result = _result(validation=validation)
    result.ledger = NS(proposals=[proposal])
    rows = build_finding_rows(result, None, source="Plan: aspirin", output="")
    assert all(row.severity == "review" for row in rows)
    assert {row.kind for row in rows} >= {"scope_changed", "edits_retained", "coverage_partial"}
    assert filter_findings(rows, "info") == []


def test_retained_edit_preserves_nonempty_proposed_after():
    proposal = NS(decision="retain_original", stage_id="rules", rule_key="r1",
                  mapping="exact", source_spans=(), before="Plan: aspirin",
                  after="Plan: aspirin 81 mg")
    validation = NS(issues=[], coverage=NS(status="complete", reasons=()))
    result = _result(validation=validation)
    result.ledger = NS(proposals=[proposal])
    rows = build_finding_rows(result, None, source="Plan: aspirin", output="")
    retained = next(row for row in rows if row.kind == "edits_retained")
    assert retained.occurrences[0]["before"] == "Plan: aspirin"
    assert retained.occurrences[0]["after"] == "Plan: aspirin 81 mg"


def test_no_actionable_findings_defaults_to_changes():
    assert default_review_view([]) == "changes"
    assert default_review_view(filter_findings([], "all")) == "changes"
    assert default_review_view([NS(severity="info"), NS(severity="info")]) == "changes"


def test_same_validation_code_from_different_rules_never_merges():
    issues = [
        NS(blocking=False, code="scope_changed", stage_id="rules", rule_key=key,
           message="Edit exceeds its declared scope", source_spans=[])
        for key in ("rule-a", "rule-b")
    ]
    validation = NS(issues=issues, coverage=NS(status="complete", reasons=()))
    rows = build_finding_rows(_result(validation=validation), None,
                              source="Plan: aspirin", output="")
    scoped = [row for row in rows if row.kind == "scope_changed"]
    assert len(scoped) == 2
    assert {row.rule_key for row in scoped} == {"rule-a", "rule-b"}
    assert all(row.count == 1 for row in scoped)
