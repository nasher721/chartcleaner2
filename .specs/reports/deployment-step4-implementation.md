# Step 4 implementation report

Implemented the Settings update lifecycle in `app.py` and persisted update preferences in `chartcleaner/store.py`.

- Settings now shows version, last status, automatic-check preference, and manual checks.
- Automatic checks start after NiceGUI startup and run through `run.io_bound`; cleaning remains available.
- Manual update confirmation stages through `UpdateClient`, then calls the Step 3 companion handoff only after staging succeeds.
- Source checkouts report local source mode without making update requests.
- Startup probes the loopback Chart Cleaner route after NiceGUI startup; only a confirmed listening route then calls the updater's environment-gated `write_health_marker`.
- About text states that clinical data stays local and update checks are the only built-in outbound request.
- Focused page/lifecycle coverage was added to `tests/test_pages.py`.

Verification:

- `python3 -m py_compile app.py tests/test_pages.py chartcleaner/updater.py` — passed.
- `.venv/bin/python -m pytest -q tests/test_pages.py tests/test_update_ui_lifecycle.py` — 15 passed.
- The system interpreter lacks NiceGUI; verification used the repository `.venv`.
