# Step 3 remediation of judge A findings

Task: `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md`, Step 3. CLAUDE_PLUGIN_ROOT unavailable. Ownership: `chartcleaner/updater.py`, `tests/test_updater.py`, this report. No commits or task completion markers.

## Changes

- After successful health, the rollback tree is atomically renamed into the disposable transaction directory before any recursive deletion. A partial cleanup leaves only an ineligible retired tree and the healthy new installation. Failure of the retirement rename still stops the new child and restores/relaunches the complete old tree once.
- `handoff_update` now independently fetches the pinned release manifest, copies/checks the exact archive size/hash, checks extraction space, safely extracts, and verifies native signatures and payload version before launching the companion from that verified release. It never copies installed helper DLLs or Python resources. Both handoff and replacement reuse `_verified_payload`; the four-argument API is unchanged.
- Because the verified helper is compiled at the target version, downgrade and minimum-supported-version checks now compare the actual installed payload version. A pending rollback takes precedence over reading a potentially incomplete new installation. Recovery reports the actual restored version.

## Regression evidence

- Added a two-transaction regression: simulated partial retired-backup cleanup leaves an incomplete old tree; the next upgrade succeeds and never restores that tree.
- Added retirement rename failure rollback, recovery when the interrupted new installation has no readable version, target-version helper success, actual-install downgrade rejection, and minimum-version enforcement.
- Added Windows supporting-DLL tamper regression: modified installed DLL is ignored and the launched runtime contains the independently hash-verified release DLL.
- Added handoff checksum, signature, and metadata failures; each prevents child launch and cleans the companion staging directory. Existing real subprocess health/termination tests remain passing.

Fresh commands:

```text
.venv/bin/python -m pytest -q tests/test_updater.py tests/test_update.py
77 passed in 4.84s

.venv/bin/python -m py_compile chartcleaner/updater.py tests/test_updater.py
passed

git diff --check
passed
```

## Limits and external gates

Native signature calls in disposable fixtures are mocked; actual Apple notarization/Gatekeeper and Windows signing/file-lock execution still require independent target evidence. The verified extracted companion release remains in staging while its process is running; Windows cannot reliably delete its own mapped runtime. A failed best-effort cleanup can also leave retired backup bytes there. Neither leftover is eligible for recovery or touches application user data. Future inactive staging cleanup can reclaim these files; no background maintenance layer was added.

Independent panel re-verification is required; this implementation report makes no approval claim.

## Follow-up: interrupted recovery is independent of release availability

Panel B identified that the pending rollback path still required a successful metadata request and valid new archive. Recovery now runs first under the updater OS lock: wait for the supplied process to exit, verify the existing backup's native identity and version, restore it, and relaunch it exactly once. It returns `rolled_back` / `interrupted_update_recovered` immediately and never attempts to update the recovered running app in the same transaction. Neither the metadata client nor the staged-archive validator is used on this path.

Regression tests cover the app-to-backup rename followed by offline metadata and missing/corrupt new archives, an untrusted archive argument pointing outside staging (untouched), and rejected backup signatures preserving the backup and invalid archive without launching. PID wait precedes backup verification. The ordinary no-backup update path still validates its archive and manifest before replacement.

Latest focused verification: `.venv/bin/python -m pytest -q tests/test_updater.py tests/test_update.py` — **81 passed**. `py_compile` for both owned Python files and `git diff --check` pass. Independent panel and final full-suite verification remain separate.
