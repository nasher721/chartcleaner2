# Step 3 independent judge A

Task: `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md`, Step 3. CLAUDE_PLUGIN_ROOT unavailable. Reviewed actual updater/installer entrypoints, release pins, updater core, shared archive/signature functions, and updater tests. No application edits.

## Findings

1. **P1 — Partial backup cleanup can destroy a healthy installation on the next attempt.** `chartcleaner/updater.py:385-386` recursively deletes the rollback tree after health success, without first retiring its recovery status. A mid-delete permission/file-lock failure leaves a partial `.rollback` tree. Lines 359-364 interpret that tree as an interrupted update and restore it over the healthy application. On Windows the recovery check verifies only the old main executable, so missing DLLs/resources are undetected. A disposable Windows-shaped transaction reproduced: first run `installation_failed` after successful new-app health; retry `rolled_back`; installed executable is the old executable and its required `python.dll` is absent. Native verification was mocked for fixture binaries; the deletion/recovery transaction was real. Retire the recovery backup atomically after health before destructive cleanup, and treat retired-tree cleanup failure independently from installation success. Add this two-run regression.

2. **P1 — Handoff executes an onedir runtime whose supporting code is unverified.** `chartcleaner/updater.py:409-414` verifies only `chart-cleaner-updater.exe`, copies the complete installed runtime, then verifies that same executable again. Authenticode does not seal its sibling `_internal/python.dll`, `.pyd`, or Python resource files. Therefore the companion's independent manifest/archive verification executes only after potentially modified supporting code has loaded. A disposable fixture confirmed modified DLL bytes were copied and launched while the only two signature targets were the unchanged companion EXE. Prefer sourcing the complete helper runtime from the independently pinned-manifest/hash-verified staged archive, with native signature verification retained, or another verifiable whole-runtime integrity boundary. Add a supporting-code tamper regression. This is a trust-boundary gap, not a request for blanket new signing dependencies.

## Verification and scores

- `.venv/bin/python -m pytest -q tests/test_updater.py tests/test_update.py`: **67 passed**, 4.78 seconds.
- `.venv/bin/python -m py_compile chartcleaner/updater.py chartcleaner/release_identity.py updater_entry.py installer_entry.py`: passed.
- Existing tests demonstrate archive rejection, canonical paths, storage sentinels, shutdown/health failure rollback, nonce freshness, OS locking, independent manifest use, embedded setup restrictions, and copied runtime placement. Real child-process health/termination checks also pass.
- Both findings above are uncovered by the existing suite. Windows replacement/shortcut/signature behavior is simulated; signed clean-platform checks remain external gates.

| Criterion | Weight | Evidence summary | Score |
|---|---:|---|---:|
| Correctness | .35 | Normal transactions and bounded rollback work; post-health cleanup causes incorrect recovery. | 3.6 |
| Safety/data preservation | .30 | Data boundary and archive checks are strong; partial recovery can replace good code and helper supporting-code trust is incomplete. | 3.3 |
| Tests | .25 | 67 focused passing tests, including real child checks; two important production paths lack regressions. | 4.1 |
| Simplicity/integration | .10 | One stdlib core, narrow platform branches, clear four-argument interface. | 4.5 |

Weighted score: **3.725/5.0 — FAIL** against 4.5. Fix the two findings and rerun panel verification.

Native Apple signing/notarization/Gatekeeper, Windows Authenticode/reputation/file locks, and clean per-user installation remain **BLOCKED** without fresh target-platform evidence. Test signature stubs are not evidence of those gates passing. UI health readiness is being reviewed/fixed separately.
