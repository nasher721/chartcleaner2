# Chart Cleaning Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce harmful chart-cleaning edits and unsupported generated content through measurable, source-linked validation of regex and pipeline transformations, including long charts.

**Architecture:** Extend the existing ordered pipeline with immutable source provenance, rule contracts, explicit validation coverage, and proposal acceptance. Introduce checks in shadow mode, add relational validation, and enable enforcement only after quality gates pass. Keep regex, clinical parsing, evaluation, and user review separate but connected through typed reports.

**Tech Stack:** Existing Python 3.10+, standard-library dataclasses/regex, NiceGUI, pytest, encrypted local storage, and optional existing local LLM integration. No new runtime dependency is required by this plan.

**Spec:** [Proposed cleaning accuracy design](../specs/2026-10-21-cleaning-accuracy-design.md). Read it before executing this plan. Both documents were requested as planning artifacts; no product changes have been made.

## Global Constraints

- Preserve Python 3.10+ support and the existing macOS, Windows, and Linux CI matrix.
- Preserve existing configuration and output contracts through additive, versioned fields; do not rewrite user configuration on load.
- Keep chart content, source spans, and candidate text local; persist examples only through existing encrypted storage and retention controls.
- Logs, including verbose development logs, contain timing, counts, rule IDs, and error codes, not chart excerpts or replacement values.
- No mandatory network service, cloud chart transfer, new LLM dependency, or automatic clinical inference.
- Safety decisions must not depend on `track_changes`, UI excerpt limits, or a warm cache.
- Missing checks are not successful checks. Validation errors cannot silently mark a result complete.
- Preserve intentional reductions only under explicit operation-specific policy and disclose them separately from unexpected loss.
- Preserve unrelated local files and edits, including `app.py` and `config.json`.

## Review Focus

1. A medication and dose survive individually but become paired with the wrong drug/date: Task 5 must flag the relationship change.
2. A whole negative sentence disappears without any recognized numeric fact: Tasks 1 and 5 must detect or explicitly leave its deletion unverified.
3. A reverted redaction restores PHI: Tasks 6 and 7 must withhold release as de-identified text across every output surface.
4. Cached candidates, edited outputs, or prior acknowledgment bypass new checks: Tasks 3, 6, and 7 must revalidate and invalidate acknowledgment.
5. A fact or regex match crosses a note/chunk/table boundary: Tasks 4 and 8 must preserve context or report incomplete coverage without declaring success.

## Phase sequence and exit criteria

| Phase | Tasks | Deliverable | Exit gate | Depends on |
|---|---|---|---|---|
| 0 — Establish the baseline | 1 | Labeled challenge corpus and reproducible accuracy report | Reproducible counts; provisional labels clearly identified | None |
| 1 — Trace edits and observe decisions | 2–3 | Source-linked proposals and shadow validation | Existing outputs unchanged; incomplete checks visible | 0 |
| 2 — Constrain and test rules | 4 | Rule contracts, counterexamples, full-pipeline trials | Scope violations caught; interaction regressions block promotion | 1 |
| 3 — Validate clinical relationships | 5 | Contextual statement extraction and source support | Critical relationship sentinels pass; unknowns remain explicit | 1–2 |
| 4 — Enforce and expose decisions | 6–7 | Selective rejection and shared release handling | Rejected edits do not reach releasable output; redaction failures remain blocked | 2–3 |
| 5 — Cover large charts | 8 | Incremental validation and coverage accounting | Boundary parity, large-input accounting, performance gates | 1–4 |
| 6 — Qualify and roll out | 9 | Release evidence, pilot, staged default decision | Design's quality targets plus clinician review | All prior phases |

One task is one reviewable implementation unit; split into smaller commits within the task when useful. Phase 0 and shadow-mode phases can ship while enforcement remains disabled. Full completion includes Phase 5: long charts must not silently retain the old validation cutoff.

## Shared contracts and file responsibilities

New types are frozen dataclasses unless they accumulate run-local state. Proposed names below are deliberate interface decisions; they are not existing APIs.

