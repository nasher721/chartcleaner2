# Independent final panel A

Reviewed 2026-09-22 against `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md` and its source design. This reviewer authored no implementation code and did not read other final panel opinions. Scope: Step 3 panel A, Step 4 panel A, Step 5 panel B. No task completion checkboxes were changed.

## Fresh evidence

- `.venv/bin/python -m pytest -q tests/test_updater.py tests/test_update.py tests/test_pages.py tests/test_update_ui_lifecycle.py tests/test_release_tools.py`: **105 passed in 8.76s**.
- `.venv/bin/python -m py_compile app.py chartcleaner/updater.py scripts/build_release.py scripts/release_smoke.py scripts/prepare_signing.py installer_entry.py updater_entry.py`: passed.
- `git diff --check`: passed.
- `config.json` SHA-256 before and after review: `ce470295e099ae780baf753f78d7a72ca056c76ef6043fe31fb0b3ec4262dca9`, matching the recorded pre-existing user configuration baseline.
- Inspected native unsigned build evidence at `../release-validation/unsigned-app-smoke.json`: frozen UI PASS, version 2.3.0, isolated user data created, port 62407. This evidence does not establish native signature or clean-machine acceptance.

## Step 3: companion and transactional installation

The complete parent staging -> archive-reverified companion -> independent metadata verification -> install transaction -> launch -> health/rollback flow was traced. The companion now comes from the hash-verified extracted release, including supporting DLL/Python resources; it never borrows the installed loose runtime. The transaction compares the actual installed version, enforces minimum support, validates payload version/signatures, waits for shutdown, and performs same-volume replacement with a bounded rollback. A pending backup takes precedence over a damaged interrupted app. Successful health acknowledgement atomically retires the rollback directory before recursive cleanup, so partial deletion cannot become a recovery candidate. Tests cover these remediations plus failure isolation, process termination, path restrictions, checksum/signature rejection, locking and sentinel preservation.

| Criterion | Evidence / limitation | Score |
|---|---|---:|
| Correctness, 0.35 | Replacement, installer reuse, version checks, health handshake and rollback are coherent; target-native execution remains a separate gate. | 4.7 |
| Safety, 0.30 | Archive sealing protects the companion runtime; backup retirement prevents partial-backup restoration; user-data sentinels survive transaction cases. | 4.7 |
| Tests, 0.25 | Focused suite passes with meaningful injected failures and a real failed-child process termination case. | 4.6 |
| Simplicity, 0.10 | Shared stdlib core and narrow platform branches; some repeated extraction is deliberate trust-boundary work. | 4.4 |

Weighted score: **4.645/5 — PASS** against 4.5.

Nonblocking limitation: successful `handoff_update` leaves its `companion-*` directory containing the verified archive and extracted release. Repeated updates can accumulate considerable disk use. A future cleanup may reclaim only retired companion directories after their process exits; it must preserve Windows runtime lifetime and avoid deleting active transactions.

## Step 4: update UI and health lifecycle

Settings displays version, status, last-check timestamp, automatic preference and manual action. Manual checking remains usable when automatic checks are disabled. Metadata, server probing, staging and companion startup run off the UI event loop. The persisted timestamp is preserved when throttled. Confirmation precedes staging, and exit occurs only after a successful verified handoff; failures retain the current process and use generic messages. Startup health is acknowledged after initialized module state and a successful loopback route probe. About wording now accurately distinguishes local clinical processing from built-in update requests.

| Criterion | Evidence / limitation | Score |
|---|---|---:|
| Correctness, 0.35 | Ready/disabled/manual/throttled/error/handoff/health paths are implemented and verified. | 4.6 |
| Safety, 0.30 | Off-loop I/O preserves responsiveness; failed stage/handoff never exits; no clinical payload is passed to update APIs. | 4.7 |
| Tests, 0.25 | Lifecycle tests exercise same-loop server readiness, persistent throttling, failed staging and handoff ordering. | 4.6 |
| Simplicity, 0.10 | Existing NiceGUI pages, prefs and dialog patterns reused. | 4.6 |

