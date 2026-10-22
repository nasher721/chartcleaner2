"""Independent accuracy benchmark for chart-cleaning edits (Phase 0).

The benchmark scores a *candidate edit* — the text a rule or stage would
produce — against labels that were authored by hand, as literal text:

* ``required_statements``: text that must still be in the output (each entry
  may list approved equivalent forms, e.g. ``["no acute distress", "NAD"]``);
* ``forbidden_claims``: text that must not appear (a swapped dose, a dropped
  negation turned into a positive finding);
* ``authorized_removals``: clutter the edit is expected to remove;
* ``required_removals``: identifying text that must be gone before the output
  may be released — kept separate from clutter because retaining the original
  does not make it acceptable either (a reverted redaction is an exposure).

Production parsing is deliberately *not* the oracle: ``fact_check`` accepts a
number found anywhere in the source, so swapping two doses between drugs
passes it, and a deleted negative sentence with no numbers is invisible to it.
Here both count against the edit.

Per category and overall the report carries counts **and denominators**:
harmful-edit escapes, valid-edit rejections, required-statement retention,
unsupported output claims, authorized clutter removal, review burden and
coverage. Rates with a zero denominator are ``None`` (JSON ``null``), never a
perfect score. Reports hold case ids, counts and digests — no chart text.

Modes mirror the validator (``off``, ``shadow``, ``enforce``). ``off`` is the
legacy behaviour: every candidate ships. The system decision for a candidate
comes from a :data:`Decider`; the default one asks ``chartcleaner.validation``
when it exists and otherwise reports ``unavailable`` coverage, so the Phase 0
baseline measures today's failures without touching production code.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import tracemalloc
from dataclasses import dataclass, field
from functools import lru_cache as _lru_cache
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence

__all__ = [
    "AccuracyCase", "AccuracyReport", "CaseOutcome", "CATEGORIES", "DISPOSITIONS",
    "Decision", "EVALUATOR_VERSION", "GATE_VERSION", "LABEL_STATUSES", "MODES", "THRESHOLDS",
    "check_release", "default_decider", "evaluate_accuracy", "load_cases", "load_splits",
    "minimal_config", "normalize", "present",
]

EVALUATOR_VERSION = 1
GATE_VERSION = 1

# The design's release targets. Changes need a recorded rationale and review;
# any override passed to check_release is echoed in the result, never silent.
THRESHOLDS = {
    "no_new_critical_escapes": 0,
    "escape_reduction": 0.5,          # vs the recorded baseline, when it was nonzero
    "valid_edit_rejection": 0.05,
    "clutter_removal_ratio": 0.95,    # of the baseline's authorized clutter removal
}

CATEGORIES = (
    "clutter_removal",
    "sentence_deletion",
    "negation",
    "laterality",
    "uncertainty",
    "temporality",
    "medication_relationships",
    "measurement_relationships",
    "abbreviation_ambiguity",
    "deduplication",
    "redaction_collisions",
    "section_table_boundaries",
)
DISPOSITIONS = ("accept", "reject")
LABEL_STATUSES = ("provisional", "reviewed")
MODES = ("off", "shadow", "enforce")
SPLITS = ("development", "held-out")

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "tests" / "accuracy" / "cases.json"
SPLITS_PATH = ROOT / "tests" / "accuracy" / "splits.json"

Alternatives = tuple[str, ...]


# --- literal matching --------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Whitespace-collapsed, case-folded text for literal containment checks."""
    return _WS.sub(" ", text).strip().casefold()


@_lru_cache(maxsize=4096)
def _needle(needle: str) -> re.Pattern:
    # Word boundaries at both ends: "glucose 20" must not match "glucose 200",
    # "QD" must not match "QDay"; punctuation-ended needles keep plain edges.
    body = re.escape(normalize(needle))
    head = r"(?<![A-Za-z0-9])" if body and body[0].isalnum() else ""
    tail = r"(?![A-Za-z0-9])" if body and body[-1].isalnum() else ""
    return re.compile(head + body + tail)


def present(needle: str, haystack: str) -> bool:
    return bool(needle.strip()) and _needle(needle).search(normalize(haystack)) is not None


def _any_present(forms: Alternatives, haystack: str) -> bool:
    return any(present(f, haystack) for f in forms)


def _alternatives(value: Any) -> Alternatives:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ValueError(f"required statement must be text or a list of equivalent forms: {value!r}")