| File | Responsibility / contract |
|---|---|
| `chartcleaner/accuracy_eval.py` (new) | `evaluate_accuracy(cases: Sequence[AccuracyCase], cfg: dict) -> AccuracyReport`; evaluate labels independently of production extraction |
| `chartcleaner/edit_provenance.py` (new) | `SourceSpan(start: int, end: int)`, `EditProposal(proposal_id: str, stage_id: str, rule_key: str | None, source_spans: tuple[SourceSpan, ...], before: str, after: str, mapping: str)`; run-local `EditLedger` |
| `chartcleaner/validation.py` (new) | `Coverage`, `ValidationIssue`, `ValidationReport`; `validate_proposal(proposal: EditProposal, policy: RuleContract, context: ValidationContext) -> ValidationReport` |
| `chartcleaner/rule_contracts.py` (new) | `RuleContract`, versioned identity, loading/migration, validation requirements and allowed-loss policy |
| `chartcleaner/clinical_statements.py` (new) | `ClinicalStatement`; `extract_statements(text: str, *, source_offset: int = 0, note_id: str = "", section: str = "") -> StatementExtraction` |
| `chartcleaner/statement_compare.py` (new) | `compare_statements(source: StatementExtraction, candidate: StatementExtraction, *, operation: str) -> ValidationReport` |
| `chartcleaner/validation_windows.py` (new) | `iter_validation_windows(text: str, *, target_chars: int = 32000) -> Iterator[ValidationWindow]`; bounded work with source ownership |
| `engine.py`, `stage_core.py`, `stage_regex.py` | Produce proposals and apply accepted results while retaining current runner interfaces |
| `fact_check.py`, `review_state.py`, `service.py` | Adapt existing facts/review reporting to additive validation and release fields |
| `rule_preview.py`, `regression_set.py`, rule editors | Trial and promote candidate rules using existing local review/examples |
| `stage_cache.py` | Cache candidate work only; never cache a policy verdict as if it were reusable text |

`Coverage` fields: `status`, `input_chars`, `processed_chars`, `recognized_statements`, `checked_statements`, `unresolved_spans`, and `reasons`. Valid status values: `complete`, `partial`, `unavailable`, `failed`. Processed spans are a union; overlap cannot inflate coverage.

`ValidationIssue` fields: `code`, `severity`, `stage_id`, `rule_key`, `source_spans`, `message`. Codes are stable machine-readable identifiers; messages must not embed chart excerpts in persisted summaries. `ValidationReport` contains `issues`, `coverage`, and `decision` (`accept`, `retain_original`, `withhold_release`).

`ValidationContext` contains the immutable source text, source/configuration digests, run-local ledger, current statement index, enabled-check versions, and mode. `RuleContract` contains `version`, `key`, `operation`, `sections`, `note_types`, `allow_cross_section`, `required_checks`, `allowed_losses`, and example references.

`ClinicalStatement` fields: `kind`, `subject`, `value`, `unit`, `polarity`, `certainty`, `temporality`, `experiencer`, `laterality`, `route`, `frequency`, `medication_status`, `note_id`, `section`, and `source_spans`. All clinical attributes may be unknown. `StatementExtraction` contains `statements` and `unresolved_spans`.

`ValidationWindow` contains `text`, `source_start`, `source_end`, `owned_start`, `owned_end`, `note_id`, `section`, and carried context. Owned ranges partition input; context ranges may overlap.

## Phase 0 — Establish the baseline

### Task 1: Build an independent accuracy benchmark

**Files:** Create `chartcleaner/accuracy_eval.py`, `tests/accuracy/cases.json`, `tests/accuracy/splits.json`, `tests/test_accuracy_eval.py`, `scripts/evaluate_cleaning_accuracy.py`. Extend `tests/test_phase7_trust.py` only where an existing regression belongs there. Reference existing `evaluate.py` and `regression_set.py`; do not replace them.

**Interfaces:** `AccuracyCase` holds case ID, category, source text, candidate edit/config, expected disposition, required statements, authorized removals, forbidden output claims, and label-review status. `AccuracyReport` carries counts/denominators per category, fixture/config/version digests, runtime, and peak memory. Production parsing is not the label oracle.

