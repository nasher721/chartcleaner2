# Cleaning accuracy — release evidence

Status: **shadow mode is the default; enforcement is opt-in.** This page
records the measured evidence behind that decision and what must happen before
enforcement can become the default. Bounded evidence, not a guarantee of
clinical correctness.

## What was measured and how

* **Corpus**: 120 synthetic cases (`tests/accuracy/cases.json`), 12 categories
  × 10, ≥ 4 valid and 4 harmful per category; splits frozen in
  `tests/accuracy/splits.json` before any tuning (96 development / 24
  held-out; held-out = v5/h5 of each category, never edited to improve
  scores). No real chart text is in Git. Labels are **provisional** until
  clinician review.
* **Commands** (from the repo root, committed venv or equivalent):
  * `python scripts/evaluate_cleaning_accuracy.py --split development|held-out --mode off|shadow|enforce --report PATH`
  * `python scripts/check_accuracy_release.py --report <enforce.json> --baseline tests/accuracy/baseline-<split>.json [--require NAME] --out gate.json`
  * `python scripts/benchmark_validation.py --sizes 100000 1000000 5000000 --report PATH`
* **Digests** (fixture `tests/accuracy/cases.json`, evaluator v1, validator
  version string in every `RunResult`): see the baseline reports committed at
  `tests/accuracy/baseline-development.json` / `baseline-held-out.json`.
  Regenerating the corpus changes the fixture digest and invalidates
  comparisons against the old baseline.
* Scheduled evidence runs: `.github/workflows/accuracy.yml` (weekly +
  manual): both splits × three modes with downloadable aggregate artifacts,
  plus the large-chart benchmark. The hard CI gate is
  `no_new_critical_escapes` only; timing gates stay out of per-PR runs.

## Baseline (mode `off` — legacy behaviour, frozen 2026-10-21)

| Split | Escapes | Valid rejected | Required kept | Unsupported claims | Clutter removed |
|---|---|---|---|---|---|
| development (96) | 48/48 (100%) | 0/48 | 119/176 (67.6%) | 20/20 | 17/17 (100%) |
| held-out (24) | 12/12 (100%) | 0/12 | 33/46 (71.7%) | 5/5 | 3/3 (100%) |

Every harmful edit ships when validation is off — that is the failure this
work exists to measure and reduce.

## Candidate (mode `enforce`, 2026-10-21, Python 3.13 / macOS arm64)

| Split | Escapes | Valid rejected | Required kept | Unsupported claims | Valid-case clutter |
|---|---|---|---|---|---|
| development | 1/48 (2.1%) | 0/48 (0.0%) | 176/176 (100%) | 0/20 | 12/12 (100%) |
| held-out | 0/12 (0.0%) | 0/12 (0.0%) | 46/46 (100%) | 0/5 | 3/3 (100%) |

The single development escape is `rc-h4` (a redaction reverted so the
identifiers are back). Candidate-judging cannot fix that by editing text — the
correct outcome is *withholding release*, which the shipped release policy
does (`review_state.release_decision` → `blocked` across UI copy, export,
clipboard watcher, CLI, API, MCP and batch). It is counted as an escape here
because the benchmark's per-case judge models edit decisions only; the release
surface has its own test coverage (`tests/test_validation_release.py`).

Escape reduction vs baseline: **97.9%** development, **100%** held-out (target
≥ 50%). Valid-edit rejection **0%** (target ≤ 5%, denominator reported).
Authorized clutter removal on valid cases **100% of baseline** (target ≥ 95%).

Shadow mode returns byte-identical output to `off` (golden tests) while
recording everything above as advisories.

## Large-chart performance (synthetic charts, same machine)

`scripts/benchmark_validation.py`, cold/warm, 2026-10-21 (macOS, Python 3.13):

| Size | off warm | shadow warm | ratio | peak mem (off → shadow) |
|---|---|---|---|---|
| 100k | 233 ms | 805 ms | ≈ 3.5× | 2.0 → 2.8 MB |
| 1M | 1.96 s | 7.96 s | ≈ 4.1× | 14.6 → 27.1 MB |
| 5M | 11.4 s | 47.6 s | ≈ 4.2× | 72.0 → 115.6 MB |

(5M memory ≈ 1.6× off — inside the ≤ 2× target; runtimes ≈ 4× off and the 5M/1M
runtime ratio ≈ 6× is inside the ≤ 7× shape target, but the absolute
multipliers exceed the ≤ 1.5× target.) Coverage is `complete`/`partial` at
every size — the 1,000,000-character blanket skip is gone
(`tests/test_large_chart_validation.py`). **Not yet meeting the design's
warm-runtime target** (≤ 1.5× at ≤ 100k): the synthetic corpus is a worst case
(hundreds of near-identical notes, so the dedup and abbreviation stages touch
the whole document and their validation pays one extraction of everything
they changed). Real-chart measurements from the scheduled job will tell
whether the gap holds outside the worst case; until then this is recorded as
a known gap, not a pass.

## Validation optimization checkpoint (2026-10-24)

Profiling identified repeated whole-region abbreviation expansion in
`_unendorsed_abbreviations`. The implementation now skips irrelevant proposals and computes the
expanded/source-folded text once per proposal. An initial local run suggested a 20% warm-shadow
improvement, but a same-environment baseline/optimized rerun did not reproduce it: baseline warm
off/shadow/enforce times were 0.800s/2.190s/2.181s versus 0.784s/2.225s/2.194s after the change.
Treat the difference as noise; **no reliable speedup has been demonstrated**. Shadow remains about
2.8× off-mode runtime, so **the ≤1.5× target is still not met**. These are local synthetic
measurements, not representative real-chart or clinical validation.

## Label review status

**Provisional.** `tests/test_accuracy_release.py` makes certification fail
while labels are unreviewed, whatever the numbers say. Required before any
default-enforcement proposal: a clinician reviews the 120 labels and the
representative errors, recorded in a labels file consumed by
`scripts/check_accuracy_release.py --labels …`.

## Pilot (before enforcement could become default)

Run locally, opt-in via Settings → Cleaning validation → enforce:

1. Shadow for at least a week of real use; record advisory counts per clean.
2. Enforce for a week; record actual-vs-predicted rejected edits, review
   burden (how many cleans needed a look), clutter still removed, and user
   corrections (each correction becomes a regression case; the frozen
   held-out split is never edited to improve scores).
3. Add the pilot's false rejections as cases; re-run both splits.

## Release decision (2026-10-21)

* **Default stays `shadow`.** Accuracy gates pass on both splits except the
  clinician-label gate, which is the point of the gate; large-chart
  performance is above target on the worst-case synthetic; the pilot has not
  run.
* **Enforcement is available opt-in** (Settings, or
  `"cleaning_validation": {"mode": "enforce"}`) for users who prefer kept
  passages over shipped risk.
* **Rollback**: switch the mode back to `shadow` (a faulty validator degrades
  to advisory while coverage warnings and the mandatory-redaction release
  rules stay visible); `"off"` reports unavailable coverage and changes
  nothing else.
* Threshold changes require a recorded rationale here and in
  `chartcleaner.accuracy_eval.THRESHOLDS`; the gate echoes any override it was
  given, so relaxations are visible in the artifacts.