# --- cases -------------------------------------------------------------------

@dataclass(frozen=True)
class AccuracyCase:
    """One labeled candidate edit.

    Exactly one of ``candidate`` (the output text to judge) or ``config`` (config
    overrides; the candidate is then the real pipeline's output on ``source``)
    is set. ``expected`` is the label: ``accept`` for a valid edit, ``reject``
    for a harmful one.
    """

    case_id: str
    category: str
    source: str
    expected: str
    candidate: str | None = None
    config: dict | None = None
    required_statements: tuple[Alternatives, ...] = ()
    authorized_removals: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    required_removals: tuple[str, ...] = ()
    label_status: str = "provisional"
    note: str = ""

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.case_id}: unknown category {self.category!r}")
        if self.expected not in DISPOSITIONS:
            raise ValueError(f"{self.case_id}: expected must be one of {DISPOSITIONS}")
        if self.label_status not in LABEL_STATUSES:
            raise ValueError(f"{self.case_id}: label_status must be one of {LABEL_STATUSES}")
        if (self.candidate is None) == (self.config is None):
            raise ValueError(f"{self.case_id}: give exactly one of candidate or config")
        if not self.source:
            raise ValueError(f"{self.case_id}: source text is required")

    @property
    def harmful(self) -> bool:
        return self.expected == "reject"

    @classmethod
    def from_dict(cls, data: dict) -> "AccuracyCase":
        return cls(
            case_id=str(data["case_id"]),
            category=str(data["category"]),
            source=str(data["source"]),
            expected=str(data["expected"]),
            candidate=data.get("candidate"),
            config=data.get("config"),
            required_statements=tuple(_alternatives(x) for x in data.get("required_statements", ())),
            authorized_removals=tuple(str(x) for x in data.get("authorized_removals", ())),
            forbidden_claims=tuple(str(x) for x in data.get("forbidden_claims", ())),
            required_removals=tuple(str(x) for x in data.get("required_removals", ())),
            label_status=str(data.get("label_status", "provisional")),
            note=str(data.get("note", "")),
        )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_splits(path: Path | None = None) -> dict[str, list[str]]:
    """``{"development": [ids], "held-out": [ids]}`` from the frozen manifest."""
    data = _read_json(path or SPLITS_PATH)
    return {name: list(data.get(name, [])) for name in SPLITS}


def load_cases(path: Path | None = None, *, split: str | None = None,
               splits_path: Path | None = None) -> list[AccuracyCase]:
    """The synthetic corpus, optionally restricted to one split (in manifest order)."""
    if split is not None and split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    data = _read_json(path or CASES_PATH)
    items = data["cases"] if isinstance(data, dict) else data
    cases = [AccuracyCase.from_dict(item) for item in items]
    if split is None:
        return cases
    wanted = load_splits(splits_path)[split]
    by_id = {c.case_id: c for c in cases}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        raise ValueError(f"split {split!r} names unknown cases: {missing[:5]}")
    return [by_id[i] for i in wanted]


def minimal_config() -> dict:
    """A pipeline config that does nothing until a case adds a rule to it.

    Pipeline-run cases layer their overrides on this so every difference in the
    output is the case's own rule, not an unrelated default.
    """
    return {
        "emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
        "literal_replacements": [], "clinical_headers": [],
        "nlp_redaction": {"enabled": False},
        "duplicate_note_detection": {"enabled": False},
        "fuzzy_dedup": {"enabled": False},
        "stage_options": {"medical_abbreviations": {"enabled": False}},
        "wrap_output": False,
    }


# --- decisions ---------------------------------------------------------------

class Decision(NamedTuple):
    """What happened to a candidate.

    ``applied`` is the decision that shaped the output (``accept`` ships the
    candidate; anything else keeps the source). ``advisory`` is what the
    validator said even when it was not allowed to act (shadow mode).
    ``coverage`` is the validator's coverage status (``complete``, ``partial``,
    ``unavailable`` or ``failed``).
    """

    applied: str
    advisory: str | None
    coverage: str


Decider = Callable[[AccuracyCase, str, dict, str], Decision]