- [ ] Add tests `test_harmful_sentence_deletion_counts_as_escape`, `test_wrong_drug_same_number_counts_as_unsupported`, `test_zero_denominator_is_not_perfect_accuracy`, and `test_split_manifest_has_no_duplicate_cases`. Assert a missing negative statement is a harmful loss even with no numbers, a mismatched dose relationship is unsupported, undefined rates are `null`, and case IDs are disjoint across splits.
- [ ] Run `python -m pytest tests/test_accuracy_eval.py -q`; verify failures identify missing evaluator behavior.
- [ ] Implement the evaluator and 120-case corpus defined in the spec: 96 development / 24 held-out, 12 categories, at least 4 valid and 4 harmful candidates per category. Include literal text expectations alongside independently authored relational labels. Mark synthetic labels provisional until clinician review; no real chart text enters Git.
- [ ] Implement `scripts/evaluate_cleaning_accuracy.py --split development|held-out --report PATH --mode off|shadow|enforce`; save only synthetic evidence and aggregate metrics. Record the initial baseline and fixture/config digests. Run held-out only at baseline and release checkpoints, not on every rule tuning iteration.
- [ ] Run `python -m pytest tests/test_accuracy_eval.py tests/test_fact_check.py tests/test_phase7_trust.py -q` and repeat the development evaluator twice; require identical metric counts. Runtime may vary. Review and commit only this task's files.

**Gate:** A report identifies current failures without modifying production behavior. Clinical label review is a dependency for release claims, not a reason to invent labels or stall implementation of the harness.

## Phase 1 — Trace edits and observe decisions

### Task 2: Add immutable provenance and typed coverage

**Files:** Create `chartcleaner/edit_provenance.py`, `chartcleaner/validation.py`, `chartcleaner/rule_contracts.py` (shared dataclass only), `tests/test_edit_provenance.py`, `tests/test_validation_coverage.py`. Modify `chartcleaner/engine.py`, `chartcleaner/stage_core.py`.

**Interfaces:** `EditLedger(source: str)`, `propose(stage_id: str, rule_key: str | None, start: int, end: int, replacement: str) -> EditProposal`, and `resolve(proposal: EditProposal, decision: str) -> None`. Offsets passed to `propose` reference the current accepted text; proposals expose original source ranges. Rejected proposals do not advance the mapping. Define the shared `RuleContract`, `Coverage`, `ValidationIssue`, `ValidationReport`, and `ValidationContext` records here so Task 3 has no dependency on later implementations. The statement index is initially `None`; Task 5 supplies it.

- [ ] Add `test_two_edits_keep_original_offsets`, `test_rejected_edit_does_not_shift_next_edit`, `test_unicode_crlf_offsets_use_python_character_indices`, and `test_tracking_limit_does_not_limit_validation`. Assert edits after a length-changing replacement still link to the exact original substring; source text remains unchanged; a proposal beyond the UI detail cap is still validated.
- [ ] Run `python -m pytest tests/test_edit_provenance.py tests/test_validation_coverage.py -q`; confirm expected failures.
- [ ] Implement span mapping and typed reports. Use coarse enclosing spans for opaque stage transformations. Record insertion anchors without pretending inserted text existed in the original source. Keep the ledger in memory; only aggregate summaries persist.
- [ ] Run the new tests plus `python -m pytest tests/test_fact_check.py tests/test_options.py -q`; require unchanged legacy outputs and accurate coarse/exact mapping labels. Review and commit.

### Task 3: Wire shadow validation through the execution loop and cache

**Files:** Modify `chartcleaner/engine.py`, `chartcleaner/stage_cache.py`, `chartcleaner/config_schema_base.py`, `chartcleaner/config_schema_review.py`, `chartcleaner/config_validator.py`, `chartcleaner/default_config.json`; extend `chartcleaner/validation.py`. Create `tests/test_validation_shadow.py`.

**Interfaces:** Add configuration `cleaning_validation = {"mode": "shadow", "schema_version": 1}`. `RunResult.validation: ValidationReport | None` and optional `CleanContext.validation_context` are additive. Initial validators adapt existing fact/meaning checks and report their limitations.

- [ ] Add `test_shadow_does_not_change_legacy_output`, `test_check_failure_is_failed_not_complete`, `test_warm_cache_revalidates_after_policy_change`, and `test_off_mode_reports_unavailable`. Assert shadow output equals prior output, injected failures do not produce success, and changed validator versions/policies cannot reuse a previous verdict.
- [ ] Run `python -m pytest tests/test_validation_shadow.py -q`; confirm failures.
- [ ] Add config validation without rewriting user config. Initialize shadow context, capture candidate stage output, and attach reports. Cache only deterministic candidate results; validate on every use. In enforcement later, rejected candidate text must never advance the next stage's input. Do not rely on existing ignored `fact_check` cache keys for acceptance decisions.
- [ ] Run `python -m pytest tests/test_validation_shadow.py tests/test_fact_check.py tests/test_options.py -q`; verify shipped golden outputs are unchanged in shadow. Review and commit.

