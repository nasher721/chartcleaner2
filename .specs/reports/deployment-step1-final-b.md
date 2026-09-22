# Step 1 Final Judge B

## Evidence

- Focused run: `./.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py` -> **30 passed, 1 skipped**. The skip is the Windows-only junction test on this macOS host. One pre-existing unknown `main_file` pytest option warning was emitted.
- `paths.py`, `storage.py`, `store.py`, and `tests/test_storage.py` compile successfully; `git diff --check` passes for the reviewed files.
- Platform helpers resolve macOS to `Library/Application Support/Chart Cleaner` and Windows to `%LOCALAPPDATA%/Chart Cleaner` (`chartcleaner/paths.py:17-28`). Source checkout constants remain rooted at `BASE_DIR` when not frozen (`chartcleaner/store.py:21-40`).
- Frozen startup calls portable migration once before creating mutable directories and invokes the known `learned_rules` schema migration (`chartcleaner/store.py:61-70`).
- Migration validates the source root and descendants with `lstat`, rejects symlinks/reparse points and wrong file types, stages only the four allowed portable items, revalidates the source, and atomically installs/restores an empty destination (`chartcleaner/storage.py:24-55`, `121-193`).
- Tests cover fresh migration and byte preservation, populated destination precedence, malformed config, descendant/root/reparse link rejection, interrupted copy, config backup/failure, wrong directory type, empty-destination replacement/restoration, parent failure, validator failure, and frozen startup wiring (`tests/test_storage.py:27-222`).
- Direct edge probes found two residual issues: a symlink-loop source raises raw `RuntimeError` from `source.resolve()` before validation (`chartcleaner/storage.py:129-133`), and a no-op config migration rewrites bytes and creates a backup (`chartcleaner/storage.py:196-224`).

## Findings

### [MEDIUM] Symlink-loop paths can crash migration before the safety guard

File: `chartcleaner/storage.py:129-133`

`source.resolve()` and `destination.resolve()` run before `_safe_entry()` and are
outside the function's failure handling. A source symlink loop produced
`RuntimeError: Symlink loop ...` rather than returning the preserved `False`
failure result. A malformed portable sibling or destination path can therefore
abort frozen startup instead of leaving the current data untouched.

Fix: perform guarded `resolve()` calls inside the existing protected path, catch
`OSError`/`RuntimeError`, and return `False`; add a symlink-loop regression test.

### [MEDIUM] No-op config migration changes user bytes on every frozen startup

File: `chartcleaner/storage.py:196-224`, `chartcleaner/store.py:63-68`

`migrate_config()` validates and then always backs up and rewrites the config,
even when `migrate(cfg)` returns an identical dictionary. A direct probe showed a
formatted config was rewritten with different indentation and the `.bak` was
created on a no-op migration. `ensure_dirs()` calls this on every frozen process
startup. This needlessly changes user configuration bytes and replaces the
previous backup despite no schema change.

Fix: compare the migrated object with the original and return without writing or
backing up when there is no known change; add a byte-for-byte no-op migration test.

### [LOW] Config migration does not reject a destination config symlink

File: `chartcleaner/storage.py:196-214`

Portable source links and the destination root are guarded, but
`migrate_config()` reads and backs up `path` without first requiring a regular,
non-symlink file. A user-data `config.json` symlink can make migration read an
external file before replacing the link. The replacement does not overwrite the
external target, but it crosses the intended data boundary.

Fix: require `_safe_entry(path, directory=False)` before reading or backing up;
return `False` for symlink/reparse configs and add a focused fixture.

## Scores

| Criterion | Weight | Score | Weighted |
|---|---:|---:|---:|
| Correctness | 0.35 | 4.2 | 1.470 |
| Safety/data preservation | 0.30 | 4.0 | 1.200 |
| Tests | 0.25 | 4.3 | 1.075 |
| Simplicity/integration | 0.10 | 4.5 | 0.450 |
| **Overall** | **1.00** |  | **4.195 / 5.0** |

## Verdict

**FAIL** — the principal Step 1 requirements and the newly claimed root/reparse
guards are implemented and exercised, but the symlink-loop crash and repeated
no-op config rewrite remain below the critical 4.5/5.0 threshold. Guard path
resolution, preserve no-op config bytes, add the two regression tests, and rerun
the focused suite before marking Step 1 complete.
