"""Typed validation records and the proposal validator.

:class:`Coverage` says how much of the input the checks actually processed —
as a union of ranges, so overlap cannot inflate it — and which ranges remain
unresolved. Its ``status`` is ``complete``, ``partial``, ``unavailable`` or
``failed``; a check that crashed is ``failed``, never silently complete, and
nothing processed is ``unavailable``, never a perfect score.

:func:`validate_proposal` runs the checks a :class:`RuleContract` requires over
one :class:`~chartcleaner.edit_provenance.EditProposal`, evaluated on the
affected *lines* of the current text (a removed "No" only means something in
the sentence it came from), and returns a :class:`ValidationReport` whose
``decision`` is the shared enum ``accept`` / ``retain_original`` /
``withhold_release``. The initial checks adapt the existing clinical-facts and
meaning guards and report their limits honestly: with no statement index yet,
a deletion that removes no recognized value is *unverified*, not approved.

Issue messages are generic; persisted summaries (:meth:`ValidationReport.summary`)
carry codes and counts, never chart text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import fact_check as fc
from .edit_provenance import DECISIONS, EditLedger, EditProposal, SourceSpan, merge_spans, union_length
from .rule_contracts import RuleContract, default_contract

__all__ = [
    "COVERAGE_STATUSES", "DECISIONS", "SEVERITIES", "VALIDATOR_VERSION", "Coverage",
    "ValidationContext", "ValidationIssue", "ValidationReport", "ValidationSession",
    "decide_report", "off_report", "validate_candidate", "validate_proposal",
]

COVERAGE_STATUSES = ("complete", "partial", "unavailable", "failed")
SEVERITIES = ("info", "warning", "error", "critical")
MODES = ("off", "shadow", "enforce")
VALIDATOR_VERSION = 1
# The ``cleaning_validation`` config group's schema; a newer number in a user
# config means a newer build wrote it, which the validator reports as an error.
SCHEMA_VERSION = 1

# Version of each check: bumping one invalidates cached verdicts and review
# acknowledgements that were made under the older behaviour.
CHECK_VERSIONS: dict[str, int] = {
    "content_preserved": 1, "facts": 1, "meaning": 1, "introduced": 1, "scope": 0, "statements": 0,
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _digest(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


# --- records -----------------------------------------------------------------

@dataclass(frozen=True)
class Coverage:
    status: str
    input_chars: int
    processed_chars: int
    recognized_statements: int
    checked_statements: int
    unresolved_spans: tuple[SourceSpan, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in COVERAGE_STATUSES:
            raise ValueError(f"status must be one of {COVERAGE_STATUSES}")

    @classmethod
    def from_spans(cls, input_chars: int, processed: Iterable[tuple[int, int]], *,
                   unresolved: Iterable[tuple[int, int]] = (), recognized: int = 0,
                   checked: int = 0, reasons: Iterable[str] = (), failed: bool = False) -> "Coverage":
        """Build coverage from processed/unresolved ranges (overlap counted once)."""
        processed_chars = min(union_length(processed), max(input_chars, 0))
        unresolved_spans = merge_spans(unresolved)
        reasons_t = tuple(dict.fromkeys(str(r) for r in reasons))
        if failed:
            status = "failed"
        elif processed_chars <= 0:
            status = "unavailable"
        elif processed_chars >= input_chars and not unresolved_spans:
            status = "complete"
        else:
            status = "partial"
        return cls(status, input_chars, processed_chars, recognized, checked, unresolved_spans,
                   reasons_t)

    @property
    def processed_fraction(self) -> float | None:
        if self.input_chars <= 0:
            return None
        return round(self.processed_chars / self.input_chars, 4)

    def to_dict(self) -> dict:
        return {"status": self.status, "input_chars": self.input_chars,
                "processed_chars": self.processed_chars,
                "processed_fraction": self.processed_fraction,
                "recognized_statements": self.recognized_statements,
                "checked_statements": self.checked_statements,
                "unresolved_spans": [list(s) for s in self.unresolved_spans],
                "unresolved_chars": union_length(self.unresolved_spans),
                "reasons": list(self.reasons)}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: str
    stage_id: str
    rule_key: str | None
    source_spans: tuple[SourceSpan, ...]
    message: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")

    @property
    def blocking(self) -> bool:
        return self.severity in ("error", "critical")

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "stage_id": self.stage_id,
                "rule_key": self.rule_key, "source_spans": [list(s) for s in self.source_spans],
                "message": self.message}


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    coverage: Coverage
    decision: str

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def blocking(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.blocking)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "coverage": self.coverage.to_dict(),
                "issues": [i.to_dict() for i in self.issues]}

    def summary(self) -> dict:
        """Codes and counts only — no messages, no chart text."""
        by_code: dict[str, int] = {}
        for i in self.issues:
            by_code[i.code] = by_code.get(i.code, 0) + 1
        return {"decision": self.decision, "status": self.coverage.status,
                "issues": len(self.issues), "blocking": len(self.blocking),
                "by_code": dict(sorted(by_code.items())),
                "processed_chars": self.coverage.processed_chars,
                "input_chars": self.coverage.input_chars,
                "unresolved_chars": union_length(self.coverage.unresolved_spans),
                "recognized_statements": self.coverage.recognized_statements,
                "checked_statements": self.coverage.checked_statements}


@dataclass
class ValidationContext:
    """Everything a check may read: the immutable source, digests, the run's ledger."""

    source: str
    source_digest: str
    config_digest: str
    ledger: EditLedger
    mode: str = "shadow"
    statement_index: Any = None  # supplied by the clinical-statements task
    check_versions: dict[str, int] = field(default_factory=lambda: dict(CHECK_VERSIONS))
    stage_id: str = ""

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")

    @classmethod
    def for_source(cls, source: str, *, config: dict, ledger: EditLedger | None = None,
                   mode: str = "shadow") -> "ValidationContext":
        return cls(source=source, source_digest=_sha(source), config_digest=_digest(config),
                   ledger=ledger or EditLedger(source), mode=mode)

    @property
    def validator_version(self) -> str:
        parts = ",".join(f"{k}={v}" for k, v in sorted(self.check_versions.items()))
        return f"{VALIDATOR_VERSION}:{parts}"


