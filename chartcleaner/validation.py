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

import bisect
import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

from . import fact_check as fc
from .edit_provenance import DECISIONS, EditLedger, EditProposal, SourceSpan, merge_spans, union_length
from .rule_contracts import RuleContract, default_contract

__all__ = [
    "COVERAGE_STATUSES", "DECISIONS", "SEVERITIES", "VALIDATOR_VERSION", "Coverage",
    "ValidationContext", "ValidationIssue", "ValidationReport", "ValidationSession",
    "decide_edit", "decide_report", "off_report", "validate_candidate", "validate_proposal",
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
    "content_preserved": 1, "facts": 1, "meaning": 1, "introduced": 1, "scope": 1, "statements": 1,
    "do_not_use": 1,
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _digest(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


def validator_version(check_versions: dict[str, int] | None = None) -> str:
    """``"<validator>:<check>=<version>,…"`` — part of every acknowledgement key."""
    versions = CHECK_VERSIONS if check_versions is None else check_versions
    parts = ",".join(f"{k}={v}" for k, v in sorted(versions.items()))
    return f"{VALIDATOR_VERSION}:{parts}"


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
    # enforcement bookkeeping: proposals kept as original / withheld from release
    rejected: int = 0
    withheld: int = 0

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def blocking(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.blocking)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "coverage": self.coverage.to_dict(),
                "issues": [i.to_dict() for i in self.issues], "rejected": self.rejected,
                "withheld": self.withheld}

    def summary(self) -> dict:
        """Codes and counts only — no messages, no chart text."""
        by_code: dict[str, int] = {}
        for i in self.issues:
            by_code[i.code] = by_code.get(i.code, 0) + 1
        return {"decision": self.decision, "status": self.coverage.status,
                "issues": len(self.issues), "blocking": len(self.blocking),
                "by_code": dict(sorted(by_code.items())),
                "rejected": self.rejected, "withheld": self.withheld,
                "processed_chars": self.coverage.processed_chars,
                "input_chars": self.coverage.input_chars,
                "unresolved_chars": union_length(self.coverage.unresolved_spans),
                "recognized_statements": self.coverage.recognized_statements,
                "checked_statements": self.coverage.checked_statements}


class StatementSet:
    """Statements of the evolving accepted text, indexed once and patched per edit.

    The source is extracted once, windowed (:class:`~chartcleaner.validation_windows.WindowedIndex`),
    each statement owned exactly once with source spans. An accepted proposal
    drops the statements its source range covers and adds the statements of its
    replacement text, anchored at that range — so validating a changed region
    costs the region, not the chart.
    """

    def __init__(self, source: str, *, target_chars: int = 32_000):
        from .validation_windows import WindowedIndex
        self.source = source
        self._windowed = WindowedIndex(source, target_chars=target_chars)
        self._windowed.ensure_loaded()
        self.statements: list = self._windowed.statements()
        self.version = 0

    def apply(self, proposal: EditProposal) -> None:
        """Fold one accepted proposal into the set (source-coordinate anchored)."""
        start, end = proposal.enclosing.start, proposal.enclosing.end
        kept = [s for s in self.statements
                if not any(sp.start < end and sp.end > start for sp in s.source_spans)]
        from .clinical_statements import extract_statements
        added = extract_statements(proposal.after, source_offset=start,
                                   note_id=proposal.proposal_id).statements
        self.statements = kept + list(added)
        self.version += 1

    def apply_many(self, proposals: list[EditProposal]) -> None:
        """Same result as :meth:`apply` for each proposal in order, in one pass.

        One accepted rule or stage can fold hundreds of proposals in; applying
        them one by one rescans every statement per proposal. Original
        statements are filtered once against the union of the ranges (bisect);
        each proposal's added statements are filtered against the ranges of
        the proposals after it, exactly as sequential application would.
        """
        if not proposals:
            return
        if len(proposals) == 1:
            self.apply(proposals[0])
            return
        from .clinical_statements import extract_statements
        ranges = [(p.enclosing.start, p.enclosing.end) for p in proposals]
        union: list[list[int]] = []
        for start, end in sorted(ranges):
            if union and start < union[-1][1]:  # strictly overlapping only
                union[-1][1] = max(union[-1][1], end)
            else:
                union.append([start, end])
        ends = [end for _start, end in union]

        def hit(statement) -> bool:
            for sp in statement.source_spans:
                i = bisect.bisect_right(ends, sp.start)
                while i < len(union) and union[i][0] < sp.end:
                    if union[i][1] > sp.start:
                        return True
                    i += 1
            return False

        out = [s for s in self.statements if not hit(s)]
        for i, p in enumerate(proposals):
            later = ranges[i + 1:]
            for s in extract_statements(p.after, source_offset=p.enclosing.start,
                                        note_id=p.proposal_id).statements:
                if not any(sp.start < end and sp.end > start
                           for start, end in later for sp in s.source_spans):
                    out.append(s)
        self.statements = out
        self.version += len(proposals)

    def in_region(self, start: int, end: int) -> list:
        return [s for s in self.statements
                if any(sp.start < end and sp.end > start for sp in s.source_spans)]

    def outside(self, start: int, end: int) -> list:
        return [s for s in self.statements
                if not any(sp.start < end and sp.end > start for sp in s.source_spans)]

    def extraction(self):
        from .clinical_statements import StatementExtraction
        return StatementExtraction(tuple(self.statements), self._windowed.unresolved())

    def coverage(self):
        return self._windowed.coverage()


@dataclass
class ValidationContext:
    """Everything a check may read: the immutable source, digests, the run's ledger."""

    source: str
    source_digest: str
    config_digest: str
    ledger: EditLedger
    mode: str = "shadow"
    _statement_index: Any = field(default=None, init=False, repr=False, compare=False)
    _statement_set: Any = field(default=None, init=False, repr=False, compare=False)
    _set_version: int = field(default=-1, init=False, repr=False, compare=False)
    _pending_accepted: list = field(default_factory=list, init=False, repr=False, compare=False)
    check_versions: dict[str, int] = field(default_factory=lambda: dict(CHECK_VERSIONS))
    stage_id: str = ""
    _sections_cache: Any = field(default=None, init=False, repr=False, compare=False)
    _note_type_cache: Any = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")

    @property
    def statement_set(self) -> "StatementSet":
        """The accepted text's statements: extracted once, patched per edit."""
        if self._statement_set is None:
            self._statement_set = StatementSet(self.source)
        pending, self._pending_accepted = self._pending_accepted, []
        self._statement_set.apply_many(pending)
        if pending:
            self._statement_index = None
        return self._statement_set

    def sync_statements(self, accepted: list) -> None:
        """Queue proposals accepted since the last sync; applied on next use."""
        self._pending_accepted.extend(accepted)

    @property
    def statement_index(self):
        """The accepted text's statements, indexed for whole-document questions."""
        if self._statement_index is None or self._set_version != self.statement_set.version:
            from .clinical_statements import StatementIndex
            self._statement_index = StatementIndex(
                self.source, extraction=self.statement_set.extraction())
            self._set_version = self.statement_set.version
        return self._statement_index

    @classmethod
    def for_source(cls, source: str, *, config: dict, ledger: EditLedger | None = None,
                   mode: str = "shadow") -> "ValidationContext":
        return cls(source=source, source_digest=_sha(source), config_digest=_digest(config),
                   ledger=ledger or EditLedger(source), mode=mode)

    @property
    def validator_version(self) -> str:
        return validator_version(self.check_versions)

    # -- section / note-type context (cached per ledger epoch) -------------------

    def sections(self) -> tuple[tuple[int, int, str], ...]:
        """``(start, end, domain)`` of the clinical sections in the current text."""
        epoch = self.ledger.epoch
        if self._sections_cache is None or self._sections_cache[0] != epoch:
            from .stage_core import _section_spans
            self._sections_cache = (epoch, tuple(_section_spans(self.ledger.current)))
        return self._sections_cache[1]

    def domain_at(self, pos: int) -> str:
        for start, end, domain in self.sections():
            if start <= pos < end:
                return domain
        return "preamble"

    def note_type(self) -> str | None:
        if self._note_type_cache is None:
            from .note_type import detect
            try:
                self._note_type_cache = detect(self.source).kind or ""
            except Exception:
                self._note_type_cache = ""
        return self._note_type_cache or None


# --- checks --------------------------------------------------------------------

@dataclass
class _Check:
    issues: list[ValidationIssue] = field(default_factory=list)
    unresolved: list[tuple[int, int]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    recognized: int = 0
    checked: int = 0
    resolved: bool = False  # the check recognized and judged what the edit touched


@dataclass(frozen=True)
class _Lines:
    """The affected lines of the current text before and after the edit."""

    before: str
    after: str
    fragment_before: str
    fragment_after: str
    fragment_start: int = 0  # offset of the edited fragment inside ``before``


def _affected_lines(proposal: EditProposal, ledger: EditLedger) -> _Lines:
    text = ledger.current
    if proposal.epoch != ledger.epoch:
        # The ledger moved on (an earlier batch was applied); fall back to the
        # fragments themselves — still honest, just less context.
        return _Lines(proposal.before, proposal.after, proposal.before, proposal.after)
    ls = text.rfind("\n", 0, proposal.start) + 1
    end = proposal.end
    if end > proposal.start and text[end - 1] == "\n":
        end -= 1  # a removal that takes its own newline does not reach into the next line
    le = text.find("\n", end)
    le = len(text) if le == -1 else le
    before = text[ls:le]
    after = text[ls:proposal.start] + proposal.after + text[proposal.end:le]
    return _Lines(before, after, proposal.before, proposal.after, proposal.start - ls)


def _issue(code: str, severity: str, proposal: EditProposal, message: str) -> ValidationIssue:
    return ValidationIssue(code, severity, proposal.stage_id, proposal.rule_key,
                           proposal.source_spans, message)


_ALNUM = re.compile(r"[^a-z0-9]+")


def _check_content_preserved(proposal: EditProposal, contract: RuleContract,
                             context: ValidationContext, lines: _Lines) -> _Check:
    """Formatting may move whitespace, change case and swap bullet/heading
    marks; it may not change a letter or a digit."""
    out = _Check()
    if _ALNUM.sub("", lines.before.lower()) == _ALNUM.sub("", lines.after.lower()):
        out.reasons.append("content_preserved")
    else:
        out.issues.append(_issue("content_changed", "error", proposal,
                                 "A formatting rule changed letters or digits, not just layout"))
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
    if lost and (proposal.stage_id in fc.DEDUP_STAGES or proposal.mapping == "coarse") \
            and proposal.epoch == context.ledger.epoch:
        # A folded duplicate keeps its value elsewhere in the output; only a
        # value that vanishes from the whole text counts. (Per-match regex
        # proposals stay local: two matches may each assume the other survives.)
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
    if _only_duplicate_segments_folded(lines):
        return out  # "No fever. No fever." → "No fever." keeps every distinct sentence
    for change in fc.meaning_changes(lines.before, lines.after, proposal.stage_id):
        code = "negation_lost" if change.kind == "negation" else "laterality_changed"
        severity = "critical"
        if change.kind == "negation" and _negation_in_clinically_empty_fragment(lines):
            # The negation left with a fragment that names nothing clinical
            # ("Dictated but not read"): unverified, not a definite flip.
            code, severity = "negation_removed_unverified", "warning"
            out.unresolved.append(tuple(proposal.enclosing))
            out.reasons.append("negation_unverified")
        out.issues.append(_issue(code, severity, proposal, change.message))
    return out


def _negation_in_clinically_empty_fragment(lines: _Lines) -> bool:
    """The removed fragment carried a negation but negated nothing clinical.

    True only when the fragment names nothing clinical *and* every negated
    finding of the line before the edit is still negated after it — removing
    the word "No" from "No fever" fails the second test and stays critical.
    """
    frag = lines.fragment_before
    if not frag.strip() or lines.fragment_after.strip() or not fc._negations(frag):
        return False
    from .clinical_statements import _is_clinical, extract_statements
    if extract_statements(frag).statements or _is_clinical(frag, cued=True):
        return False
    negated_before = {s.subject for s in extract_statements(lines.before).statements if s.polarity == "negative"}
    negated_after = {s.subject for s in extract_statements(lines.after).statements if s.polarity == "negative"}
    return not (negated_before - negated_after)


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
    """Whole-match scope: the match must start *and* end inside an allowed section.

    An unknown section (preamble, unrecognised header) authorizes nothing; a
    match that starts in scope but runs into the next section is rejected
    unless the contract allows crossing. Only an explicit contract disallows
    crossing by default — legacy rules keep today's behaviour.
    """
    out = _Check()
    if not (contract.sections or contract.note_types or not contract.allow_cross_section):
        return out
    if proposal.epoch != context.ledger.epoch:
        out.reasons.append("scope_unchecked")  # offsets no longer address the current text
        out.unresolved.append(tuple(proposal.enclosing))
        return out
    if contract.note_types:
        kind = context.note_type()
        if kind is None:
            out.reasons.append("note_type_unknown")
            out.unresolved.append(tuple(proposal.enclosing))
        elif kind not in contract.note_types:
            out.issues.append(_issue("scope_violation", "error", proposal,
                                     "The rule is not allowed in this note type"))
    if proposal.start >= proposal.end:
        return out
    first = context.domain_at(proposal.start)
    last = context.domain_at(proposal.end - 1)
    allowed = {s.strip().casefold() for s in contract.sections}
    if allowed:
        if first.casefold() not in allowed:
            out.issues.append(_issue("scope_violation", "error", proposal,
                                     "The match is outside the rule's sections" if first != "preamble"
                                     else "An unrecognised section cannot authorize a scoped removal"))
        elif last != first:
            code = "scope_violation" if (not contract.allow_cross_section or last.casefold() not in allowed) else None
            if code:
                out.issues.append(_issue(code, "error", proposal,
                                         "The match starts in scope but ends in another section"))
    elif last != first and not contract.allow_cross_section:
        out.issues.append(_issue("cross_section", "error", proposal,
                                 "The match crosses a section boundary"))
    return out


def _check_statements(proposal: EditProposal, contract: RuleContract,
                      context: ValidationContext, lines: _Lines) -> _Check:
    """Relationship check on the affected lines (see :mod:`statement_compare`).

    Statements recognized in the removed/rewritten text are compared with the
    text after the edit; their loss or alteration is a definite finding and
    the edit counts as *resolved*. Text with clinical signal that did not parse
    stays unresolved.
    """
    out = _Check()
    if lines.before.split() == lines.after.split():
        out.resolved = True
        return out
    from .clinical_statements import StatementExtraction, extract_statements
    from .statement_compare import compare_statements
    from .validation_windows import CONTEXT_CHARS
    coarse = proposal.mapping == "coarse" and proposal.epoch == context.ledger.epoch
    survivors = None
    if coarse:
        # One proposal covers the whole change (opaque stage, whole candidate).
        # Before: the statements the changed region owns, straight from the
        # incrementally maintained set (no re-extraction). Survivors: statements
        # elsewhere in the accepted text, so a folded duplicate is not a loss.
        # After: the changed lines parsed with a line-aligned context head, so
        # a table header or wrapped sentence above the change still labels them.
        cur = context.ledger.current
        ls = cur.rfind("\n", 0, proposal.start) + 1
        # The affected lines, mapped to the source coordinates the statement
        # set uses — before and after must cover the same ground.
        le = cur.find("\n", proposal.end - 1 if proposal.end > proposal.start and
                      cur[proposal.end - 1:proposal.end] == "\n" else proposal.end)
        le = len(cur) if le == -1 else le
        line_spans, _quality = context.ledger.spans_for(ls, le)
        line_spans = list(line_spans) or [context.ledger.spans_for(ls, max(le, ls + 1))[0][0]]

        def _overlaps(statement) -> bool:
            return any(a.start < s.end and s.start < a.end
                       for s in statement.source_spans for a in line_spans)

        before = StatementExtraction(
            tuple(s for s in context.statement_set.statements if _overlaps(s)), ())
        head_start = (cur.rfind("\n", 0, max(0, ls - CONTEXT_CHARS)) + 1) if ls > 0 else 0
        head = cur[head_start:ls]
        after_all = extract_statements(head + cur[ls:proposal.start] + proposal.after + cur[proposal.end:le])
        own = len(head)
        after = StatementExtraction(
            tuple(s for s in after_all.statements if any(sp.end > own for sp in s.source_spans)),
            tuple(sp for sp in after_all.unresolved_spans if sp.end > own))
        before_unresolved = [sp for sp in context.statement_set._windowed.unresolved()
                             if any(sp.start < a.end and a.start < sp.end for a in line_spans)]
        before = StatementExtraction(before.statements, tuple(before_unresolved))
        survivors = StatementExtraction(
            tuple(s for s in context.statement_set.statements if not _overlaps(s)), ())
        touched = list(before.statements)
    else:
        before = extract_statements(lines.before)
        after = extract_statements(lines.after)
        frag = (lines.fragment_start, lines.fragment_start + max(len(lines.fragment_before), 1))
        touched = [s for s in before.statements
                   if any(sp.start < frag[1] and sp.end > frag[0] for sp in s.source_spans)]
    out.recognized = len(touched)
    if not before.statements and not after.statements:
        if before.unresolved_spans:
            out.unresolved.append(tuple(proposal.enclosing))
            out.reasons.append("statements_unparsed")
        return out
    report = compare_statements(before, after, operation=contract.operation,
                                allowed_losses=contract.allowed_losses, stage_id=proposal.stage_id,
                                rule_key=proposal.rule_key, reference=context.statement_index,
                                survivors=survivors)
    for issue in report.issues:
        if issue.code == "claim_unverified":
            continue
        out.issues.append(_issue(issue.code, issue.severity, proposal, issue.message))
    out.checked = len(touched)
    # Resolved only when the edit touched something the extractor recognized;
    # a value on a neighbouring line does not vouch for the text removed.
    out.resolved = bool(touched)
    if before.unresolved_spans:
        out.unresolved.append(tuple(proposal.enclosing))
        out.reasons.append("statements_unparsed")
    return out


@lru_cache(maxsize=1)
def _do_not_use_entries() -> tuple[tuple[re.Pattern, re.Pattern | None, str], ...]:
    """(abbreviation regex, meaning regex, preferred form) for block-level entries."""
    try:
        from .abbreviation_safety import LIST_PATH
        data = json.loads(LIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ()
    out = []
    for entry in data.get("entries", []):
        if entry.get("level") != "block":
            continue
        abbrs = [a for a in entry.get("abbr", []) if isinstance(a, str) and a]
        if not abbrs:
            continue
        alternation = "|".join(re.escape(a) for a in sorted(abbrs, key=len, reverse=True))
        meaning = re.compile(entry["meaning"], re.IGNORECASE) if entry.get("meaning") else None
        out.append((re.compile(rf"(?<![A-Za-z0-9]){alternation}(?![A-Za-z0-9])"), meaning,
                    str(entry.get("use", ""))))
    return tuple(out)


_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+")


def _only_duplicate_segments_folded(lines: _Lines) -> bool:
    """True when the after-text keeps every distinct sentence of the before-text."""
    def _norm(segment: str) -> str:
        text = _LABEL_PREFIX.sub("", segment.strip(), count=1)
        return text.strip().casefold()

    seen = [_norm(s) for s in _SENTENCE_SPLIT.split(lines.before) if s.strip()]
    kept = {_norm(s) for s in _SENTENCE_SPLIT.split(lines.after) if s.strip()}
    return bool(kept) and all(s in kept for s in seen)


_LABEL_PREFIX = re.compile(r"^\s*[A-Za-z][A-Za-z /&()-]{0,30}:\s*")


def _check_do_not_use(proposal: EditProposal, contract: RuleContract, context: ValidationContext,
                      lines: _Lines) -> _Check:
    """An edit must not write a Do Not Use abbreviation for a term the line spelled out.

    Meaning-aware like :mod:`abbreviation_safety`: ``U`` only counts when the
    text before the edit said *units*, ``QD`` when it said *daily*.
    """
    out = _Check()
    if lines.before.split() == lines.after.split() or lines.after == "":
        return out
    for rx, meaning, use in _do_not_use_entries():
        if not rx.search(lines.after) or rx.search(lines.before):
            continue
        if meaning is not None and not meaning.search(lines.before):
            continue
        out.issues.append(_issue("do_not_use_abbreviation", "error", proposal,
                                 f"A Do Not Use abbreviation was written where the chart said '{use or 'the full term'}'"))
    out.issues.extend(_unendorsed_abbreviations(proposal, lines))
    return out


@lru_cache(maxsize=1)
def _abbreviation_meanings() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every dictionary abbreviation with its distinct meanings."""
    try:
        from .abbreviations import _source_rows, display_term
        meanings: dict[str, list[str]] = {}
        for row in _source_rows():
            abbr = (row.get("Abbreviation") or "").strip()
            term = (row.get("Expanded version") or "").strip()
            if not abbr or not term or "not expanded in source" in term.casefold():
                continue
            forms = meanings.setdefault(abbr, [])
            found = display_term(term).lower()
            if found not in forms:
                forms.append(found)
        return tuple((a, tuple(fs)) for a, fs in sorted(meanings.items()) if fs)
    except Exception:
        return ()


def _unendorsed_abbreviations(proposal: EditProposal, lines: _Lines) -> list[ValidationIssue]:
    """Flag a newly written abbreviation the tool would not have chosen.

    ``Mental status`` → ``MS`` passes (the abbreviation matcher endorses it for
    that meaning); ``Physical exam`` → ``PE`` and ``Chest pain`` → ``CP`` do
    not — the dictionary reads them as pulmonary embolism and cerebral palsy,
    so the shorthand the edit invented points at the wrong thing.
    """
    from .abbreviations import lookup
    from .clinical_statements import _expand
    issues: list[ValidationIssue] = []
    table = dict(_abbreviation_meanings())
    before_tokens = set(re.findall(r"[A-Za-z0-9]+", lines.before))
    candidates = [m.group(0) for m in
                  re.finditer(r"(?<![A-Za-z0-9])[A-Za-z]{2,6}(?![A-Za-z0-9])", lines.after)
                  if m.group(0) in table and m.group(0) not in before_tokens]
    if not candidates:
        return issues
    # These whole-line operations used to run once per possible meaning. Do
    # them once per proposal, and skip them entirely when no new abbreviation
    # needs semantic review (the common case for opaque stage proposals).
    expanded_before = _expand(lines.before).casefold()
    before_fold = lines.before.casefold()

    for abbr in candidates:
        forms = table[abbr]

        def _said(meaning: str) -> bool:
            # The term itself, or its words' stems in a variant spelling
            # ("CT angiogram" for "computed tomography angiography").
            if meaning in before_fold:
                return True
            stems = [w[:5] for w in meaning.split() if len(w) > 4]
            return bool(stems) and all(s in expanded_before for s in stems)

        matched = next((f for f in forms if _said(f)), None)
        if matched is not None and lookup(matched) == abbr:
            continue  # exactly what the matcher writes for that term
        issues.append(_issue("ambiguous_abbreviation", "error", proposal,
                             f"'{abbr}' was written for text the dictionary does not read that way"
                             if matched is None else
                             f"'{abbr}' was written where the matcher keeps the term spelled out"))
    return issues


CheckFn = Callable[[EditProposal, RuleContract, ValidationContext, _Lines], _Check]
_CHECKS: dict[str, CheckFn] = {
    "content_preserved": _check_content_preserved,
    "facts": _check_facts,
    "meaning": _check_meaning,
    "introduced": _check_introduced,
    "scope": _check_scope,
    "statements": _check_statements,
    "do_not_use": _check_do_not_use,
}


# --- decision ------------------------------------------------------------------

def decide_report(issues: Iterable[ValidationIssue], coverage: Coverage,
                  contract: RuleContract) -> str:
    """The shared decision for one proposal (see the design's decision table).

    A definite violation or a failed check keeps the original. Missing
    evidence (unresolved spans) keeps it too for an explicit destructive
    contract until the rule is reviewed; an implicit legacy contract has no
    review to wait for, so only definite findings stop it — its coverage still
    says what was not checked.
    """
    issues = tuple(issues)
    if coverage.status == "failed":
        return "retain_original"
    if any(i.blocking for i in issues):
        return "retain_original"
    if coverage.unresolved_spans and contract.destructive and not contract.reviewed and not contract.legacy:
        return "retain_original"  # missing evidence: keep the passage, ask for review
    return "accept"


def decide_edit(proposal: EditProposal, report: ValidationReport, contract: RuleContract) -> str:
    """The enforcement decision for one proposal (the shared decision enum).

    The report's own decision stands, with one escalation: a *redaction* that
    cannot be accepted leaves identifying text in place, so keeping the
    original is not enough — release is withheld.
    """
    if contract.operation == "redact" and report.decision != "accept":
        return "withhold_release"
    return report.decision


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
    resolved = False
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
        resolved = resolved or result.resolved
    if proposal.is_removal and not resolved and "content_preserved" not in policy.required_checks:
        # Nothing recognizable was in the removed text (or the statements check
        # did not run): what it *meant* is unchecked, whatever the cheaper
        # checks found. Processing coverage is full; relation coverage is not,
        # so the span stays unresolved and the status partial.
        unresolved.append(tuple(proposal.enclosing))
        reasons.append("statements_unavailable" if "statements" not in policy.required_checks
                       else "no_statements_recognized")
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

    The benchmark, whole-document callers and edited-output checks use this.
    The window of difference becomes one proposal under a generic legacy
    ``reduce`` contract (facts, meaning, introduced numbers, statements).
    """
    ledger = EditLedger(source)
    context = ValidationContext.for_source(source, config=cfg, ledger=ledger, mode=mode)
    if candidate == source:
        cov = context.statement_set.coverage()
        return ValidationReport((), cov, "accept" if cov.status != "failed" else "retain_original")
    limit = min(len(source), len(candidate))
    a = 0
    while a < limit and source[a] == candidate[a]:
        a += 1
    b = 0
    while b < limit - a and source[-1 - b] == candidate[-1 - b]:
        b += 1
    proposal = ledger.propose(stage_id, None, a, len(source) - b, candidate[a:len(candidate) - b],
                              mapping="coarse")
    return validate_proposal(proposal, RuleContract(key="candidate", operation="reduce", legacy=True),
                             context)


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
        self.limits: list[str] = []
        self._synced_reports = 0
        self._synced_unvalidated = 0

    def note_limit(self, reason: str) -> None:
        """Record a budget another check hit (its coverage is then partial)."""
        if reason not in self.limits:
            self.limits.append(reason)

    def sync(self) -> None:
        """After a stage: fold the proposals accepted since last time into the statement set."""
        judged = [p for p, _r in self.reports[self._synced_reports:]] \
            + self.unvalidated[self._synced_unvalidated:]
        self._synced_reports = len(self.reports)
        self._synced_unvalidated = len(self.unvalidated)
        self.context.sync_statements([p for p in judged if p.decision == "accept"])

    def contract_for(self, proposal: EditProposal) -> RuleContract:
        found = self.contracts.get(proposal.rule_key) if proposal.rule_key else None
        return found or default_contract(proposal.stage_id, proposal.rule_key)

    def judge(self, proposal: EditProposal) -> str:
        if self.max_proposals is not None and len(self.reports) >= self.max_proposals:
            self.unvalidated.append(proposal)
            # Missing evidence: shadow ships the text anyway; enforcement keeps the passage.
            return "retain_original" if self.context.mode == "enforce" else "accept"
        contract = self.contract_for(proposal)
        report = validate_proposal(proposal, contract, self.context)
        decision = decide_edit(proposal, report, contract)
        if decision != report.decision:
            report = ValidationReport(report.issues, report.coverage, decision)
            proposal.report = report
        self.reports.append((proposal, report))
        return decision if self.context.mode == "enforce" else "accept"

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
        reasons.extend(self.limits)
        if self.context._statement_set is not None:
            # Windows that could not be split, and clinical signal nobody parsed.
            cov = self.context.statement_set.coverage()
            reasons.extend(cov.reasons)
            unresolved.extend(tuple(s) for s in cov.unresolved_spans)
        coverage = Coverage.from_spans(source_len, processed, unresolved=unresolved,
                                       recognized=recognized, checked=checked, reasons=reasons,
                                       failed=failed)
        decision = _worst(rep.decision for _p, rep in self.reports)
        if self.unvalidated and decision == "accept":
            decision = "retain_original"  # unchecked edits are not accepted edits
        if coverage.status == "failed" and decision == "accept":
            decision = "retain_original"
        enforcing = self.context.mode == "enforce"
        applied = [p.decision for p, _rep in self.reports] + [p.decision for p in self.unvalidated]
        rejected = sum(1 for d in applied if d == "retain_original") if enforcing else 0
        withheld = sum(1 for d in applied if d == "withhold_release") if enforcing else 0
        return ValidationReport(tuple(issues), coverage, decision, rejected=rejected, withheld=withheld)