Weighted score: **4.630/5 — PASS** against 4.5.

Nonblocking UI limitation: the visible last-check timestamp is rendered when Settings opens; manual checks update the status immediately, while the timestamp label refreshes on revisiting the page. The persisted timestamp is correct.

## Step 5: release tooling and packaging

Traced spec product selection, companion inclusion, trust-pin generation, app signing before archive hashing, sealed installer construction, installer signing, native smoke sequence, and final two-platform publication gate. Missing credentials fail closed; unsigned local builds are explicitly excluded from release packaging. Workflow creates a draft first, uses temporary per-user smoke roots and synthetic sentinels, requires both platform reports, and hashes final signed archives before publication. Windows uses a signed onefile installer to seal embedded metadata/resources. Release documentation accurately describes its source-transaction smoke harness, signed seed prerequisite and remaining production frozen-companion smoke gate.

| Criterion | Evidence / limitation | Score |
|---|---|---:|
| Correctness, 0.35 | Build/sign/zip/embed/sign/smoke/hash/publish sequence and platform artifact paths align. | 4.6 |
| Safety, 0.30 | Compiled trust pins, secret-only signing inputs, temporary smoke homes and two-platform fail-closed gates. | 4.7 |
| Tests, 0.25 | Offline archive/manifest/credential tests pass; native unsigned macOS UI evidence exists; unavailable signed native gates are not claimed passed. | 4.5 |
| Simplicity, 0.10 | Existing PyInstaller stack extended through a shared spec and stdlib scripts. | 4.6 |

Weighted score: **4.605/5 — PASS** for implementation readiness against 4.5.

## External gates: BLOCKED, not implementation failures

- Apple Developer ID signing, notarization/stapling and fresh clean Apple Silicon Gatekeeper acceptance.
- Windows x64 native signed build/install, Authenticode publisher verification and clean Windows reputation presentation.
- Signed previous-release seed and both native release-smoke runs.
- Production frozen-companion/GitHub-network upgrade and real release publication.

These require credentials, target runners or publication evidence that this review did not have. Implementation PASS does not mean release readiness or publication PASS. No blocking code defect was found in this final scope.

## Final Step 3 delta recheck: offline interrupted recovery

Independently inspected the final `_install_transaction` ordering without consulting the other panel's verdict. Pending local rollback recovery now runs under the update lock, waits for the caller to exit, verifies the backup's native signature and version, restores it, and relaunches exactly once before reaching staged-archive validation or release metadata fetching. Cleanup guards `archive is not None`, so recovery does not inspect or delete an untrusted archive argument. A rejected backup remains present and is not launched. The existing atomic retirement of a healthy backup remains intact.

Fresh bounded command: `.venv/bin/python -m pytest -q tests/test_updater.py -k 'recover or interrupted or retirement'` — **10 passed, 42 deselected in 0.11s**. This covers missing and corrupt archives with a release-client trap that fails on any network-discovery attempt, incomplete new installation, untrusted archive arguments, backup signature rejection, replacement interruption, and retirement failure/partial cleanup. Fixture teardown checks user-data sentinels byte-for-byte. `py_compile` of updater and its tests and `git diff --check` passed. The current `config.json` SHA-256 remains `ce470295e099ae780baf753f78d7a72ca056c76ef6043fe31fb0b3ec4262dca9`.

| Criterion | Final delta evidence | Score |
|---|---|---:|
| Correctness, 0.35 | Interrupted restoration no longer depends on a download or readable new installation. | 4.7 |
| Safety, 0.30 | Lock, exit wait, native backup validation and one relaunch precede network/archive handling. | 4.7 |
| Tests, 0.25 | New offline recovery and rejection regressions pass alongside retirement/interruption checks. | 4.7 |
| Simplicity, 0.10 | Existing transaction and restore helper reused with an early recovery branch. | 4.5 |

Final Step 3 weighted score: **4.680/5 — PASS**, superseding the earlier Step 3 score. Step 4 and Step 5 verdicts and external release gates are unchanged. No blocking defect found in this delta.