def default_decider(case: AccuracyCase, candidate: str, cfg: dict, mode: str) -> Decision:
    """Ask the validator when it exists; otherwise the legacy answer: accept, unchecked."""
    if mode == "off":
        return Decision("accept", None, "unavailable")
    try:
        from . import validation  # added by the provenance/validation phases
    except ImportError:
        return Decision("accept", None, "unavailable")
    judge = getattr(validation, "validate_candidate", None)
    if judge is None:
        return Decision("accept", None, "unavailable")
    try:
        report = judge(case.source, candidate, cfg, mode=mode)
    except Exception:
        # A validator crash is a failed check, not a pass; in enforce mode the
        # original is retained (the design's "validator failure" row).
        return Decision("retain_original" if mode == "enforce" else "accept", None, "failed")
    advisory = str(report.decision)
    coverage = str(report.coverage.status)
    applied = advisory if mode == "enforce" else "accept"
    return Decision(applied, advisory, coverage)


# --- outcomes and report -----------------------------------------------------

@dataclass(frozen=True)
class CaseOutcome:
    """Text-free result for one case."""

    case_id: str
    category: str
    harmful: bool
    label_status: str
    applied: str
    advisory: str | None
    coverage: str
    required: int
    retained: int
    forbidden: int
    unsupported: int
    authorized: int
    removed: int
    required_removals: int
    exposed: int
    escape: bool
    false_rejection: bool
    label_inconsistent: bool
    output_digest: str
    error: str | None = None
    withheld: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


_COUNT_KEYS = ("harmful", "escapes", "valid", "valid_rejected", "required", "retained",
               "forbidden", "unsupported", "authorized", "removed", "required_removals",
               "exposed", "review_burden", "withheld", "label_inconsistent", "errors")


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _metrics(outcomes: Sequence[CaseOutcome]) -> dict[str, Any]:
    c = {k: 0 for k in _COUNT_KEYS}
    c["cases"] = len(outcomes)
    for o in outcomes:
        if o.harmful:
            c["harmful"] += 1
            c["escapes"] += int(o.escape)
        else:
            c["valid"] += 1
            c["valid_rejected"] += int(o.false_rejection)
        c["required"] += o.required
        c["retained"] += o.retained
        c["forbidden"] += o.forbidden
        c["unsupported"] += o.unsupported
        c["authorized"] += o.authorized
        c["removed"] += o.removed
        c["required_removals"] += o.required_removals
        c["exposed"] += o.exposed
        c["review_burden"] += int(o.applied != "accept")
        c["withheld"] += int(o.withheld)
        c["label_inconsistent"] += int(o.label_inconsistent)
        c["errors"] += int(o.error is not None)
    c["escape_rate"] = _rate(c["escapes"], c["harmful"])
    c["valid_rejection_rate"] = _rate(c["valid_rejected"], c["valid"])
    c["retention_rate"] = _rate(c["retained"], c["required"])
    c["clutter_removal_rate"] = _rate(c["removed"], c["authorized"])
    # PHI removal stays its own metric (the design keeps it apart from accuracy).
    c["phi_removal_rate"] = _rate(c["required_removals"] - c["exposed"], c["required_removals"])
    return c


@dataclass
class AccuracyReport:
    split: str
    mode: str
    cases: int
    by_category: dict[str, dict[str, Any]]
    totals: dict[str, Any]
    label_status: dict[str, int]
    coverage: dict[str, int]
    fixture_digest: str
    config_digest: str
    evaluator_version: int
    runtime_ms: float
    peak_memory_bytes: int
    outcomes: list[CaseOutcome] = field(default_factory=list)

    def metric_counts(self) -> dict[str, Any]:
        """Everything that must be identical between two runs on the same inputs."""
        return {
            "cases": self.cases,
            "by_category": self.by_category,
            "totals": self.totals,
            "label_status": self.label_status,
            "coverage": self.coverage,
            "fixture_digest": self.fixture_digest,
            "config_digest": self.config_digest,
            "evaluator_version": self.evaluator_version,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }

    def to_dict(self) -> dict:
        return {"split": self.split, "mode": self.mode, **self.metric_counts(),
                "runtime_ms": round(self.runtime_ms, 1), "peak_memory_bytes": self.peak_memory_bytes}

    def gate_summary(self) -> str:
        t = self.totals
        def pct(v): return "n/a" if v is None else f"{100 * v:.1f}%"
        return (f"{self.cases} case(s): escapes {t['escapes']}/{t['harmful']} ({pct(t['escape_rate'])}), "
                f"valid rejected {t['valid_rejected']}/{t['valid']} ({pct(t['valid_rejection_rate'])}), "
                f"required kept {t['retained']}/{t['required']} ({pct(t['retention_rate'])}), "
                f"unsupported claims {t['unsupported']}/{t['forbidden']}, "
                f"clutter removed {t['removed']}/{t['authorized']} ({pct(t['clutter_removal_rate'])})")


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _case_fixture(case: AccuracyCase) -> dict:
    return {"case_id": case.case_id, "category": case.category, "source": case.source,
            "expected": case.expected, "candidate": case.candidate, "config": case.config,
            "required_statements": case.required_statements,
            "authorized_removals": case.authorized_removals,
            "forbidden_claims": case.forbidden_claims, "required_removals": case.required_removals,
            "label_status": case.label_status}


