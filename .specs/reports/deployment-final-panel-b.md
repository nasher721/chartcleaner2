# Independent final panel B — Steps 3 and 4

Task: `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md`.
Design: `.specs/plans/cross-platform-deployment-updater.design.md`.
CLAUDE_PLUGIN_ROOT unavailable. Read current implementation and remediation reports only; no other final panel opinions. Code, task status, and config were not modified.

## Verdict and scores

| Step | Correctness (.35) | Safety (.30) | Tests (.25) | Simplicity (.10) | Weighted | Threshold | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| 3 | 4.7 | 4.6 | 4.7 | 4.6 | **4.660** | 4.5 | **PASS** |
| 4 | 4.7 | 4.7 | 4.6 | 4.7 | **4.675** | 4.5 | **PASS** |

Step 3 evidence: four-argument interface, strict per-user install/staging boundaries, independently verified archive bytes and publishers, external companion runtime, lock, process wait, nonce/version health handshake, bounded rollback, atomic retirement of confirmed backup, shared initial-install transaction, and Windows Start-menu construction are implemented. Disposable tests preserve settings/history/token/custom-rule sentinels. The independently reverified interrupted-recovery branch now restores the trusted local backup before new-release archive validation, metadata fetch, or extraction. No blocking finding remains in this slice.

Step 4 evidence: Settings exposes current version/status/preference/manual action; checks, readiness HTTP probe, staging, and handoff execute outside the UI loop. Disabled/throttled checks preserve actual timestamps; confirmed and successfully staged/handoff updates alone exit. Initialization precedes route readiness and the health marker; a real same-loop HTTP fixture proves the probe can complete. Error notices stay generic, manual check codes remain retryable, and About describes the clinical-data boundary. No blocking finding in this slice.

## Resolved finding and independent re-verification

The first panel evaluation scored Step 3 **4.345 / FAIL** because an interrupted app-to-backup rename followed by an offline check returned `network_error`, leaving the normal install path absent and no relaunch. The required ordering fix is now verified.

Current `_install_transaction` enters the OS lock, detects the pending backup, waits for the relevant PID, verifies native signature and backup version, restores the normal installation, relaunches once, and returns `rolled_back`. `_staged_archive`, `release_client`, and extraction occur only on the normal update path. Invalid backups remain unmodified and are not launched. Archive cleanup is guarded by whether an archive was actually validated, so an unused external archive argument is not touched.

Independent disposable checks exercised three conditions with release metadata unavailable. Native signature results were mocked, as in the fixture; the check order was asserted as wait then signature. Each also exercised the fixture's byte-for-byte sentinel assertion:

| Scenario | Result | Restored version | Relaunches | Network requests |
|---|---|---|---:|---:|
| Offline, valid archive | rolled_back | 2.3.0 | 1 | 0 |
| Offline, missing archive | rolled_back | 2.3.0 | 1 | 0 |
| Offline, corrupt archive | rolled_back | 2.3.0 | 1 | 0 |

The regression tests additionally reject an unverified backup while preserving it, and pass a user-data file as the unused archive argument to prove it remains untouched. Existing locks, normal update verification, retirement, rollback, and signature failure tests remain passing. No application code or test edits were made by this reviewer.

## Fresh verification

Latest Step 3 re-verification:

- `.venv/bin/python -m pytest -q tests/test_updater.py tests/test_update.py`: **81 passed in 4.68s**.
- `py_compile` on updater and updater tests: PASS.
- `git diff --check`: PASS.
- Config SHA-256 unchanged from the value below.

Earlier Step 3/4 integrated evidence (Step 4 was not changed in this recovery fix):

- `.venv/bin/python -m pytest -q tests/test_updater.py tests/test_update.py tests/test_update_ui_lifecycle.py tests/test_pages.py`: **100 passed in 9.33s**.
- `py_compile` on updater, release identity, both entries, app, and focused updater/UI/page tests: PASS.
- `git diff --check`: PASS.
- `config.json` SHA-256 before and after: `ce470295e099ae780baf753f78d7a72ca056c76ef6043fe31fb0b3ec4262dca9`.

## External limits

Actual signed macOS/Windows bundles, notarization, Gatekeeper, Authenticode, clean Windows kernel file locks/Start menu execution, and publication remain **BLOCKED pending target evidence**. Windows branches here are simulated; real subprocess exit/health/termination checks run on this host. No installed user app was launched. Successful companion and best-effort retired-backup staging may remain on disk as documented; these are not recovery-eligible trees and do not contain user data.