# --- checks --------------------------------------------------------------------

@dataclass
class _Check:
    issues: list[ValidationIssue] = field(default_factory=list)
    unresolved: list[tuple[int, int]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    recognized: int = 0
    checked: int = 0


@dataclass(frozen=True)
class _Lines:
    """The affected lines of the current text before and after the edit."""

    before: str
    after: str
    fragment_before: str
    fragment_after: str


def _affected_lines(proposal: EditProposal, ledger: EditLedger) -> _Lines:
    text = ledger.current
    if proposal.epoch != ledger.epoch:
        # The ledger moved on (an earlier batch was applied); fall back to the
        # fragments themselves — still honest, just less context.
        return _Lines(proposal.before, proposal.after, proposal.before, proposal.after)
    ls = text.rfind("\n", 0, proposal.start) + 1
    le = text.find("\n", proposal.end)
    le = len(text) if le == -1 else le
    before = text[ls:le]
    after = text[ls:proposal.start] + proposal.after + text[proposal.end:le]
    return _Lines(before, after, proposal.before, proposal.after)


def _issue(code: str, severity: str, proposal: EditProposal, message: str) -> ValidationIssue:
    return ValidationIssue(code, severity, proposal.stage_id, proposal.rule_key,
                           proposal.source_spans, message)


def _check_content_preserved(proposal: EditProposal, contract: RuleContract,
                             context: ValidationContext, lines: _Lines) -> _Check:
    out = _Check()
    if lines.before.split() == lines.after.split():
        out.reasons.append("content_preserved")
    else:
        out.issues.append(_issue("content_changed", "error", proposal,
                                 "A formatting rule changed more than whitespace"))
    return out


def _loss_allowed(key: str, contract: RuleContract) -> bool:
    return any(key == a or key.startswith(a) for a in contract.allowed_losses)


def _check_facts(proposal: EditProposal, contract: RuleContract, context: ValidationContext,
                 lines: _Lines) -> _Check:
    out = _Check()
    if lines.before.split() == lines.after.split():
        return out  # whitespace-only: nothing to lose
    before = fc.fact_counts(lines.before)
    after = fc.fact_counts(lines.after)
    out.recognized = sum(before.values())
    out.checked = out.recognized
    lost = {k: v - after.get(k, 0) for k, v in before.items() if v > after.get(k, 0)}
    if contract.operation == "abbreviate":
        lost = {k: v for k, v in lost.items() if k.startswith("n:")}  # words become short forms
    if lost and proposal.stage_id in fc.DEDUP_STAGES and proposal.epoch == context.ledger.epoch:
        # A folded duplicate keeps its value elsewhere in the output; only a
        # value that vanishes from the whole text counts.
        cur = context.ledger.current
        whole = fc.fact_counts(cur[:proposal.start] + proposal.after + cur[proposal.end:])
        lost = {k: v for k, v in lost.items() if not whole.get(k)}
    intentional = {k: v for k, v in lost.items() if _loss_allowed(k, contract)}
    unexpected = {k: v for k, v in lost.items() if k not in intentional}
    if intentional:
        out.issues.append(_issue("intentional_loss", "info", proposal,
                                 f"{sum(intentional.values())} value(s) removed under the rule's allowed-loss policy"))
    if unexpected:
        out.issues.append(_issue("fact_lost", "error", proposal,
                                 f"{sum(unexpected.values())} clinical value(s) removed"))
    return out


def _check_meaning(proposal: EditProposal, contract: RuleContract, context: ValidationContext,
                   lines: _Lines) -> _Check:
    out = _Check()
    if proposal.stage_id in fc.BY_DESIGN_STAGES or proposal.stage_id in fc.DEDUP_STAGES:
        # Summaries and folded duplicates rewrite freely; the facts check (with
        # its by-design policy) is what applies to them — same as today's guard.
        return out
    if lines.before.split() == lines.after.split():
        return out  # whitespace-only: no word moved, no meaning changed
    for change in fc.meaning_changes(lines.before, lines.after, proposal.stage_id):
        code = "negation_lost" if change.kind == "negation" else "laterality_changed"
        out.issues.append(_issue(code, "critical", proposal, change.message))
    # Lines that vanished outright (not rewritten) are not paired by the guard
    # above; a vanished negation or side is still a meaning loss.
    a, b = lines.before.split("\n"), lines.after.split("\n")
    olds, news = fc._surplus(a, b), fc._surplus(b, a)
    removed: list[str] = []
    if not news:
        removed = olds
    elif olds:
        import difflib
        sm = difflib.SequenceMatcher(a=olds, b=news, autojunk=False)
        for tag, i1, i2, _j1, _j2 in sm.get_opcodes():
            if tag == "delete":
                removed.extend(olds[i1:i2])
    if any(fc._negations(ln) or fc._sides(ln) for ln in removed):
        out.issues.append(_issue("negated_statement_removed", "error", proposal,
                                 "A line carrying a negation or side was removed"))
    return out


_ANY_NUMBER = re.compile(r"(?<![\w.])[<>]?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def _check_introduced(proposal: EditProposal, contract: RuleContract,
                      context: ValidationContext, lines: _Lines) -> _Check:
    out = _Check()
    if not any(c.isdigit() for c in lines.after):
        return out
    before = {fc._norm_number(m.group(1)) for m in _ANY_NUMBER.finditer(lines.before)}
    new = {fc._norm_number(m.group(1)) for m in _ANY_NUMBER.finditer(lines.after)} - before
    if not new:
        return out
    source = {fc._norm_number(m.group(1)) for m in _ANY_NUMBER.finditer(context.source)}
    foreign = new - source
    if foreign:
        out.issues.append(_issue("value_introduced", "error", proposal,
                                 f"{len(foreign)} number(s) appeared that are nowhere in the chart"))
    return out


def _check_scope(proposal: EditProposal, contract: RuleContract, context: ValidationContext,
                 lines: _Lines) -> _Check:
    out = _Check()
    if contract.sections or contract.note_types or not contract.allow_cross_section:
        if context.check_versions.get("scope", 0) < 1:
            out.reasons.append("scope_unchecked")  # the rule-contract task implements it
    return out


def _check_statements(proposal: EditProposal, contract: RuleContract,
                      context: ValidationContext, lines: _Lines) -> _Check:
    out = _Check()
    if context.statement_index is None:
        out.reasons.append("statements_unavailable")
        out.unresolved.append(tuple(proposal.enclosing))
    return out


CheckFn = Callable[[EditProposal, RuleContract, ValidationContext, _Lines], _Check]
_CHECKS: dict[str, CheckFn] = {
    "content_preserved": _check_content_preserved,
    "facts": _check_facts,
    "meaning": _check_meaning,
    "introduced": _check_introduced,
    "scope": _check_scope,
    "statements": _check_statements,
}


# --- decision ------------------------------------------------------------------

def decide_report(issues: Iterable[ValidationIssue], coverage: Coverage,
                  contract: RuleContract) -> str:
    """The shared decision for one proposal (see the design's decision table)."""
    issues = tuple(issues)
    if coverage.status == "failed":
        return "retain_original"
    if any(i.blocking for i in issues):
        return "retain_original"
    if coverage.unresolved_spans and not contract.reviewed:
        return "retain_original"  # missing evidence: keep the passage, ask for review
    return "accept"


def validate_proposal(proposal: EditProposal, policy: RuleContract,
                      context: ValidationContext) -> ValidationReport:
    """Run ``policy.required_checks`` over ``proposal`` and decide."""
    lines = _affected_lines(proposal, context.ledger)
    processed = [tuple(s) for s in proposal.source_spans if s.end > s.start]
    if not processed:
        processed = [tuple(proposal.enclosing)]  # an insertion anchor: zero chars
    input_chars = max(union_length(processed), 1 if proposal.before else 0)
    issues: list[ValidationIssue] = []
    unresolved: list[tuple[int, int]] = []
    reasons: list[str] = []
    recognized = checked = 0
    failed = False
    for name in policy.required_checks:
        fn = _CHECKS.get(name)
        if fn is None:
            reasons.append(f"{name}_unavailable")
            unresolved.append(tuple(proposal.enclosing))
            continue
        try:
            result = fn(proposal, policy, context, lines)
        except Exception as e:  # a crashed check is a failed check, never a pass
            failed = True
            reasons.append("validator_failed")
            issues.append(_issue("validator_failed", "error", proposal,
                                 f"Check '{name}' failed: {type(e).__name__}"))
            continue
        issues.extend(result.issues)
        unresolved.extend(result.unresolved)
        reasons.extend(result.reasons)
        recognized = max(recognized, result.recognized)
        checked = max(checked, result.checked)
    if proposal.is_removal and context.statement_index is None and "content_preserved" not in policy.required_checks:
        # No statement extractor yet: what the removed text *meant* is unchecked,
        # whatever the cheaper checks found. Processing coverage is full; relation
        # coverage is not, so the span stays unresolved and the status partial.
        unresolved.append(tuple(proposal.enclosing))
        reasons.append("statements_unavailable")
        issues.append(_issue("deletion_unverified", "warning", proposal,
                             "Removed text could not be verified against recognized statements"))
    coverage = Coverage.from_spans(input_chars, processed, unresolved=unresolved,
                                   recognized=recognized, checked=checked, reasons=reasons,
                                   failed=failed)
    report = ValidationReport(tuple(issues), coverage, decide_report(issues, coverage, policy))
    proposal.report = report
    return report


def validate_candidate(source: str, candidate: str, cfg: dict, *, mode: str = "shadow",
                       stage_id: str = "candidate") -> ValidationReport:
    """Judge a whole candidate text against ``source`` as one coarse proposal.

    The benchmark and whole-document callers use this before the incremental
    document validator exists; the window of difference is one proposal under a
    generic ``reduce`` contract (facts, meaning, introduced numbers).
    """
    ledger = EditLedger(source)
    context = ValidationContext.for_source(source, config=cfg, ledger=ledger, mode=mode)
    if candidate == source:
        return ValidationReport((), Coverage.from_spans(len(source), [(0, len(source))],
                                                        reasons=("unchanged",)), "accept")
    limit = min(len(source), len(candidate))
    a = 0
    while a < limit and source[a] == candidate[a]:
        a += 1
    b = 0
    while b < limit - a and source[-1 - b] == candidate[-1 - b]:
        b += 1
    proposal = ledger.propose(stage_id, None, a, len(source) - b, candidate[a:len(candidate) - b],
                              mapping="coarse")
    return validate_proposal(proposal, RuleContract(key="candidate", operation="reduce"), context)


def off_report(input_chars: int) -> ValidationReport:
    """The report for ``mode = off``: nothing checked, said plainly."""
    return ValidationReport((), Coverage.from_spans(input_chars, [], reasons=("validation_off",)),
                            "accept")


def _worst(decisions: Iterable[str]) -> str:
    seen = set(decisions)
    for d in ("withhold_release", "retain_original"):
        if d in seen:
            return d
    return "accept"


class ValidationSession:
    """Run-local judge: validates every proposal and aggregates the run report.

    In ``shadow`` mode :meth:`judge` always answers ``accept`` (legacy output
    ships) while recording what enforcement would have decided; in
    ``enforce`` it returns the report's decision. ``max_proposals`` bounds the
    work: proposals past the budget are recorded by the ledger but left
    unchecked, and the run report says so (``budget_exhausted``, partial
    coverage) instead of looking complete.
    """

    def __init__(self, context: ValidationContext, *, contracts: dict[str, RuleContract] | None = None,
                 max_proposals: int | None = None):
        self.context = context
        self.contracts = contracts or {}
        self.max_proposals = max_proposals
        self.reports: list[tuple[EditProposal, ValidationReport]] = []
        self.unvalidated: list[EditProposal] = []

    def contract_for(self, proposal: EditProposal) -> RuleContract:
        found = self.contracts.get(proposal.rule_key) if proposal.rule_key else None
        return found or default_contract(proposal.stage_id, proposal.rule_key)

    def judge(self, proposal: EditProposal) -> str:
        if self.max_proposals is not None and len(self.reports) >= self.max_proposals:
            self.unvalidated.append(proposal)
            # Missing evidence: shadow ships the text anyway; enforcement keeps the passage.
            return "retain_original" if self.context.mode == "enforce" else "accept"
        report = validate_proposal(proposal, self.contract_for(proposal), self.context)
        self.reports.append((proposal, report))
        return report.decision if self.context.mode == "enforce" else "accept"

    def report(self) -> ValidationReport:
        source_len = len(self.context.source)
        skipped = merge_spans(tuple(p.enclosing) for p in self.unvalidated)
        processed: list[tuple[int, int]] = []
        pos = 0
        for span in skipped:
            if span.start > pos:
                processed.append((pos, span.start))
            pos = max(pos, span.end)
        if pos < source_len:
            processed.append((pos, source_len))
        issues: list[ValidationIssue] = []
        unresolved: list[tuple[int, int]] = []
        reasons: list[str] = []
        recognized = checked = 0
        failed = False
        for _p, rep in self.reports:
            issues.extend(rep.issues)
            unresolved.extend(tuple(s) for s in rep.coverage.unresolved_spans)
            reasons.extend(rep.coverage.reasons)
            recognized += rep.coverage.recognized_statements
            checked += rep.coverage.checked_statements
            failed = failed or rep.coverage.status == "failed"
        if self.unvalidated:
            reasons.append("budget_exhausted")
        coverage = Coverage.from_spans(source_len, processed, unresolved=unresolved,
                                       recognized=recognized, checked=checked, reasons=reasons,
                                       failed=failed)
        decision = _worst(rep.decision for _p, rep in self.reports)
        if self.unvalidated and decision == "accept":
            decision = "retain_original"  # unchecked edits are not accepted edits
        if coverage.status == "failed" and decision == "accept":
            decision = "retain_original"
        return ValidationReport(tuple(issues), coverage, decision)
