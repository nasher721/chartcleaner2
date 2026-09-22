# Step 4 remediation

Changed only `app.py`, `tests/test_update_ui_lifecycle.py`, and `tests/test_pages.py` plus this report. No core updater, packaging, config, status/DONE, or commit changes.

- Readiness probes execute in NiceGUI's IO thread pool, so the loopback server can answer. Health acknowledgement and automatic checks occur only after readiness succeeds; timeout acknowledges neither.
- A throttled result preserves the persisted timestamp and previous actual status. Disabled automatic checks preserve both; manual checks still run.
- Companion signature/copy/spawn work runs off the UI loop. Only a successful handoff proceeds to app exit. Failed handoff removes its own known staging transaction and gives a generic error.
- Added real same-loop HTTP readiness coverage, preferences persisted/reloaded at 23-hour startup intervals, disabled/manual checks, off-thread handoff and exit order, failed signature staging/handoff, unready startup, and actual confirmation/cancellation clicks.

Regression evidence before app changes: throttle, handoff-thread, and handoff-cleanup tests all failed (3 failed); earlier exact-function readiness repro blocked for 1.52 seconds and returned false while the off-thread probe returned true.

Fresh verification after changes:

- `.venv/bin/python -m pytest -q tests/test_update_ui_lifecycle.py tests/test_pages.py`: **23 passed in 3.73s**.
- `.venv/bin/python -m py_compile app.py tests/test_update_ui_lifecycle.py tests/test_pages.py`: PASS.
- `git diff --check`: PASS.
- `config.json` before/after SHA-256 unchanged: `ce470295e099ae780baf753f78d7a72ca056c76ef6043fe31fb0b3ec4262dca9`.

Remaining boundaries: trusted signed native update/relaunch, clean-platform checks, and release publication require separate evidence. This report is implementation evidence, not an independent judge approval.
