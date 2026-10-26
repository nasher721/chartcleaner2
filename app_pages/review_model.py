"""Presentation-only projection of review findings into grouped rows."""
from dataclasses import dataclass

from chartcleaner.review_state import review_findings, support_text

_SEVERITY = {"blocked": 0, "review": 1, "info": 2}
MAX_BEFORE_CHARS = 2000  # source context kept per validation occurrence


@dataclass(frozen=True)
class FindingRow:
    kind: str
    severity: str
    title: str
    detail: str
    stage_id: str
    rule_key: str | None
    occurrences: tuple[dict, ...]

    @property
    def count(self) -> int:
        return len(self.occurrences)


def _group(items: list[dict]) -> list[FindingRow]:
    grouped = {}
    for item in items:
        key = (item["kind"], item["severity"], item["title"], item["detail"],
               item.get("stage_id", ""), item.get("rule_key"))
        grouped.setdefault(key, []).append(item)
    rows = [FindingRow(*key, tuple(values)) for key, values in grouped.items()]
    return sorted(rows, key=lambda row: (_SEVERITY[row.severity], row.title, row.detail,
                                         row.stage_id, row.rule_key or ""))


def _coverage_detail(result, coverage) -> str:
    required = ("input_chars", "processed_chars", "recognized_statements",
                "checked_statements", "unresolved_spans")
    if all(hasattr(coverage, name) for name in required):
        return support_text(result)
    return f"Validation coverage: {coverage.status}."


def build_finding_rows(result, audit, *, source: str, output: str) -> list[FindingRow]:
    items = []
    for finding in review_findings(result.fact_check, audit, source=source, output=output):
        items.append({**finding, "kind": "clinical_or_identifier",
                      "stage_id": "", "rule_key": None})
    validation = getattr(result, "validation", None)
    if validation is None:
        return _group(items)
    labels = {stage.id: stage.label for stage in result.stages}
    for issue in validation.issues:
        spans = tuple(span for span in issue.source_spans if span.end > span.start)
        # Capped: a coarse span can cover most of a long chart, and copying it
        # once per issue made building the Review tab quadratic.
        before = (source[spans[0].start:min(spans[0].end, spans[0].start + MAX_BEFORE_CHARS)]
                  if spans else "")
        items.append(dict(kind=issue.code,
                          severity="blocked" if issue.blocking else "review",
                          title=issue.message,
                          detail=labels.get(issue.stage_id, issue.stage_id),
                          stage_id=issue.stage_id, rule_key=issue.rule_key,
                          source_spans=spans, mapping="exact" if spans else "unavailable",
                          before=before, after=""))
    proposals = getattr(getattr(result, "ledger", None), "proposals", ())
    for proposal in proposals:
        if proposal.decision != "retain_original":
            continue
        items.append(dict(kind="edits_retained", severity="review",
                          title="Edit kept as original",
                          detail=labels.get(proposal.stage_id, proposal.stage_id),
                          stage_id=proposal.stage_id, rule_key=proposal.rule_key,
                          source_spans=proposal.source_spans, mapping=proposal.mapping,
                          before=proposal.before, after=proposal.after))
    coverage = validation.coverage
    if coverage.status != "complete":
        items.append(dict(kind=f"coverage_{coverage.status}", severity="review",
                          title=f"Validation coverage: {coverage.status}",
                          detail=_coverage_detail(result, coverage), stage_id="", rule_key=None,
                          source_spans=(), mapping="unavailable", before="", after=""))
    return _group(items)


def finding_count(rows: list[FindingRow]) -> int:
    """The single presentation count used by both the badge and workspace."""
    return sum(row.count for row in rows)


def filter_findings(rows: list[FindingRow], mode: str) -> list[FindingRow]:
    if mode == "blocking":
        return [row for row in rows if row.severity == "blocked"]
    if mode == "info":
        return [row for row in rows if row.severity == "info"]
    if mode == "review":
        return [row for row in rows if row.severity in ("blocked", "review")]
    return list(rows)


def default_review_view(rows: list[FindingRow]) -> str:
    return "findings" if any(row.severity != "info" for row in rows) else "changes"
