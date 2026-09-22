# Step 1 cycle 3 — independent judge A

Verdict: **FAIL, 4.35/5.0** (critical threshold 4.5).

| Criterion | Weight | Evidence summary | Score |
|---|---:|---|---:|
| Correctness | .35 | Frozen mutable paths and startup schema wiring are present; the latest `_safe_entry` guard now checks source, item, tree, and destination roots. Empty-destination restore and ordinary migrations pass. | 4.7 |
| Safety/data preservation | .30 | Root symlink and reparse regressions pass; staged copying preserves source bytes and rejects malformed directories. Native Windows operation remains unexecuted here. | 4.6 |
| Tests | .25 | 30 storage/store tests pass, but the sole Windows junction test is broken and vacuous before reaching its error. A descendant-link check was accidentally moved under that Windows-only test. | 3.5 |
| Simplicity/integration | .10 | Standard-library path/storage boundary and existing store constants remain in use; no new dependency. | 4.5 |

Weighted score: `4.7*.35 + 4.6*.30 + 3.5*.25 + 4.5*.10 = 4.35`.

## Fresh evidence

- `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py`: **30 passed, 1 skipped**, existing `main_file` config warning.
- `rtk pytest -q tests/test_storage.py` exited 1 without useful output in this environment; the explicit repository interpreter run above passed.
- Calling the Windows test body with `subprocess.run` replaced solely to create a directory symlink reproduces **`NameError: name 'destination' is not defined`**. This demonstrates the Python defect, not native Windows junction semantics.
- This review changed only this report; `config.json` and implementation files were untouched.

## Required fix

`tests/test_storage.py:82-93`: define destination, create valid config in the external directory before constructing the junction, and assert unchanged external bytes and absent destination. Currently line 88 references an undefined variable; moreover, the junction target has no config at the first migration call, so rejection would also occur without the junction guard. The trailing descendant-symlink assertions at lines 90-93 belong in a separate platform-independent test; otherwise they are skipped on macOS and require Windows symlink privileges unnecessarily.

Latest production guards address the cycle-2 root-symlink/reparse findings. Rejudge after repairing these tests. Native Windows execution remains an external platform gate, not a PASS.

## Correction recheck — final verdict

**PASS — 4.625/5.0**, above the critical threshold of 4.5.

The fixture now defines `destination`, starts from valid external configuration, asserts its original bytes remain intact, and leaves the destination absent. The descendant-symlink assertions again run on the ordinary platform-independent path. The previously identified undefined-variable and vacuous-fixture defects are resolved.

Fresh run: `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py` -> **30 passed, 1 skipped**, one existing `main_file` config warning. Calling the repaired Windows test body with only junction creation replaced by a symlink stand-in also passes; this verifies Python control flow only, not native Windows behavior.

| Criterion | Weight | Final score | Evidence |
|---|---:|---:|---|
| Correctness | .35 | 4.7 | Platform storage paths, frozen migration wiring, validated staging, existing destination precedence, and restoration checks pass. |
| Safety/data preservation | .30 | 4.6 | Root/item/tree/destination guards reject links/reparse paths; source sentinels and failed-install preservation are exercised. |
| Tests | .25 | 4.6 | Listed ordinary storage cases pass and the Windows fixture is now valid; native Windows execution remains unavailable on this host. |
| Simplicity/integration | .10 | 4.5 | Existing store constants and standard-library implementation are retained without a new dependency. |

Calculation: `4.7*.35 + 4.6*.30 + 4.6*.25 + 4.5*.10 = 4.625`.

Scope: local Step 1 implementation passes this review. Clean/native Windows verification remains **BLOCKED**, not PASS. No application code or user configuration was modified by this judge.
