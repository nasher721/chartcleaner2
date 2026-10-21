# Chart Cleaning Accuracy Design

Status: proposed design accompanying the requested phased plan; implementation has not started.

## Purpose and success

Improve all five areas discussed on 2026-10-21: prevent risky edits, preserve relationships between clinical facts, constrain regex rules, measure preservation of meaning, and retain validation on long charts. The user requested a phased implementation plan for all five. The design assumption is that preserving meaning takes priority over shortening output. The user's most frequent real-world failure category has not been established; Phase 0 measures it rather than assuming it.

Success means fewer harmful deletions and unsupported alterations on independently reviewed examples, useful removal of clutter, and an explicit account of what was checked. Passing checks never proves complete clinical accuracy.

## Current implementation anchors

Inspected checkout: `86f20d4`, 2026-10-21. Recheck paths and interfaces at execution time. `app.py` and `config.json` contain pre-existing local changes.

- `engine.Pipeline.run` snapshots facts between stages and returns a report. Its execution loop does not use that report to reject individual transformations.
- `stage_core._subn_tracked` centralizes regex replacement and already supports exceptions, section scope, and table protection. Recorded change details are capped; a safety mechanism cannot depend on that display-oriented cap.
- `fact_check.meaning_changes` compares rewritten lines for negation/laterality, skips removed lines, and has a line-count limit.
- `fact_check.verify_output` accepts numeric values found anywhere in the source; this is not a check of the relationship between a value and its subject.
- `rule_preview.preview` runs a candidate's stage against recent charts/examples. `regression_set` stores encrypted known-good input/output pairs. Extend both.
- `review_state.py` and `app_pages/clean.py` already expose review states and guarded copying. Extend these rather than introduce a second review workflow.
- `Pipeline.run` skips its clinical-facts check above 1,000,000 characters. Existing cache keys ignore `fact_check`; adding enforcement requires separating reusable candidates from policy-dependent accepted output.

These are static code observations, not new runtime or accuracy measurements.

## Chosen architecture

Use a deterministic proposal–validation–acceptance pipeline. Keep the current Python/NiceGUI architecture, local AI boundary, ordered stage runners, and legacy configuration formats. Introduce small modules for source-linked edits, rule contracts, clinical statements, and validation coverage. Existing runners retain their tuple return contract; optional state on `CleanContext` and additive fields on `RunResult` carry new data.

The source document remains immutable for one run. Every edit identifies its original source spans, owning stage/rule, candidate text, and disposition. Regex edits can be accepted individually; opaque legacy stages and custom scripts initially form one atomic proposal per stage. If exact alignment cannot be established, retain conservative enclosing source spans and mark mapping as coarse. Do not invent precise evidence links.

### Rule contracts

A versioned sidecar contract uses a key derived from stage ID, pattern, replacement, and flags. Do not change legacy pattern IDs used by current history/UI. A contract defines allowed sections and note types, whether crossing a section boundary is permitted, allowed operation (`format`, `abbreviate`, `remove_boilerplate`, `redact`, or `reduce`), and positive/negative examples.

Defaults: cross-section edits disallowed for new contracted removal rules; unknown section context does not authorize a scoped edit. Old rules remain usable in legacy/shadow mode and are visibly unreviewed. A contract authorizes an operation, not arbitrary fact loss. Edited or imported contracts must be validated, tested, and versioned; signatures/trust records must account for metadata where applicable.

### Clinical statements

Represent recognized statements with source spans, note/section identity, subject, value/unit, polarity, certainty, temporality, experiencer, laterality, and medication route/frequency/status when present. Unknown fields remain unknown. Retain the original text and never infer an absent dose, diagnosis, date, or treatment status.

Use deterministic parsing for a declared set of patterns first, drawing on existing abbreviations, compactors, and date helpers. If ambiguous, record an unchecked relation. New parsing libraries or model-based verification require a separate measured decision; this plan does not add a required model dependency.

Match source and output statements within their event context. Equivalent approved abbreviations are allowed. Same numbers under different drugs, dates, subjects, or measurements do not establish support. Distinct dates and statuses remain distinct even when text otherwise repeats. Conflicting source statements remain conflicting; the cleaner does not choose the medically correct one.

For generated summaries/Q&A, require each recognized output claim to link to a compatible source statement. Do not require summaries to retain every source statement. For cleaning, assess both lost source statements and unsupported new statements, subject to explicit reduction policy.

### Decisions and review

Modes: `off`, `shadow`, `enforce`; migration and first release default to `shadow`. Shadow returns the existing candidate output while recording what enforcement would have done. Off disables the new validator and explicitly reports unavailable coverage; it does not disable existing checks.

Enforcement decision:

