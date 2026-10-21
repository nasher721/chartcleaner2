"""Relationship comparison between source and candidate clinical statements.

Support means a candidate statement claims nothing the source does not: the
same subject with the same value, the same polarity, no stronger certainty,
the same experiencer, the same side, a date the source gives for that
value, and — for medications — no status the source contradicts (a held
drug cannot read as active). The same numbers under another drug, date or
subject establish nothing; distinct dates stay distinct even when the text
otherwise repeats; conflicting source statements stay conflicting.

Two uses share the machinery:

* **cleaning** (``operation`` is a rule operation): every source statement
  must survive in a candidate statement that keeps its known attributes
  (``statement_lost`` otherwise, or ``intentional_loss`` under an explicit
  allowed-loss policy) and every candidate statement must be supported;
* **generated text** (:func:`verify_claims`): omission is fine, an
  unsupported or altered claim is not, and prose nobody parsed is reported as
  unchecked coverage — never as a score.

Issue ``source_spans`` point at the conflicting *source* evidence for lost or
altered statements and at the claim itself (candidate coordinates) for a
statement the source never had.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .clinical_statements import (
    CERTAINTY_RANK,
    NON_ACTIVE_STATUSES,
    ClinicalStatement,
    StatementExtraction,
    StatementIndex,
    extract_statements,
)
from .edit_provenance import SourceSpan, merge_spans, union_length
from .validation import Coverage, ValidationIssue, ValidationReport

__all__ = ["ATTRIBUTE_CODES", "compare_statements", "mismatches", "verify_claims"]

ATTRIBUTE_CODES = {
    "value": "value_changed", "unit": "unit_changed", "polarity": "polarity_changed",
    "certainty": "certainty_changed", "temporality": "temporality_changed",
    "experiencer": "experiencer_changed", "laterality": "laterality_changed", "route": "route_changed",
    "frequency": "frequency_changed", "medication_status": "medication_status_changed",
}
# Attribute mismatches that flip what the sentence means are critical; a
# dropped qualifier (strict mode only) is an error.
_CRITICAL = frozenset({"value", "polarity", "certainty", "experiencer", "laterality", "medication_status",
                       "temporality", "route"})


def _dated(t: str | None) -> bool:
    return bool(t) and t[0].isdigit()


def _temporal_ok(src: str | None, cand: str | None, latest: str | None, strict: bool) -> bool:
    if cand is None:
        return not (strict and src is not None)
    if src is None:
        # A dated claim the source never dated is a claim; "current"/"prior"
        # against an undated source cannot be checked either way.
        return not _dated(cand)
    if src == cand:
        return True
    if cand == "current":
        return _dated(src) and latest is not None and src == latest
    if src == "current":
        return _dated(cand) and latest is not None and cand == latest
    if cand == "prior":
        return _dated(src) and latest is not None and src < latest
    return False


def _unit(u: str | None) -> str | None:
    return u.replace(" ", "").casefold() if u else None


def mismatches(src: ClinicalStatement, cand: ClinicalStatement, *, strict: bool,
               latest: str | None = None) -> tuple[str, ...]:
    """Attributes where ``cand`` claims more than, or differently from, ``src``.

    ``strict`` (cleaning) also counts a known source attribute the candidate
    dropped. ``latest`` is the latest dated temporality the source gives for
    this subject, used to resolve "current".
    """
    out: list[str] = []
    if cand.value is not None and (src.value is None or src.value != cand.value):
        out.append("value")
    elif strict and src.value is not None and cand.value is None:
        out.append("value")
    if _unit(src.unit) and _unit(cand.unit) and _unit(src.unit) != _unit(cand.unit):
        out.append("unit")
    if (cand.polarity or "positive") != (src.polarity or "positive"):
        out.append("polarity")
    rc, rs = CERTAINTY_RANK[cand.certainty or "stated"], CERTAINTY_RANK[src.certainty or "stated"]
    if rc > rs or (strict and rc != rs):
        out.append("certainty")
    if (cand.experiencer or "patient") != (src.experiencer or "patient"):
        out.append("experiencer")
    for attr in ("laterality", "route", "frequency"):
        s, c = getattr(src, attr), getattr(cand, attr)
        if c is not None and (s is None or s != c):
            out.append(attr)
        elif strict and s is not None and c is None:
            out.append(attr)
    if not _temporal_ok(src.temporality, cand.temporality, latest, strict):
        out.append("temporality")
    s, c = src.medication_status, cand.medication_status
    if c is not None:
        if s is None or s != c:
            out.append("medication_status")
    elif s in NON_ACTIVE_STATUSES:
        out.append("medication_status")  # a held/stopped drug cannot read as active
    return tuple(out)


def _issue(code: str, severity: str, spans: Iterable[SourceSpan], message: str, *, stage_id: str,
           rule_key: str | None) -> ValidationIssue:
    spans = tuple(spans)
    if len(spans) > _MAX_ISSUE_SPANS:
        spans = spans[:_MAX_ISSUE_SPANS]
    return ValidationIssue(code, severity, stage_id, rule_key, spans, message)


_MAX_ISSUE_SPANS = 50


def _attrs(s: ClinicalStatement) -> tuple:
    return (s.value, _unit(s.unit), s.polarity, s.certainty, s.temporality, s.experiencer, s.laterality,
            s.route, s.frequency, s.medication_status)


class _Groups:
    """Statements grouped by kind → subject → attribute tuple.

    Identical repeats (copy-forward charts say "aspirin 81 mg" on every line)
    collapse into one group, so comparing two charts costs the number of
    *distinct* statements, not their product.
    """

    def __init__(self, extraction: StatementExtraction):
        self.by_subject: dict[tuple[str, str], dict[tuple, list[ClinicalStatement]]] = {}
        for s in extraction.statements:
            self.by_subject.setdefault((s.kind, s.subject), {}).setdefault(_attrs(s), []).append(s)
        self._by_sig: dict[tuple[str, str], list[tuple[str, str]]] | None = None

    def pool(self, s: ClinicalStatement) -> list[list[ClinicalStatement]]:
        """Groups about an equivalent subject (exact first, then by abbreviation signature)."""
        exact = self.by_subject.get((s.kind, s.subject))
        if exact:
            return list(exact.values())
        if self._by_sig is None:
            from .clinical_statements import signature
            self._by_sig = {}
            for kind, subject in self.by_subject:
                self._by_sig.setdefault((kind, signature(subject)), []).append((kind, subject))
        from .clinical_statements import signature
        out: list[list[ClinicalStatement]] = []
        for key in self._by_sig.get((s.kind, signature(s.subject)), []):
            out.extend(self.by_subject[key].values())
        return out

    def groups(self):
        for key, variants in self.by_subject.items():
            for group in variants.values():
                yield key, group

    def latest(self, kind: str, subject: str) -> str | None:
        dates = [s.temporality for group in self.pool(ClinicalStatement(kind, subject)) for s in group[:1]
                 if _dated(s.temporality)]
        return max(dates) if dates else None


def _latest_for(groups: "_Groups", s: ClinicalStatement, reference: StatementIndex | None,
                cache: dict) -> str | None:
    key = (s.kind, s.subject)
    if key in cache:
        return cache[key]
    found = reference.latest(key) if reference is not None else None
    if found is None:
        found = groups.latest(s.kind, s.subject)
    cache[key] = found
    return found


def _loss_allowed(s: ClinicalStatement, allowed: Sequence[str], kept: list[ClinicalStatement],
                  latest: str | None) -> bool:
    for rule in allowed:
        if rule in ("*", "statement:", f"statement:{s.kind}"):
            return True
        if rule == "n:" and s.kind == "measurement":
            return True
        if rule == "drug:" and s.kind == "medication":
            return True
        if rule == "prior_values" and _dated(s.temporality) and latest and s.temporality < latest:
            # latest-only reduction: fine while the latest value for the subject survives
            if any(k.temporality == latest for k in kept):
                return True
    return False


def compare_statements(source: StatementExtraction, candidate: StatementExtraction, *, operation: str,
                       allowed_losses: Sequence[str] = (), stage_id: str = "statements",
                       rule_key: str | None = None, reference: StatementIndex | None = None,
                       input_chars: int | None = None,
                       survivors: StatementExtraction | None = None) -> ValidationReport:
    """Compare recognized statements; ``operation`` ``"summary"``/``"qa"`` allows omission.

    ``survivors`` are statements found elsewhere in the whole output; a source
    statement one of them still states (same attributes, same date) was folded,
    not lost.
    """
    generated = operation in ("summary", "qa")
    strict = not generated
    issues: list[ValidationIssue] = []
    src, cand = _Groups(source), _Groups(candidate)
    surv = _Groups(survivors) if survivors is not None else None
    latest_cache: dict = {}
    preserved: set[int] = set()  # id() of every statement in a strictly matched source group

    for _key, group in cand.groups():
        c = group[0]
        latest = _latest_for(src, c, reference, latest_cache)
        pool = src.pool(c)
        reps = [g[0] for g in pool]
        if any(not mismatches(s, c, strict=False, latest=latest) for s in reps):
            for g in pool:
                if not mismatches(g[0], c, strict=True, latest=latest):
                    preserved.update(id(s) for s in g)
            continue
        spans = tuple(sp for s in group for sp in s.source_spans)
        if reps:
            best = min(reps, key=lambda s: len(mismatches(s, c, strict=False, latest=latest)))
            best_spans = tuple(sp for g in pool if g[0] is best for s in g for sp in s.source_spans)
            for attr in mismatches(best, c, strict=False, latest=latest):
                issues.append(_issue(ATTRIBUTE_CODES[attr], "critical" if attr in _CRITICAL else "error",
                                     best_spans,
                                     f"{c.kind.capitalize()} {attr.replace('_', ' ')} differs from the chart",
                                     stage_id=stage_id, rule_key=rule_key))
        else:
            issues.append(_issue("statement_unsupported", "error", spans,
                                 f"A {c.kind} statement has no support in the chart",
                                 stage_id=stage_id, rule_key=rule_key))

    if not generated:
        for _key, group in src.groups():
            s = group[0]
            if id(s) in preserved:
                continue
            latest = _latest_for(src, s, reference, latest_cache)
            pool = cand.pool(s)
            reps = [g[0] for g in pool]
            if any(not mismatches(s, c, strict=True, latest=latest) for c in reps):
                continue
            if surv is not None and any(not mismatches(s, g[0], strict=True, latest=latest) for g in surv.pool(s)):
                continue  # folded duplicate: the same statement survives elsewhere in the output
            spans = tuple(sp for x in group for sp in x.source_spans)
            if _loss_allowed(s, allowed_losses, reps, latest):
                issues.append(_issue("intentional_loss", "info", spans,
                                     f"A {s.kind} statement was dropped under the rule's allowed-loss policy",
                                     stage_id=stage_id, rule_key=rule_key))
                continue
            if reps and any(not mismatches(s, c, strict=False, latest=latest) for c in reps):
                # Present but weakened (a dropped side, date, route…): report the attribute.
                best = min(reps, key=lambda c: len(mismatches(s, c, strict=True, latest=latest)))
                for attr in mismatches(s, best, strict=True, latest=latest):
                    issues.append(_issue(ATTRIBUTE_CODES[attr], "error", spans,
                                         f"{s.kind.capitalize()} {attr.replace('_', ' ')} was dropped",
                                         stage_id=stage_id, rule_key=rule_key))
                continue
            issues.append(_issue("statement_lost", "error", spans,
                                 f"A {s.kind} statement was removed", stage_id=stage_id, rule_key=rule_key))

    for span in candidate.unresolved_spans:
        issues.append(_issue("claim_unverified", "warning", (span,),
                             "Text with clinical signal could not be checked", stage_id=stage_id,
                             rule_key=rule_key))

    spans = [tuple(x) for x in source.spans + candidate.spans]
    unresolved = [tuple(x) for x in source.unresolved_spans + candidate.unresolved_spans]
    total = input_chars if input_chars is not None else max(
        [e for _s, e in spans + unresolved] + [0])
    coverage = Coverage.from_spans(total, spans, unresolved=unresolved,
                                   recognized=len(candidate.statements) if generated else len(source.statements),
                                   checked=len(candidate.statements) if generated else len(source.statements),
                                   reasons=("statements_unparsed",) if unresolved else ())
    decision = "retain_original" if any(i.blocking for i in issues) else "accept"
    return ValidationReport(tuple(issues), coverage, decision)


def verify_claims(source: str, generated: str) -> ValidationReport:
    """Check each recognized claim in ``generated`` against ``source``.

    Omissions are fine. Coverage counts the recognized claims and marks
    unparsed clinical prose as unresolved; it never yields an accuracy score
    for text nobody checked.
    """
    index = StatementIndex(source)
    claims = extract_statements(generated)
    report = compare_statements(index.extraction, claims, operation="summary", reference=index,
                                input_chars=len(generated))
    # Coverage of the *generated* text: what of it was recognized and checked.
    processed = [tuple(s) for s in claims.spans]
    unresolved = [tuple(s) for s in claims.unresolved_spans]
    coverage = Coverage.from_spans(len(generated), processed, unresolved=unresolved,
                                   recognized=len(claims.statements), checked=len(claims.statements),
                                   reasons=(("unparsed_prose",) if union_length(processed) < len(generated) else ())
                                   + (("statements_unparsed",) if unresolved else ()))
    return ValidationReport(report.issues, coverage, report.decision)
