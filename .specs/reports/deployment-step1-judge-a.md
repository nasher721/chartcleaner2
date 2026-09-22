# Step 1 Judge A — External user-data storage and migration

## Verdict

**FAIL** — weighted score **3.7/5.0**, below the critical threshold of 4.5.

The implementation has a sound basic boundary and preserves the existing source-checkout constants, and the focused storage/store tests pass. It does not yet demonstrate all acceptance cases required for a critical storage migration, and a few failure-mode details need tightening before this step is ready.

## Evidence

- `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py`: **21 passed**, one pre-existing/irrelevant `main_file` pytest config warning.
- `.venv/bin/python -m compileall -q chartcleaner tests`: passed.
- `git diff --check`: passed.
- The working-tree `config.json` change is present and was not altered by this review.
- `store.ensure_dirs()` invokes portable migration before creating the frozen data directories (`chartcleaner/store.py:54-61`), while source mode still uses the repository root (`chartcleaner/store.py:33-40`).

## Rubric scores

| Criterion | Weight | Score | Justification |
|---|---:|---:|---|
| Correctness | 0.35 | 4.0 | `user_data_dir()` maps macOS to `~/Library/Application Support/Chart Cleaner/` and Windows to `%LOCALAPPDATA%/Chart Cleaner/` (`chartcleaner/paths.py:17-28`). Frozen store constants route mutable config/data/presets/rules there, while source mode remains repository-relative (`chartcleaner/store.py:33-40`). Migration validates before copying, stages in a sibling temporary directory, and uses `os.replace()` for the normal fresh-destination case (`chartcleaner/storage.py:64-101`). The destination-precedence and config migration behavior is present, but an existing empty destination is removed before replacement (`chartcleaner/storage.py:90-94`), so that branch is not truly atomic and can leave the destination absent if replacement is interrupted. The config migration helper is also not called by application startup; only portable migration is integrated through `ensure_dirs()` (`chartcleaner/store.py:54-61`). |
| Safety/data preservation | 0.30 | 3.9 | Source bytes are copied rather than removed, source symlinks and unsafe descendant names are rejected, temporary trees are cleaned on copy errors, and configuration writes are backed up and atomically replaced (`chartcleaner/storage.py:23-31`, `56-101`, `104-128`). The validator does not reject non-regular special files, and it does not explicitly reject Windows junction/reparse-point directories; `path.is_symlink()` alone is not a complete unsafe-copy guard on Windows (`chartcleaner/storage.py:23-31`). Validation exceptions from a supplied validator are not contained by `_valid_config()` (`chartcleaner/storage.py:43-53`), so a validator failure can escape instead of returning a preserved-data failure. |
| Tests | 3.0 | 0.25 | The 7 storage tests cover platform strings, successful migration, non-empty destination precedence, malformed JSON, a symlink, one interrupted `copy2`, and successful/invalid config migration (`tests/test_storage.py:14-91`). They do not cover an actual frozen `store.ensure_dirs()` startup, permission failures, content in `presets/` and `custom_rules/`, failure while replacing an existing empty destination, validator exceptions, or Windows-specific path/reparse behavior. The source preservation assertion only includes the populated config/data files (`tests/test_storage.py:22-37`). |
| Simplicity/integration | 0.10 | 4.3 | The change is small, standard-library-only, and keeps the existing `store` compatibility boundary. The unused `meipass` parameter in `portable_root()` (`chartcleaner/paths.py:31-42`) and the split between an integrated portable migration and an uncalled config migration helper add minor dead surface, but no new runtime dependency or source-checkout rewrite was introduced. |

Weighted calculation: `(4.0 × .35) + (3.9 × .30) + (3.0 × .25) + (4.3 × .10) = 3.725`, rounded to **3.7/5.0**.

## Required fixes

1. Add focused tests for permission-denied/failed-directory creation, actual frozen startup routing through `store.ensure_dirs()`, populated `presets` and `custom_rules` byte preservation, and a failed atomic replacement. Keep the source `config.json` working-tree change untouched.
2. Make the existing-empty-destination path preserve atomicity, or explicitly prevent migration into an existing destination directory rather than removing it before `os.replace()`.
3. Harden the tree validator against special files and Windows junction/reparse points, and convert validator exceptions into a failed migration with the source and destination unchanged.
4. Confirm where schema migrations are invoked. If Step 1 owns startup schema migration, wire `migrate_config()` into the startup path; if it is intentionally only a reusable boundary for a later step, document that boundary and add a caller-level test proving current startup does not reset configuration.

