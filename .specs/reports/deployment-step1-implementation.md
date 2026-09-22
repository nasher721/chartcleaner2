# Step 1 storage implementation report

## Scope and cleanup plan

1. Keep `chartcleaner.store` as the compatibility boundary used by the app and
   tests; change only its frozen path resolution.
2. Add pure platform path helpers in `chartcleaner.paths` so macOS, Windows,
   and source-checkout behavior can be tested without changing host state.
3. Add `chartcleaner.storage` for portable migration and config migration. The
   migration validates before install, copies through a temporary sibling, and
   leaves source files in place.
4. Route existing store constants through the frozen user-data directory while
   preserving repository-relative constants for source checkouts.
5. Add focused tests for platform paths, precedence, malformed data, unsafe
   links, interrupted copies, config backup, and byte preservation.

## Public boundary for Step 2

- `chartcleaner.paths.user_data_dir(platform=None, env=None, home=None)`
- `chartcleaner.paths.portable_root(executable=None, meipass=None)`
- `chartcleaner.storage.migrate_portable_data(source, destination, validate=None)`
- `chartcleaner.storage.migrate_config(path, validate, migrate=None)`

Step 2 should treat `store.CONFIG_PATH`, `store.DATA_DIR`,
`store.PRESETS_DIR`, and `store.CUSTOM_RULES_DIR` as the only application data
paths it needs. The updater must not call or mutate the storage migration layer.

## Evidence

- `21 passed` from `.venv/bin/python -m pytest -q tests/test_storage.py tests/test_store.py`.
- Source `config.json` was not modified by this work.
- No commit or release action was performed.

## Known boundary

Frozen builds resolve mutable state under the platform user-data directory;
`store.BASE_DIR` remains the frozen resource/install location for bundled
read-only assets such as `sample_chart.txt`.