**Gate:** Full runs produce traceable decisions and honest coverage while returning legacy text in shadow mode.

## Phase 2 — Constrain and test rules

### Task 4: Add contracts, negative examples, and full-pipeline trials

**Files:** Create `tests/test_rule_contracts.py`, `tests/test_rule_pipeline_trial.py`. Extend `chartcleaner/rule_contracts.py` from Task 2. Modify `chartcleaner/stage_core.py`, `chartcleaner/stage_regex.py`, `chartcleaner/rule_meta.py`, `chartcleaner/rule_examples.py`, `chartcleaner/rule_preview.py`, `chartcleaner/regression_set.py`, `chartcleaner/config_schema_regex.py`, `chartcleaner/config_schema_base.py`, `chartcleaner/config_validator.py`, `chartcleaner/rule_sharing.py`, `chartcleaner/rule_signing.py`, `chartcleaner/learned_editor.py`, and `chartcleaner/rule_history.py`.

**Interfaces:** `contract_key(stage_id: str, pattern: str, replacement: str, flags: int) -> str`; `load_contracts(cfg: dict) -> dict[str, RuleContract]`; `trial_pipeline(cfg: dict, sid: str, entry: object, texts: list[str]) -> PipelineTrialReport` in `rule_preview.py`. `PipelineTrialReport` contains baseline/candidate comparisons, contract failures, regression differences, and text-free summary counts. Existing `preview` remains the quick stage-only preview.

- [ ] Add `test_match_start_in_scope_but_end_outside_is_rejected`, `test_unknown_section_cannot_authorize_scoped_removal`, `test_identical_pattern_different_replacement_has_distinct_contract`, `test_rule_interaction_deletes_newly_exposed_finding`, and `test_import_cannot_mark_contract_reviewed`. Assert whole-match scope checks, safe unknown-context handling, stable legacy IDs, detection of an upstream/downstream interaction, and review invalidation on import/edit.
- [ ] Run `python -m pytest tests/test_rule_contracts.py tests/test_rule_pipeline_trial.py -q`; confirm failures.
- [ ] Implement a versioned `rule_contracts` sidecar in configuration, including validator registration and round-trip support. Required examples: at least one intended match and one near-match that must survive before a newly contracted destructive rule is promoted. Legacy rules remain unreviewed in shadow mode. Preserve exceptions, tables, replacement backreferences, flags, and existing stage order.
- [ ] Extend preview/editor promotion to run the full candidate pipeline against synthetic examples and available encrypted known-good charts. Difference alone requires review; a failed mandatory counterexample prevents promotion. Never auto-update expected outputs. Include contract metadata in sharing/signing/history paths without exporting private chart examples.
- [ ] Run `python -m pytest tests/test_rule_contracts.py tests/test_rule_pipeline_trial.py tests/test_rule_sharing.py tests/test_options.py -q`; require failed counterexamples to prevent promotion and unchanged legacy behavior without contracts. Review and commit.

**Gate:** Newly promoted rules have explicit scope and counterexamples; a locally safe but globally destructive rule is visible before activation.

## Phase 3 — Validate clinical relationships

### Task 5: Add source-linked statements and relationship comparison

**Files:** Create `chartcleaner/clinical_statements.py`, `chartcleaner/statement_compare.py`, `tests/test_clinical_statements.py`, `tests/test_statement_compare.py`. Modify `chartcleaner/fact_check.py`, `chartcleaner/validation.py`, `chartcleaner/summarizer.py`, `chartcleaner/chart_qa.py`. Reuse `chart_dates.py`, `section_parser.py`, abbreviation safety helpers, and compactor parsers through narrow adapters.

**Interfaces:** Extraction/comparison signatures and dataclasses are defined in Shared contracts. Add `verify_claims(source: str, generated: str) -> ValidationReport` in `statement_compare.py`. Preserve `verify_output`'s existing return type during migration; return the richer report as an additive summary/Q&A field.

