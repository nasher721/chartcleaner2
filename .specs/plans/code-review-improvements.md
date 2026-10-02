# Review and improvement plan

Scope: the two latest code commits (medical abbreviation conversion and local-AI UI resilience), plus their shared callers. Preserve the existing personal `config.json` changes and DocJev draft. Do not change the supplied CSV or add dependencies.

1. Run independent code/security and architecture reviews; reproduce actionable findings before changing behavior. Baseline: 348 tests passed, 3 skipped.
2. Add a regression for an AI request finishing after the chart or cleaning mode changes. Snapshot the request's chart and discard a result whose source is no longer current, using existing state rather than a new service layer.
3. Fix legacy stage-order migration: when `line_length` was moved earlier, automatically added abbreviations must still run after the existing cleaning rules. Preserve explicitly configured abbreviation positions. Reproduce using a partial legacy order and a full reordered legacy order before the engine edit.
4. Run focused regressions, the full pytest suite, bytecode compilation, and available static checks. Re-review the final diff and record remaining risks and verification limits.

Completed: both main regressions fixed, dictionary preview gaps addressed, and final suite passed (362 passed, 3 skipped). Independent code review: APPROVE; architecture: WATCH for existing shared-workspace and broad-dictionary tradeoffs. Full results are recorded in `.specs/reviews/2026-10-01-code-review.md`.
