"""In-memory findings shared by the Clean status, review list and copy gate,
and the one release decision every output surface consults."""
from dataclasses import dataclass
from collections import Counter
from bisect import bisect_right
from difflib import SequenceMatcher, get_close_matches
import hashlib
import re

RELEASE_STATUSES = ("ready", "review", "blocked")


def review_findings(report, audit, *, source='', output=''):
    items = []
    if report:
        for loss in report.losses:
            if loss.category == 'by_design':
                continue
            items.append(dict(severity='blocked' if loss.category == 'unexpected' else 'review',
                              title=f'Clinical value removed: {loss.display}',
                              detail=loss.stage_label, before=loss.lines[0] if loss.lines else '', after=''))
        for change in report.meaning:
            items.append(dict(severity='blocked' if change.category == 'unexpected' else 'review',
                              title=change.message, detail=change.stage_label,
                              before=change.before, after=change.after))
        for value in report.introduced:
            items.append(dict(severity='blocked' if value.category == 'unexpected' else 'review',
                              title=f'Value introduced: {value.display}', detail=value.stage_label,
                              before='', after=value.lines[0] if value.lines else ''))
        for value in report.implausible:
            items.append(dict(severity='review' if value.key() in report.implausible_in_source else 'blocked',
                              title=value.message, detail='Check the source and units',
                              before=value.line if value.key() in report.implausible_in_source else '', after=value.line))
    if audit and not audit.skipped:
        for finding in audit.findings:
            lines = output.splitlines()
            actual = lines[finding.line - 1] if 0 < finding.line <= len(lines) else ''
            original = actual if actual and actual in source else ''
            if actual and not original:
                nearby = get_close_matches(actual, source.splitlines(), n=1, cutoff=0.6)
                original = nearby[0] if nearby else ''
            items.append(dict(severity='review', title='Possible identifying information',
                              detail=finding.check, before=original, after=actual))
    return sorted(items, key=lambda item: item['severity'] != 'blocked')


def review_status(report, audit):
    items = review_findings(report, audit)
    if (report and report.status == 'alert') or any(x['severity'] == 'blocked' for x in items):
        return 'blocked'
    if items or report is None or audit is None or audit.skipped or audit.errors:
        return 'review'
    return 'clean'


def redaction_candidates(before, after):
    """Map only unambiguous single-token, single-line replacements for inspection.

    Ambiguous changes deliberately have no original candidate; users can still
    inspect/edit them without guessing which source text should be restored.
    """
    matches = list(re.finditer(r'\[REDACTED_[A-Z_]+\]', after))
    if not matches:
        return []
    mapping = {}
    source_lines = before.splitlines()
    indexed_output = [(i, line) for i, line in enumerate(after.splitlines())
                      if line not in ('<patient_chart>', '</patient_chart>')]
    output_lines = [line for _, line in indexed_output]
    from .edit_provenance import SMALL_DIFF_CELLS
    # shortcut: large-chart originals stay ambiguous, use exact provenance before enabling restoration.
    opcodes = (SequenceMatcher(None, source_lines, output_lines, autojunk=False).get_opcodes()
               if len(source_lines) * len(output_lines) <= SMALL_DIFF_CELLS else ())
    for tag, a, b, c, d in opcodes:
        if tag != 'replace' or b-a != d-c:
            continue
        for line_index, (source, output) in enumerate(zip(source_lines[a:b], output_lines[c:d]), c):
            tokens = list(re.finditer(r'\[REDACTED_[A-Z_]+\]', output))
            if len(tokens) != 1:
                continue
            token = tokens[0]
            prefix, suffix = output[:token.start()], output[token.end():]
            if source.startswith(prefix) and source.endswith(suffix):
                original = source[len(prefix):len(source)-len(suffix) if suffix else len(source)]
                mapping[indexed_output[line_index][0]] = original
    newlines = [m.start() for m in re.finditer('\n', after)]
    return [dict(start=m.start(), end=m.end(), token=m.group(),
                 original=mapping.get(bisect_right(newlines, m.start())))
            for m in matches]


# --- release ---------------------------------------------------------------------

@dataclass(frozen=True)
class ReleaseDecision:
    """Whether a result may leave the app as de-identified, cleaned text.

    ``blocked`` (a mandatory redaction failed; no acknowledgement overrides
    it), ``review`` (someone must look and acknowledge with
    ``acknowledgement_key``), or ``ready``.
    """

    status: str
    reasons: tuple[str, ...]
    acknowledgement_key: str
    deidentification_complete: bool

    def __post_init__(self):
        if self.status not in RELEASE_STATUSES:
            raise ValueError(f"status must be one of {RELEASE_STATUSES}")

    @property
    def releasable(self) -> bool:
        return self.status != "blocked"

    def to_dict(self) -> dict:
        return {"status": self.status, "reasons": list(self.reasons),
                "acknowledgement_key": self.acknowledgement_key,
                "deidentification_complete": self.deidentification_complete}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def acknowledgement_key(result, output_text: str) -> str:
    """Key an acknowledgement binds to: source, this exact output, config, validator."""
    parts = (getattr(result, "source_digest", "") or "", _sha(output_text),
             getattr(result, "config_digest", "") or "", getattr(result, "validator_version", "") or "")
    return _sha("|".join(parts))[:16]


