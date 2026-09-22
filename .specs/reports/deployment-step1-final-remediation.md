# Step 1 final remediation

Addressed the three findings in `deployment-step1-final-b.md`.

- `chartcleaner/storage.py`: guarded portable source/destination resolution; rejected existing unsafe destination roots; rejected symlink/reparse config and backup paths; preserved config bytes and the existing backup on no-op migrations; compared against an independent original parse so in-place migrations still apply; contained migration callback failures; completed serialization before creating the migration backup.
- `tests/test_storage.py`: covered source/destination symlink loops, no-op byte/backup preservation, in-place migration, config/backup links, config reparse points, callback/serialization/backup/atomic-replace failures, and repeated frozen startup preserving the previous real migration backup.

Validation: `./.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py` -> **41 passed, 1 skipped**. Windows junction integration remains skipped on macOS. The existing unknown `main_file` pytest option warning remains. `py_compile` for paths/storage/store/tests and `git diff --check` passed.

Pre-existing repository `config.json` was never edited; its SHA-256 before and after was `ce470295e099ae780baf753f78d7a72ca056c76ef6043fe31fb0b3ec4262dca9`.

No task status changes or commits. Independent judge confirmation remains the orchestrator's next gate.