- [ ] Add `test_same_doses_swapped_between_drugs_are_unsupported`, `test_prior_day_value_does_not_support_current_day_claim`, `test_held_medication_cannot_become_active`, `test_possible_diagnosis_cannot_become_confirmed`, `test_family_history_cannot_become_patient_history`, and `test_deleted_negative_sentence_is_not_invisible`. Assert source spans identify the conflicting evidence; missing fields stay unknown.
- [ ] Add valid-change tests for approved abbreviations, whitespace/wrapping, preserved dose/route/frequency, unchanged uncertainty, repeated statements on the same event, and explicit latest-only reduction. Assert conflicting source notes are retained as conflicts, not resolved by the parser. Summary omission alone is not a failure, while an unsupported summary claim is.
- [ ] Run `python -m pytest tests/test_clinical_statements.py tests/test_statement_compare.py -q`; confirm expected failures.
- [ ] Implement deterministic supported-pattern extraction and contextual comparison. Start with medication tuples, labeled measurements, negation/laterality, certainty, and date/status relationships. Parsing ambiguity yields unresolved spans; count-based presence alone cannot upgrade a claim to supported. Map removals as well as rewrites. Explicit approved reductions retain traceable allowed-loss records.
- [ ] Integrate richer reports with cleaning, summary, and Q&A. Unchecked prose is visibly unchecked; do not suggest a numerical accuracy score for it. Use synthetic/stubbed model outputs in tests, without requiring an installed model.
- [ ] Run `python -m pytest tests/test_clinical_statements.py tests/test_statement_compare.py tests/test_phase7_trust.py tests/test_fact_check.py -q` and the development benchmark. Require all relationship sentinels to pass and record false rejections. Review and commit.

**Gate:** Evidence must support the relationship being asserted; every supported pattern has positive and negative fixtures. Additional patterns can be added later only with labels and tests.

## Phase 4 — Enforce and expose decisions

### Task 6: Accept or reject edits transactionally

**Files:** Modify `chartcleaner/validation.py`, `chartcleaner/engine.py`, `chartcleaner/stage_core.py`, `chartcleaner/stage_regex.py`, `chartcleaner/stage_cache.py`, `chartcleaner/fact_check.py`; create `tests/test_edit_enforcement.py`.

**Interfaces:** `decide_edit(proposal: EditProposal, report: ValidationReport, contract: RuleContract) -> str` returns the shared decision enum. `EditLedger.resolve` advances only accepted text. Opaque runners are atomic stage proposals until they emit precise edits.

- [ ] Add `test_reject_bad_edit_accept_neighboring_good_edit`, `test_downstream_rule_reads_retained_text`, `test_warm_and_cold_enforcement_are_identical`, `test_validator_failure_retains_original`, `test_required_redaction_rejection_withholds_release`, and `test_reduction_stage_name_does_not_authorize_unrelated_loss`. Assert accepted text, stage counts, provenance, and report all agree after rejection.
- [ ] Run `python -m pytest tests/test_edit_enforcement.py -q`; confirm failures.
- [ ] Implement opt-in enforcement at the regex proposal boundary; snapshot opaque stage output and reject the whole stage when necessary. Recompute dependent stage inputs/statistics from accepted text. A failed mandatory redaction marks release withheld even if retaining original text preserves clinical meaning. Implement operation-specific validation requirements so proven formatting does not require complete semantic parsing.
- [ ] Run `python -m pytest tests/test_edit_enforcement.py tests/test_validation_shadow.py tests/test_statement_compare.py -q`; compare off/shadow/enforce outputs and report all intentional differences. Review and commit.

### Task 7: Extend the existing review workflow across output surfaces

**Files:** Modify `chartcleaner/review_state.py`, `chartcleaner/service.py`, `chartcleaner/api.py`, `chartcleaner/mcp_server.py`, `chartcleaner/exporters.py`, `chartcleaner/batch.py`, `chartcleaner/clipboard_watcher.py`, `app_pages/clean.py`, `app_pages/settings.py`, and `app.py`; create `tests/test_validation_release.py`, `tests/test_validation_review_ui.py`. Preserve the pre-existing local edit to `app.py`.

**Interfaces:** Add `release_decision(result: RunResult, *, output_text: str, acknowledgement: str | None = None) -> ReleaseDecision` in `review_state.py`. Fields: `status` (`ready`, `review`, `blocked`), `reasons`, `acknowledgement_key`, and `deidentification_complete`. Derive the acknowledgment key from source/result/config digests plus validator version. Review acknowledgment cannot override failed mandatory de-identification into `ready`.