# Reasons that need a human acknowledgement before release; the others are
# disclosed on the decision but do not gate it.
_REVIEW_REASONS = frozenset({"clinical_alert", "edits_retained", "harmful_edits_shipped",
                             "redaction_unverified", "coverage_failed", "edited_output_issues",
                             "budget_exhausted"})


def release_decision(result, *, output_text: str, acknowledgement: str | None = None,
                     source: str | None = None) -> ReleaseDecision:
    """The one decision UI copy, export, CLI, API, MCP, batch and watcher all use.

    ``output_text`` is what would leave (edits included); when it differs from
    the pipeline's text and ``source`` is given, the edited text is validated
    again. A failed mandatory redaction blocks regardless of acknowledgement.
    """
    reasons: list[str] = []
    validation = getattr(result, "validation", None)
    deidentified = True
    if validation is not None:
        if validation.withheld:
            # Enforcement withheld a redaction: identifying text is still in place.
            deidentified = False
            reasons.append("redaction_failed")
        elif validation.decision == "withhold_release":
            # Shadow mode: the validator would have withheld, but shadow changes
            # nothing — the result ships with a review flag, not a block.
            reasons.append("redaction_unverified")
    facts = getattr(result, "fact_check", None)
    if facts is not None:
        if facts.status == "alert":
            reasons.append("clinical_alert")
        elif facts.status == "review":
            reasons.append("rule_removed_values")
    if validation is not None:
        if validation.rejected:
            reasons.append("edits_retained")
        elif validation.decision == "retain_original":
            reasons.append("harmful_edits_shipped")  # shadow mode: advisory only
        status = validation.coverage.status
        if status != "complete":
            reasons.append(f"coverage_{status}")
        if "budget_exhausted" in validation.coverage.reasons:
            reasons.append("budget_exhausted")
    baseline = getattr(result, "validated_output_digest", "")
    edited = (_sha(output_text) != baseline if baseline
              else output_text != getattr(result, "text", output_text))
    if edited:
        reasons.append("edited_output")
        ledger = getattr(result, "ledger", None)
        if ledger is not None:
            markers = re.compile(r'\[REDACTED_[A-Z_]+\]')
            before = Counter(markers.sub("[REDACTED]", line) for line in ledger.current.splitlines()
                             if markers.search(line))
            after = Counter(markers.sub("[REDACTED]", line) for line in output_text.splitlines()
                            if markers.search(line))
            if before - after:
                deidentified = False
                reasons.append("redaction_unverified")
        if source is not None:
            from .validation import validate_candidate, validator_version
            signature = (_sha(source), _sha(output_text), validator_version())
            cached = getattr(result, "edited_validation", None)
            try:
                if cached is not None and cached[0] == signature:
                    again = cached[1]
                else:
                    again = validate_candidate(source, output_text, {}, mode="enforce", stage_id="edited_output")
                    result.edited_validation = (signature, again)
            except Exception:
                reasons.append("coverage_failed")
            else:
                if again.decision != "accept" or again.coverage.status != "complete":
                    reasons.append("edited_output_issues")
    key = acknowledgement_key(result, output_text)
    if "redaction_failed" in reasons:
        status = "blocked"
    elif any(r in _REVIEW_REASONS for r in reasons):
        status = "ready" if acknowledgement == key else "review"
    else:
        status = "ready"
    return ReleaseDecision(status, tuple(dict.fromkeys(reasons)), key, deidentified)


def support_text(result) -> str:
    """Processing and relation coverage in words — never a score for unchecked text."""
    validation = getattr(result, "validation", None)
    if validation is None:
        return "Validation not run."
    cov = validation.coverage
    if cov.status == "unavailable":
        return "Validation off — edits were not checked."
    processed = f"{cov.processed_chars:,} of {cov.input_chars:,} characters processed"
    if cov.recognized_statements == 0:
        return f"{processed}; no clinical statements recognized in the edited text — its meaning is unchecked."
    text = (f"{processed}; {cov.checked_statements} of {cov.recognized_statements} recognized "
            f"statement(s) checked")
    if cov.unresolved_spans:
        text += f"; {len(cov.unresolved_spans)} region(s) unresolved"
    if cov.status == "failed":
        text += "; a check failed"
    return text + "."