| Condition | Result |
|---|---|
| Supported, complete validation for the rule's required checks | Accept candidate |
| Definite protected meaning violation | Keep original affected passage; show rejected proposal |
| Missing required evidence, ambiguous mapping, validator failure or exhausted budget | Keep original affected passage; mark incomplete and require review |
| Explicit reduction policy permits a specific loss | Accept, record intentional loss and its source |
| A required redaction cannot be accepted | Withhold releasable de-identified output; show a local review result |

Do not reject every edit in unparsed prose: contracts declare required checks by operation. Formatting that provably preserves content can be accepted without complete clinical parsing. A semantic deletion cannot use absent parser findings as evidence of safety. This distinction and false-rejection measurement are essential to usability.

Validation statuses are `complete`, `partial`, `unavailable`, and `failed`. The UI says "No issues detected by enabled checks" only when appropriate; never "clinically safe". Coverage reports processing coverage and supported-relation coverage separately. A document with no recognized facts must not receive a 100% clinical-support score.

The shared service evaluates release readiness for UI, clipboard automation, CLI, API, MCP, export, and batch output. Source/candidate views remain available locally for review. Acknowledgment is tied to source digest, result digest, configuration digest, and validator version; changing any invalidates it. Manual output edits trigger revalidation.

### Long-chart behavior

Partition validation work at note/section boundaries while preserving original offsets and carried context. Do not split and rerun the cleaning pipeline independently on arbitrary chunks: that changes multiline regex, deduplication, and temporal behavior.

Maintain incremental indexes of accepted statements. Validate changed regions with surrounding context and a final reconciliation of statements crossing boundaries. Index original facts once; avoid rescanning the entire chart for each match. Work limits must produce explicit incomplete coverage, never an empty list interpreted as success. A single oversized note, malformed table, or uncertain boundary retains its unresolved region under enforcement.

## Global constraints

- Preserve Python 3.10+ support and the existing macOS, Windows, and Linux CI matrix.
- Preserve existing configuration and output contracts through additive, versioned fields; do not rewrite user configuration on load.
- Keep chart content, source spans, and candidate text local; persist examples only through existing encrypted storage and retention controls.
- Logs, including verbose development logs, contain timing, counts, rule IDs, and error codes, not chart excerpts or replacement values.
- No mandatory network service, cloud chart transfer, new LLM dependency, or automatic clinical inference.
- Safety decisions must not depend on `track_changes`, UI excerpt limits, or a warm cache.
- Missing checks are not successful checks. Validation errors cannot silently mark a result complete.
- Preserve intentional reductions only under explicit operation-specific policy and disclose them separately from unexpected loss.
- Preserve unrelated local files and edits, including `app.py` and `config.json`.

## Measurement and release gates

Create 120 synthetic cases across 12 categories: clutter removal, whole-sentence deletion, negation, laterality, uncertainty, temporality, medication relationships, measurement relationships, abbreviation ambiguity, deduplication, redaction/clinical-word collisions, and section/table boundaries. Each category has 10 cases, including at least four valid edits and four harmful candidates. Use 8 per category for development and 2 for held-out assessment; freeze both manifests before tuning. Final labels require clinician review; unreviewed labels are provisional.

Report harmful-edit escape rate, valid-edit rejection rate, required-fact retention, unsupported output claims, authorized clutter removal, review burden, and both forms of coverage. Provide counts and denominators by category and operation, not only an aggregate score. PHI-removal effectiveness remains a separate metric.

Proposed release targets, not claims of measured performance:

- Zero escapes on the finite critical sentinel suite; reject any release that introduces a new critical escape.
- At least 50% fewer harmful escapes on the frozen held-out set versus recorded baseline when baseline escapes are nonzero; when zero, maintain zero and add independently labeled challenges without replacing the original holdout.
- Valid-edit rejection at or below 5% on the labeled valid-edit set; always show the denominator because the initial corpus is small.
- Authorized clutter removal at least 95% of baseline on the same labeled set, with separately justified changes when baseline removal was harmful.
- 100% accounted input processing for supported synthetic large-chart fixtures. Unsupported semantics remain explicitly partial regardless of processing coverage.
- Warm median runtime at most 1.5 times baseline for representative inputs up to 100,000 characters; at 1M/5M characters, peak memory at most twice baseline and 5M runtime at most seven times 1M runtime on the same machine. Record cold/warm results separately; large runs belong in scheduled performance jobs, not brittle wall-clock PR assertions.

Default enforcement is a separate release decision after clinician review and opt-in local pilot results. If thresholds fail, keep shadow mode and fix or narrow the applicable checks. Existing known-good outputs are never bulk-approved just to make tests pass.

## Delivery and review

Seven phases: baseline, source provenance/shadow decisions, rule contracts and full-pipeline trials, relational validation, enforcement and release handling, long-chart scaling, and controlled rollout. Each phase has a testable deliverable and can land without enabling enforcement by default. The implementation plan contains exact task ownership and gates.

This document records proposed choices needed to make the user's requested plan executable. It is not a claim that the design, thresholds, or default-mode change have already received separate approval.