class _Checks(NamedTuple):
    retained: int
    unsupported: int
    removed: int
    exposed: int

    def harmed(self, case: "AccuracyCase") -> bool:
        return (self.retained < len(case.required_statements) or self.unsupported > 0
                or self.exposed > 0)


def _literal_checks(case: AccuracyCase, output: str) -> _Checks:
    """Counts for ``output`` against the case's literal labels."""
    return _Checks(
        retained=sum(1 for forms in case.required_statements if _any_present(forms, output)),
        unsupported=sum(1 for claim in case.forbidden_claims if present(claim, output)),
        removed=sum(1 for clutter in case.authorized_removals if not present(clutter, output)),
        exposed=sum(1 for phi in case.required_removals if present(phi, output)),
    )


def _pipeline_candidate(case: AccuracyCase, cfg: dict, mode: str) -> tuple[str, Decision]:
    from .engine import Pipeline

    merged = {**cfg, **(case.config or {})}
    if "stage_options" in cfg and "stage_options" in (case.config or {}):
        merged["stage_options"] = {**cfg["stage_options"], **case.config["stage_options"]}
    # The benchmark's mode is the pipeline's mode — including "off", so the
    # engine's shadow default never leaks into an off-mode baseline.
    merged["cleaning_validation"] = {**(merged.get("cleaning_validation") or {}),
                                     "mode": mode, "schema_version": 1}
    result = Pipeline(merged).run(case.source, wrap=False, fact_check=False)
    report = getattr(result, "validation", None)
    if report is None or mode == "off":
        return result.text, Decision("accept", None, "unavailable")
    # The pipeline already applied (or shadowed) the decision; the output is final.
    return result.text, Decision("accept", str(report.decision), str(report.coverage.status))


def _evaluate_case(case: AccuracyCase, cfg: dict, mode: str, decide: Decider) -> CaseOutcome:
    error = None
    try:
        if case.config is not None:
            candidate, decision = _pipeline_candidate(case, cfg, mode)
        else:
            candidate = case.candidate or ""
            decision = decide(case, candidate, cfg, mode)
    except Exception as e:  # a failing case must not hide the rest of the report
        error = f"{type(e).__name__}"
        candidate, decision = case.source, Decision("retain_original", None, "failed")
    withheld = decision.applied == "withhold_release"
    output = candidate if decision.applied == "accept" else case.source
    checks = _literal_checks(case, output)
    if withheld:
        # Nothing is released: no escape and no exposure, but every required
        # removal counts as not done and the case lands on the review pile.
        checks = _Checks(retained=checks.retained, unsupported=0, removed=0, exposed=0)
    harmed = (not withheld) and checks.harmed(case)
    candidate_harm = _literal_checks(case, candidate).harmed(case)
    return CaseOutcome(
        case_id=case.case_id, category=case.category, harmful=case.harmful,
        label_status=case.label_status, applied=decision.applied, advisory=decision.advisory,
        coverage=decision.coverage,
        required=len(case.required_statements), retained=checks.retained,
        forbidden=len(case.forbidden_claims), unsupported=checks.unsupported,
        authorized=len(case.authorized_removals), removed=checks.removed,
        required_removals=len(case.required_removals), exposed=checks.exposed,
        escape=case.harmful and harmed,
        false_rejection=(not case.harmful) and decision.applied != "accept",
        label_inconsistent=(case.harmful != candidate_harm) and error is None,
        output_digest=hashlib.sha256(output.encode("utf-8", "surrogatepass")).hexdigest()[:16],
        error=error, withheld=withheld,
    )