- [ ] Add parameterized `test_release_policy_consistent_across_surfaces` for UI copy, export, clipboard watcher, CLI, API, MCP, and batch. Assert failed mandatory redaction never appears in an ordinary successful cleaned-output payload; local review retrieval is explicitly marked as raw/unverified.
- [ ] Add `test_manual_edit_invalidates_acknowledgement`, `test_new_config_invalidates_acknowledgement`, `test_no_recognized_facts_does_not_show_full_support`, and `test_newer_result_cannot_inherit_old_async_review`. Assert revised results are revalidated, partial coverage stays visible, and stale asynchronous responses cannot release an older result.
- [ ] Run `python -m pytest tests/test_validation_release.py tests/test_validation_review_ui.py -q`; confirm failures.
- [ ] Extend the existing review panel and guarded-copy path with rejected edits, reasons, exact/coarse source links, intentional reductions, and processing versus relation coverage. Group related warnings by rule/region; do not interrupt for each match. Add shared release handling at every listed output path, including generated summaries and formatted copies. Keep source restoration local and clearly labeled.
- [ ] Run `python -m pytest tests/test_validation_release.py tests/test_validation_review_ui.py tests/test_review_state.py tests/test_clean_review_ui.py tests/test_service.py -q`; manually exercise synthetic accept/reject/edit/copy flows. Review and commit.

**Gate:** A core validation decision has the same meaning across every output path; the UI cannot be the only enforcement point.

## Phase 5 — Cover large charts

### Task 8: Replace size-based skipping with incremental coverage

**Files:** Create `chartcleaner/validation_windows.py`, `tests/test_validation_windows.py`, `tests/test_large_chart_validation.py`, `scripts/benchmark_validation.py`. Modify `chartcleaner/engine.py`, `chartcleaner/fact_check.py`, `chartcleaner/edit_provenance.py`, `chartcleaner/validation.py`, `chartcleaner/clinical_statements.py`, `chartcleaner/statement_compare.py`.

**Interfaces:** Window contract is defined above. Add `validate_document(source: str, candidate: str, *, ledger: EditLedger, context: ValidationContext) -> ValidationReport`; reuse statement indexes from accepted edits and reconcile boundary statements. Unsupported or exhausted windows return unresolved ranges rather than empty-success reports.

- [ ] Add `test_fact_split_across_window_keeps_its_subject`, `test_duplicate_overlap_counts_once`, `test_multiline_regex_behavior_unchanged`, `test_single_oversized_note_reports_partial`, `test_no_global_skip_above_one_million_chars`, and `test_changed_window_invalidates_related_index`. Compare findings against unchunked validation on supported small fixtures and multiple window sizes.
- [ ] Run `python -m pytest tests/test_validation_windows.py tests/test_large_chart_validation.py -q`; confirm failures.
- [ ] Implement bounded validation windows, carried note/section context, union-based coverage, and dependency invalidation for edits crossing boundaries. Retain original source offsets. Index source once; batch proposals by affected region to avoid full-document work per match. Keep a compact mapping structure rather than copies of whole charts for each edit.
- [ ] Remove the old 1,000,000-character blanket skip only after the replacement path reports coverage on every range. Replace the old skip-expectation test in `tests/test_fact_check.py`; retain tests for legitimate partial/failure states. Remove silent meaning-check limits by returning explicit incomplete coverage whenever a budget is reached.
- [ ] Implement a synthetic 100k/1M/5M benchmark and capture cold/warm time and peak memory on the same machine/config. Run `python scripts/benchmark_validation.py --sizes 100000 1000000 5000000 --report PATH`. Compare against the design's runtime/memory targets and report unsupported cases separately.
- [ ] Run `python -m pytest tests/test_validation_windows.py tests/test_large_chart_validation.py tests/test_fact_check.py tests/test_edit_enforcement.py -q`; require exact processing-range accounting and stable boundary outcomes. Review and commit.

**Gate:** Large charts either receive checks with reported coverage or explicitly remain partially validated; size alone never silently disables all checks.

## Phase 6 — Qualify and roll out

### Task 9: Freeze evidence and stage enforcement release

**Files:** Modify `.github/workflows/tests.yml`, `README.md`; create `.github/workflows/accuracy.yml`, `docs/validation/cleaning-accuracy-release.md`, `tests/test_accuracy_release.py`. Extend the evaluator/benchmark scripts only as needed for reproducible CI artifacts. Reference existing `logging_setup.py`, `secure_store.py`, and retention tests.