## Round 2 — independent rejudge after fixes

### Verdict

**FAIL** — weighted score **4.2/5.0**, below the critical threshold of 4.5.

The second implementation closes the earlier coverage and integration gaps: 28 focused storage/store tests pass, migration is now wired into frozen startup, populated presets/rules are copied byte-for-byte, failed installs preserve the empty destination, and special files/validator exceptions are covered. One material cross-platform path-safety gap remains in the actual copy path: a top-level Windows junction/reparse-point directory can pass the root checks and then be traversed by `_copy_tree_nofollow()`.

### Fresh evidence

- `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py`: **28 passed**, one existing `main_file` pytest config warning.
- The source working-tree `config.json` modification remains present and untouched by this rejudge.
- `store.ensure_dirs()` now runs `migrate_portable_data()` and then the known `_migrate_config_schema()` only once for frozen startup (`chartcleaner/store.py:61-70`).
- Populated `data/`, `presets/`, and `custom_rules/` are now asserted byte-for-byte (`tests/test_storage.py:22-40`), and failed replacement/permission/validator cases are covered (`tests/test_storage.py:118-162`).

### Round 2 rubric scores

| Criterion | Weight | Score | Justification |
|---|---:|---:|---|
| Correctness | 0.35 | 4.4 | Platform paths and source-checkout routing remain correct (`chartcleaner/paths.py:17-42`, `chartcleaner/store.py:33-40`). Frozen startup now performs portable migration, known schema migration, and directory setup in the required order (`chartcleaner/store.py:61-70`). Empty destinations are displaced and restored around replacement (`chartcleaner/storage.py:147-174`), and copied config/data/presets/rules are validated before install. The remaining concern is that migration success is still reported even if cleanup of the displaced empty directory fails after the new destination is installed (`chartcleaner/storage.py:159-161`), and migration/config failures are intentionally ignored by `ensure_dirs()` without a local status. |
| Safety/data preservation | 0.30 | 4.1 | No-follow file copying, regular-file checks, symlink/reparse checks for descendants, source recheck, staging cleanup, and validator exception handling materially improve preservation (`chartcleaner/storage.py:24-103`, `141-174`). However, `_tree_is_safe(item)` does not inspect the top-level directory's own reparse attributes, and `_copy_tree_nofollow()` checks only `source.is_symlink()` for its root (`chartcleaner/storage.py:85-99`). A Windows junction/reparse-point `data`, `presets`, or `custom_rules` directory can therefore be traversed as a normal directory and copy outside the intended portable tree. The destination root has the same gap because `destination.is_symlink()` does not cover all reparse points (`chartcleaner/storage.py:147-149`). |
| Tests | 0.25 | 4.1 | The focused suite now covers all named ordinary cases: fresh frozen startup, portable migration, populated data/presets/rules, existing destination precedence, malformed config, interrupted copy, permission-like parent failure, schema migration, validator failure, empty-destination install/restore, and byte preservation (`tests/test_storage.py:14-188`). It still lacks a true Windows junction/reparse fixture, an actual OS permission-denied test, an injected failure during post-install displaced-directory cleanup, and a startup test proving an invalid existing config is preserved byte-for-byte while defaults are not substituted. |
| Simplicity/integration | 0.10 | 4.3 | The boundary remains small and standard-library-only, and the existing store constants continue to serve the app. The extra no-follow copy helpers are justified by the safety requirement. Minor complexity remains in the displaced-directory transaction and the unused `meipass` argument in `portable_root()` (`chartcleaner/paths.py:31-42`). |

Weighted calculation: `(4.4 × .35) + (4.1 × .30) + (4.1 × .25) + (4.3 × .10) = 4.225`, rounded to **4.2/5.0**.

### Remaining blocker

Reject top-level Windows junction/reparse-point roots in `_tree_is_safe()`, `_copy_tree_nofollow()`, and the destination-root check before treating those paths as directories. Add a Windows-targeted regression test, then rerun the focused suite and rejudge.
