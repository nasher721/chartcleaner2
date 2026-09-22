# Step 1 Final Panel B — External user-data storage and migration

## Verdict

**PASS** — weighted score **4.5/5.0**, meeting the critical threshold.

## Evidence

- `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py`: **41 passed, 1 skipped**, one existing unknown `main_file` pytest-option warning. The skipped test is explicitly Windows-only junction semantics.
- `.venv/bin/python -m py_compile chartcleaner/paths.py chartcleaner/storage.py chartcleaner/store.py tests/test_storage.py tests/test_store.py`: passed.
- `git diff --check`: passed.
- Source mode still uses the repository root, while frozen mode routes mutable state to macOS Application Support or Windows `%LOCALAPPDATA%` (`chartcleaner/store.py:33-40`, `chartcleaner/paths.py:17-28`).
- Frozen startup performs portable migration and the one known schema migration before creating data directories (`chartcleaner/store.py:61-70`).
- Migration validates source and destination roots, rejects symlink/reparse/special entries, copies only `config.json`, `data`, `presets`, and `custom_rules` through a temporary tree, rechecks the source, and restores an existing empty destination if replacement fails (`chartcleaner/storage.py:24-55`, `121-199`).
- Config migration preserves no-op bytes and existing backups, detects in-place callback mutation, writes a completed temporary file before backing up, and retains the old file across callback, serialization, backup, and replace failures (`chartcleaner/storage.py:202-243`).

## Rubric scores

| Criterion | Weight | Score | Justification |
|---|---:|---:|---|
| Correctness | 0.35 | 4.6 | Platform paths, source-checkout compatibility, portable precedence, selected-item copying, schema migration, atomic replacement, and config backup/validation behavior match the design. Startup wiring is exercised, including repeated frozen startup without backup churn (`tests/test_storage.py:287-318`). The Windows junction path is structurally guarded but not executed on this macOS host. |
| Safety/data preservation | 0.30 | 4.5 | Root and descendant symlink/reparse checks, no-follow file copying, special-file rejection, source recheck, temporary cleanup, destination restoration, byte-for-byte source preservation, and config failure isolation are implemented and tested (`chartcleaner/storage.py:45-55`, `83-119`, `121-199`, `202-243`). A true Windows junction/reparse integration run remains unavailable here; the unit-level reparse guard is tested independently. |
| Tests | 0.25 | 4.5 | The focused tests cover fresh frozen startup, populated data/presets/rules migration, destination precedence, malformed config, interrupted and permission-like failures, schema/no-op/in-place migration, symlink/reparse roots, callback/serialization/backup/replace failures, atomic destination restore, and byte preservation (`tests/test_storage.py:27-318`). One Windows-only junction test is skipped by platform condition rather than marked passed. |
| Simplicity/integration | 0.10 | 4.4 | The existing `store` boundary and standard library are reused; safety helpers are narrowly scoped to the required migration boundary. The displaced empty-directory transaction and no-follow copier add justified complexity, while `portable_root()` still carries an unused `meipass` parameter (`chartcleaner/paths.py:31-42`). |

Weighted calculation: `(4.6 × .35) + (4.5 × .30) + (4.5 × .25) + (4.4 × .10) = 4.525`, rounded to **4.5/5.0**.

## Evidence boundary

The skipped Windows junction test must remain a release/platform gate until run on Windows. No application code, commit, push, or release action was performed during this review.