def check_release(report: AccuracyReport, baseline: AccuracyReport, *,
                  labels: dict | None = None,
                  thresholds: dict | None = None) -> dict:
    """The machine-readable release gate; ``ok`` is False when any check fails.

    Compares a candidate ``report`` against the frozen ``baseline`` of the same
    split: no new critical escapes (and none at all when the baseline had
    zero), at least the design's escape reduction, valid-edit rejection and
    clutter-removal ratios within target — and labels that a clinician has
    actually reviewed. Unreviewed labels fail certification no matter how good
    the numbers are. Counters and digests only; no chart text.
    """
    used = dict(THRESHOLDS)
    if thresholds:
        used.update(thresholds)
    t, b = report.totals, baseline.totals
    checks: list[dict] = []

    def add(name: str, ok: bool, observed, threshold) -> None:
        checks.append({"name": name, "ok": bool(ok), "observed": observed, "threshold": threshold})

    add("no_new_critical_escapes", t["escapes"] - b["escapes"] <= used["no_new_critical_escapes"],
        t["escapes"] - b["escapes"], used["no_new_critical_escapes"])
    if b["escapes"] > 0:
        reduction = 1 - (t["escapes"] / b["escapes"]) if b["escapes"] else 1.0
        add("escape_reduction", reduction >= used["escape_reduction"],
            round(reduction, 4), used["escape_reduction"])
    else:
        add("no_escapes", t["escapes"] == 0, t["escapes"], 0)
    vr = t["valid_rejection_rate"]
    add("valid_edit_rejection", vr is None or vr <= used["valid_edit_rejection"],
        vr, used["valid_edit_rejection"])
    reviewed = bool(labels) and labels.get("status") == "reviewed"
    add("labels_clinician_reviewed", reviewed, (labels or {}).get("status", "unprovided"), "reviewed")
    # Clutter removal is judged on the *valid* cases only: a correctly rejected
    # harmful edit keeps the clutter it would have removed, by design.
    def _valid_clutter(outcomes) -> tuple[int, int]:
        auth = sum(o.authorized for o in outcomes if not o.harmful)
        rem = sum(o.removed for o in outcomes if not o.harmful)
        return auth, rem
    v_auth, v_rem = _valid_clutter(report.outcomes)
    b_auth, b_rem = _valid_clutter(baseline.outcomes)
    if v_auth and b_auth:
        ratio = (v_rem / v_auth) / (b_rem / b_auth)
    elif v_auth == b_auth == 0:
        ratio = 1.0
    else:
        ratio = 0.0
    return {
        "ok": all(c["ok"] for c in checks), "gate_version": GATE_VERSION,
        "checks": checks, "thresholds_used": used,
        "split": report.split, "mode": report.mode,
        "fixture_digest": report.fixture_digest, "config_digest": report.config_digest,
        "baseline_fixture_digest": baseline.fixture_digest,
        "totals": report.totals, "baseline_totals": baseline.totals,
        "labels": labels or {"status": "provisional"},
    }


def evaluate_accuracy(cases: Sequence[AccuracyCase], cfg: dict, *, mode: str = "off",
                      decide: Decider | None = None, split: str = "custom") -> AccuracyReport:
    """Score ``cases`` under ``mode`` and return the text-free :class:`AccuracyReport`."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    decide = decide or default_decider
    started = time.perf_counter()
    tracing = not tracemalloc.is_tracing()
    if tracing:
        tracemalloc.start()
    try:
        outcomes = [_evaluate_case(case, cfg, mode, decide) for case in cases]
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        if tracing:
            tracemalloc.stop()
    by_cat: dict[str, list[CaseOutcome]] = {}
    for o in outcomes:
        by_cat.setdefault(o.category, []).append(o)
    labels: dict[str, int] = {}
    coverage: dict[str, int] = {}
    for o in outcomes:
        labels[o.label_status] = labels.get(o.label_status, 0) + 1
        coverage[o.coverage] = coverage.get(o.coverage, 0) + 1
    return AccuracyReport(
        split=split, mode=mode, cases=len(outcomes),
        by_category={cat: _metrics(items) for cat, items in sorted(by_cat.items())},
        totals=_metrics(outcomes),
        label_status=dict(sorted(labels.items())),
        coverage=dict(sorted(coverage.items())),
        fixture_digest=_digest([_case_fixture(c) for c in cases]),
        config_digest=_digest(cfg),
        evaluator_version=EVALUATOR_VERSION,
        runtime_ms=(time.perf_counter() - started) * 1000,
        peak_memory_bytes=max(int(peak), 1),
        outcomes=outcomes,
    )
