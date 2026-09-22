# Step 1 Judge B

## Evidence

- Focused storage tests pass when run with the repository's NiceGUI root conftest excluded: `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py` -> **7 passed**.
- Existing persistence tests also pass: `... tests/test_storage.py tests/test_store.py` -> **21 passed** (with an unrelated `main_file` config warning).
- `paths.py`, `storage.py`, and `store.py` compile successfully.
- Source-checkout behavior remains repository-relative because `store.py` selects `BASE_DIR` unless `paths.is_frozen()` is true. Frozen paths match the macOS and Windows locations in the design, and startup calls `ensure_dirs()` before seeding bundled defaults.
- Portable migration validates JSON, rejects symlinks and disallowed relative names, copies through a temporary sibling, validates the copied config, and uses `os.replace`; existing non-empty destinations are left untouched and source bytes are retained.
- The focused tests cover platform path helpers, normal migration, existing destination precedence, malformed JSON/symlink rejection, an injected copy failure, successful config backup/write, and validation failure.

## Findings

### [HIGH] A malformed portable directory can be installed as a file

File: `chartcleaner/storage.py:72-87`

`present` includes `data`, `presets`, and `custom_rules` whenever they exist, but only checks `_tree_is_safe()` for entries where `item.is_dir()` is true. A regular file named `data` (or either other directory) passes validation, is copied to the temporary tree, and is atomically installed. On the next startup `store.ensure_dirs()` calls `DATA_DIR.mkdir(...)` and raises `FileExistsError`, leaving the frozen app unusable. Reject any present non-config item unless `item.is_dir()` is true, and test this malformed-input case while asserting the destination remains absent and the source bytes are unchanged.

### [MEDIUM] The migration validation is vulnerable to source changes between scan and copy

File: `chartcleaner/storage.py:67-87`

The implementation scans the source tree for symlinks, then later calls `copytree(..., symlinks=False)` and `copy2`. A file or directory can be swapped for a symlink after `_tree_is_safe()` returns; the copy then follows the link and can import data outside the portable root. Use no-follow/open-based copying or revalidate the temporary tree and reject links before install; add a link-swap or equivalent adversarial test if migration runs in a directory writable by other processes.

### [MEDIUM] Configuration migration is not connected to the application config boundary

File: `chartcleaner/storage.py:104-128`; `chartcleaner/store.py:348-414`

`migrate_config()` satisfies the isolated unit tests, but no production caller uses it. Application config reads/writes still go through `engine.load_config()` and `engine.save_config()`/`store.rotate_config_backup()`. As a result, a future schema migration will not run automatically at startup, and the Step 1 acceptance requirement is only present as an unused helper. Integrate the helper at the config-load/startup boundary with an explicit schema/version migration map, or document and implement the actual caller before marking the step complete.

### [LOW] Focused tests omit several acceptance cases

File: `tests/test_storage.py:14-92`

There is no frozen startup integration test, permissions-error test, config backup/write failure test, existing-empty-destination race test, or malformed regular-file directory test. The seven tests are useful and pass, but they do not demonstrate every case listed in the task/design. Add the smallest tests for the production failure paths, especially the regular-file directory case above.

## Scores

| Criterion | Weight | Score | Weighted |
|---|---:|---:|---:|
| Correctness | 0.35 | 3.5 | 1.225 |
| Safety/data preservation | 0.30 | 3.7 | 1.110 |
| Tests | 0.25 | 3.4 | 0.850 |
| Simplicity/integration | 0.10 | 3.6 | 0.360 |
| **Overall** | **1.00** |  | **3.545 / 5.0** |

## Verdict

**FAIL** — below the required 4.5/5.0 threshold. Fix the malformed-directory acceptance bug and connect or explicitly complete the configuration migration path, then expand focused tests for the listed failure cases. The source `config.json` working-tree change was not modified.

## Cycle 2 — independent recheck

### Evidence

- Focused storage run: `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py` -> **14 passed** (one pre-existing unknown `main_file` config-option warning).
- Storage plus persistence run: `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py` -> **28 passed** (same warning).
- The new tests cover regular-file directory rejection, replacement of an existing empty destination, restoration when install fails, atomic-install failure preservation, and frozen startup migration/config schema wiring (`tests/test_storage.py:98-188`).
- Direct probe: a symlink used as the `source` root was accepted and copied (`migrate_portable_data(link_to_external_dir, destination, valid) == True`). `_tree_is_safe()` scans `root.rglob("*")` but never validates the root itself (`chartcleaner/storage.py:24-37`), and `migrate_portable_data()` checks only `source.is_dir()` plus that descendant scan (`:114-121`).
- The empty-destination rename/install/restore sequence is now present at `chartcleaner/storage.py:147-174`; the existing-empty and failed-install tests exercise it.

### Remaining blocker

#### [HIGH] Portable source root symlink can import an external directory

File: `chartcleaner/storage.py:24-37`, `chartcleaner/storage.py:114-121`

The validation rejects descendant symlinks/reparse points, but a symlink passed as
the portable root is followed by `source.is_dir()`, `source / "config.json"`,
`rglob()`, and the no-follow copy traversal. A portable sibling path replaced by
a directory symlink can therefore migrate files from outside the intended
portable installation. The direct probe copied an external `config.json` through
such a root symlink.

Fix: validate the root with `source.lstat()` before any follow-based operation and
reject symlink/reparse roots; use an opened directory handle/no-follow traversal
if the portable directory can be modified by another process. Add a root-symlink
fixture that asserts the destination is untouched and source bytes remain intact.

### Cycle 2 scores

| Criterion | Weight | Score | Weighted |
|---|---:|---:|---:|
| Correctness | 0.35 | 4.0 | 1.400 |
| Safety/data preservation | 0.30 | 3.5 | 1.050 |
| Tests | 0.25 | 4.0 | 1.000 |
| Simplicity/integration | 0.10 | 4.2 | 0.420 |
| **Overall** | **1.00** |  | **3.87 / 5.0** |

### Cycle 2 verdict

**FAIL** — the staged-install restoration and migration wiring fixes are verified,
but the remaining root-symlink boundary is a safety blocker and the weighted
score remains below the required 4.5/5.0 threshold. Reject symlink/reparse source
roots, add the regression fixture, and rerun the focused suite before marking Step
1 complete. The source `config.json` working-tree change remains untouched.
