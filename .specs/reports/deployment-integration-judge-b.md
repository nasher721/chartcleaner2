# Independent integration judge B

Scope: Step 2 panel B and Step 4 panel A, against `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md` and the source design. No other judge report read. Source/configuration unchanged.

Fresh command: `.venv/bin/python -m pytest -q tests/test_update.py tests/test_pages.py tests/test_update_ui_lifecycle.py` — **44 passed in 5.95s**.

## Step 2 — PASS, 4.620/5

| Criterion | Evidence | Score | Weight |
|---|---|---:|---:|
| Correctness | Strict manifest/version/platform validation; HTTPS redirect checks before following; required rollback disk context; size/hash verification; actual macOS requirement compiler and PowerShell parser exercised. Native signed release acceptance remains a separate external gate. | 4.6 | .35 |
| Safety | Staging transactions clean up on verification failures; no mutable data inputs; native signature requirement and publisher checks; deferred symlinks validated as a complete graph including cycles and chained escapes. | 4.7 | .30 |
| Tests | 29 release-client tests pass; malformed metadata, redirects, disk/hash/signature failures, framework links and native command boundaries covered. Real trusted production signatures not available. | 4.6 | .25 |
| Simplicity | Standard library, small immutable metadata records, no runtime dependency addition. | 4.5 | .10 |

Weighted: 1.610 + 1.410 + 1.150 + .450 = **4.620/5**, PASS against 4.5.

## Step 4 — FAIL, 3.360/5

| Criterion | Evidence | Score | Weight |
|---|---|---:|---:|
| Correctness | Settings controls, confirmation, source separation, persisted status and off-thread metadata check exist. Health probe blocks its own server loop, and throttled launches advance the persisted timestamp forever. | 2.8 | .35 |
| Safety | Only metadata enters update client and staged verification precedes handoff/exit. Startup loop blocking threatens cleaning availability; rollback may trigger even on healthy new version. | 3.6 | .30 |
| Tests | 15 UI/lifecycle tests pass but mocked probe hides same-loop HTTP failure. No handoff success/failure, disabled-auto, confirmation interaction, or repeated-start throttle regression coverage. | 3.4 | .25 |
| Simplicity | Reuses existing Settings, dialog, preference and NiceGUI facilities; no framework addition. | 4.5 | .10 |

Weighted: .980 + 1.080 + .850 + .450 = **3.360/5**, FAIL against 4.5.

### Required fixes

1. **P1 — app.py:202: synchronous health probe blocks the same event loop serving `/`.** `_is_chart_cleaner` uses blocking `urlopen(timeout=1.5)` inside `_after_server_ready`. A running same-loop HTTP server cannot answer while that call executes. The 300 probes may take roughly eight minutes; health marker is never acknowledged and updater times out. Move the probe to `run.io_bound`/a thread and test with an actual same-loop local server, not a lambda probe.
2. **P2 — app.py:180: throttled checks overwrite the last actual check timestamp.** Every start less than 24 hours after the previous launch persists `status.checked_at` (the current time), preventing future automatic requests indefinitely for frequent users. Keep the previous timestamp and last actual result for `throttled`; test several restart intervals with preference reload.

### Executable reproduction evidence

Loaded the exact `_is_chart_cleaner` and `_check_for_updates` function ASTs from `app.py`, without importing the application or changing data. Served `Chart Cleaner` via `asyncio.start_server` on the same loop. Direct probe versus `await asyncio.to_thread(probe, port)` produced:

```text
same-event-loop probe: False elapsed: 1.52
off-event-loop probe: True
```

Invoked exact `_check_for_updates` with real `UpdateClient`, fake clock and in-memory persistence, initial actual-check timestamp 100.0. Startup intervals each 23 hours, transport call count tracked:

```text
hours since actual check: 23.0 state: throttled persisted timestamp: 82900.0 requests: 0
hours since actual check: 46.0 state: throttled persisted timestamp: 165700.0 requests: 0
```

Additional review notes: `handoff_update` performs signature checks/copy synchronously after staged download; keeping this off the UI loop would match the availability contract. On its failure, the UI displays a generic safe error but does not delete already-successful staging. Native signed release, clean target OS, and publication remain externally BLOCKED, not passes established by these tests.