**Interfaces:** Release evidence includes base/candidate commits, fixture/config/validator digests, exact commands, category counts, observed failures, clinician label-review status, platform results, and performance environment. A machine-readable threshold check returns nonzero when a required gate fails; no auto-approval of new golden outputs.

- [ ] Add `test_release_fails_on_new_critical_escape`, `test_release_fails_on_excess_valid_edit_rejection`, `test_unreviewed_labels_cannot_certify_release`, and `test_logs_and_shared_reports_exclude_chart_text`. Assert the proposed numerical thresholds and provisional-label handling from the spec.
- [ ] Run `python -m pytest tests/test_accuracy_release.py -q`; confirm failures.
- [ ] Add deterministic development/sentinel checks to the existing platform CI workflow, preserving its tracked-file mutation check. Add scheduled/manual held-out and large performance jobs with downloadable synthetic aggregate artifacts. Keep timing gates out of ordinary short PR runs.
- [ ] Run `python -m pytest` and both evaluator splits under each relevant mode. Attribute pre-existing failures explicitly; do not call the suite green if it failed. Obtain clinician review of labels and representative errors, with a held-out assessment after tuning is complete.
- [ ] Pilot shadow and opt-in enforcement locally. Record aggregate actual-versus-predicted rejected edits, review burden, useful clutter removal, and user corrections. Private examples stay encrypted and local. Add new regression cases from corrections without modifying the frozen holdout to improve reported scores.
- [ ] Publish a release decision in the evidence document: keep shadow if any required gate fails; permit broader opt-in when gates pass. Change default to enforcement only after explicit product review of the evidence. Roll back a faulty validator to shadow while keeping coverage warnings and mandatory de-identification restrictions visible. Review and commit.

**Gate:** Zero critical sentinel escapes; held-out improvement and false-rejection/clutter targets met; clinician-reviewed labels; cross-platform checks pass; large-chart performance and coverage accounted for. This is bounded evidence, not a guarantee of clinical correctness.

## Implementation operating notes

- Work from `/Volumes/1TB ssd/Remix/chart cleaner/chart-cleaner`, not its parent. Before execution inspect Git status, active worktree artifacts, and current instructions; reuse an appropriate isolated checkout if available. Do not reset, clean, or include unrelated local edits in commits.
- Run commands from the repository root with its established virtual environment. Do not install a replacement environment as part of plan execution without first checking the existing setup.
- Each task's final commit contains only its reviewed files. `git add -A` is inappropriate in this checkout. Record the exact touched files and validation results in the commit/PR description.
- Use `pytest.MonkeyPatch.context()` when temporary patches need independent cleanup; never call `monkeypatch.undo()` and accidentally remove test storage redirects. Model test doubles accept `**kw` as required by the current streaming interfaces.
- Review proposed thresholds against baseline evidence; changes to thresholds require a recorded rationale and review, not quiet relaxation to pass.
- Estimated relative effort: Phase 0 medium; Phase 1 large; Phase 2 medium; Phase 3 large; Phase 4 large; Phase 5 large; Phase 6 medium. Calendar estimates require Phase 0 and provenance work to reveal parsing/review costs.

## Coverage of the five requested improvements

| Requested improvement | Implemented by |
|---|---|
| Reject risky edits automatically | Tasks 2–3, 6–7; qualified by Task 9 |
| Preserve relationships between facts | Task 5, including generated summaries/Q&A |
| Explicit regex boundaries and counterexamples | Task 4, enforced in Task 6 |
| Test preserved meaning | Task 1 and task-specific sentinels, release evaluation in Task 9 |
| Keep validation active on long charts | Task 8, release evidence in Task 9 |

## Plan review and execution handoff

This plan is ready for review, not a record of completed implementation. Proposed default policy, clinical labels, and release targets need review before enabling enforcement. Suggested execution method: task-by-task implementation with independent review, because the provenance, policy, and output-release interfaces are tightly coupled and errors could silently alter source meaning. A single implementer can also follow the same tasks sequentially with a final independent review. Select the method when authorizing implementation; creating these planning documents does not start execution or delegate work.

Self-review: all five requested improvements have task coverage; current integration anchors were inspected; new interfaces and modes are defined; the five review-focus cases have explicit tests; unresolved semantic parsing is a defined outcome; legacy configuration, caching, intentional reductions, de-identification, and long-chart failure behavior are addressed. No tests or accuracy benchmarks were executed while writing this plan.
