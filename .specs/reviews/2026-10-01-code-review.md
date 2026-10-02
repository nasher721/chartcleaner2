# Chart Cleaner review and improvements

Reviewed the 13 files changed by `f649297` and `ecdbc9b`, with affected pipeline and UI callers. The pre-existing personal `config.json` edit and DocJev draft were outside the write scope.

## Findings addressed

| Severity | Location | Problem and resolution |
| --- | --- | --- |
| HIGH | `app.py:704`, `app.py:784` | Summary and Q&A workers read mutable chart state and published without checking the source. A response for a previous chart could enter the current chart's results. Both handlers now capture the source text and result object before background work, then discard responses when either changes. |
| HIGH | `chartcleaner/engine.py:395` | Auto-inserting abbreviations before an early `line_length` stage could consume terms before PHI or learned rules saw them. Missing abbreviation stages now follow the resolved cleaning rules, preceding long-line formatting only when it is terminal. Explicitly chosen abbreviation positions remain unchanged. |
| LOW | `app.py:1731` | The dictionary editor fell through to a redundant generic message. It now uses the existing exclusive branch chain. |
| LOW | `app.py:2051` | The pipeline's sample tester omitted dictionary match counts. It now reuses `abbreviate` to report those matches. |

## Changes and simplifications

- `app.py`: source snapshots and late-result guards for both AI handlers; dictionary preview count; remove editor fallthrough.
- `chartcleaner/engine.py`: defer legacy abbreviation insertion until the existing stage order is resolved.
- `tests/test_abbreviation_modes.py`: legacy partial/full custom orders and explicit abbreviation placement.
- `tests/test_ai_ui_lifecycle.py`: delayed AI responses after mode switch, clear, re-clean, and in-place text restoration; normal completion. The tests await the actual handler task before asserting state.
- `tests/test_pages.py`: dictionary sample count and editor-message regression.
- `README.md`: explain the legacy-order tradeoff.
- `.specs/plans/code-review-improvements.md`: review and regression-first repair plan.

The fixes reuse existing result identity, pipeline ordering, and dictionary matching. No new dependencies, services, configuration format, or dictionary filters were added.

## Verification

- Baseline: **348 passed, 3 skipped**.
- Legacy-order regression: **2 failed before the fix**, then all **65 focused engine/rule tests passed**.
- Combined abbreviation/rule/page checks: **86 passed**.
- AI regressions replayed against the original `ecdbc9b:app.py` in an isolated test process: **8 stale-response cases failed, 2 ordinary-completion cases passed**, with 2 additional teardown errors from stale updates to deleted widgets. No source rollback was needed.
- Final full suite: **362 passed, 3 skipped**, in 14.88 seconds. The skips are 2 optional medspaCy checks (dependency absent) and 1 Windows junction check. Five existing PDF/SWIG deprecation warnings remain.
- Final Python bytecode compilation and `git diff --check` passed.
- No Python lint/type-check tool is configured or installed in this environment. The available code-intelligence diagnostics call failed with `Transport closed`; compilation and executable tests do not establish static type-check coverage.

## Remaining architecture watchlist

1. `chartcleaner/appstate.py:19`: chart state is process-wide. The patch prevents stale responses after state changes but does not create isolated workspaces for multiple browser tabs or clients. The current design remains a single shared desktop workspace.
2. `chartcleaner/abbreviations.py:66`: the supplied dictionary intentionally includes broad shorthand such as `For → x` and `Right/Renal → R`. This can reduce readability or introduce ambiguous shorthand. All supplied mappings remain intact; filtering them would change the accepted behavior.

No native packaged-app build, Windows execution, or live-model inference was performed for these local changes. UI behavior was exercised through NiceGUI's Python user-test harness rather than a visual browser session.

## Review synthesis

- Code/security lane: **APPROVE**; all confirmed findings addressed.
- Architecture lane: **WATCH**; no unresolved blocker in the final production changes.
- Final recommendation: **COMMENT** under the skill's deterministic synthesis (`APPROVE` plus architecture `WATCH`). The remaining items are explicit design tradeoffs, not newly introduced unaddressed defects.
