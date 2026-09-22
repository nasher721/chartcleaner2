# Cross-platform deployment updater

Source design: `.specs/plans/cross-platform-deployment-updater.design.md`

This task implements signed per-user deployment and one-click updates for Apple Silicon macOS and 64-bit Windows while preserving user data and keeping clinical content local. Existing source-checkout behavior and the current `config.json` working-tree change must remain intact.

## Implementation Process

### Step 1: External user-data storage and migration [DONE]

**Type:** Critical — panel of 2 judges, target 4.5/5.0

**Dependencies:** None.

**Expected artifacts:**

- `chartcleaner/paths.py` or the smallest existing module boundary suitable for platform user-data resolution.
- `chartcleaner/storage.py` or the smallest existing storage/migration boundary.
- Focused tests under `tests/` covering fresh frozen startup, portable migration, existing destination precedence, malformed configuration, interrupted copying, permissions, schema backup/migration, and byte-for-byte preservation.
- Any minimal frozen-build configuration updates required to keep development paths repository-relative.

**Acceptance criteria:**

- Frozen macOS data resolves to `~/Library/Application Support/Chart Cleaner/`; frozen Windows data resolves to `%LOCALAPPDATA%\\Chart Cleaner\\`; source checkouts retain current repository-relative behavior.
- First frozen launch copies only `config.json`, `data/`, `presets/`, and `custom_rules/` from a portable sibling when the destination is empty, through a temporary validated directory and atomic install.
- Existing destination data always wins; no automatic merge occurs. Source files remain available as rollback copies.
- Configuration migrations back up first, apply only known changes, validate, and retain the previous configuration on failure. Factory defaults never reset user configuration.
- Path and filename validation prevents traversal or unsafe copies, and all failure paths preserve the original data.

**Verification rubric:**

| Criterion | Weight | Description |
|---|---:|---|
| Correctness | 0.35 | Platform paths, portable detection, precedence, migration, validation, and atomic install match the design. |
| Safety/data preservation | 0.30 | Original data survives malformed input, interruption, permissions errors, and migration failure byte-for-byte. |
| Tests | 0.25 | Focused tests cover every listed storage case and run without external services. |
| Simplicity/integration | 0.10 | Reuses existing config/backup behavior and does not alter source-checkout semantics unnecessarily. |

### Step 2: Release manifest, download staging, and signature verification [DONE]

**Type:** Critical — panel of 2 judges, target 4.5/5.0

**Dependencies:** Step 1 data boundary agreed; implementation may proceed in parallel with Step 1 once interfaces are fixed.

**Expected artifacts:**

- `chartcleaner/update.py` using the Python standard library.
- Focused tests and local HTTP fixtures under `tests/`.
- `update-manifest.json` schema/example or fixture documentation.
- Privacy-safe update status/error representation.

**Acceptance criteria:**

- Stable public manifest parsing rejects malformed data, downgrades, incompatible minimum versions, missing exact platform assets, invalid URLs, sizes, and hashes.
- Semantic-version comparison and platform selection support only `macos-arm64` and `windows-x64` in this release.
- Checks run asynchronously at startup readiness and are throttled to at most once per 24 hours; manual checks remain available when automatic checks are disabled.
- Downloads use temporary files, validate expected byte count and SHA-256, check available disk space for archive/extracted/rollback needs, and delete interrupted or failed staging files.
- Native signature verification is mandatory and has no bypass: Developer ID/notarization assessment on macOS and expected Authenticode publisher on Windows.
- Requests and diagnostics contain only normal HTTPS/GitHub metadata, current version, timestamps, platform, stages, and generic error codes; no chart/config/path/token/custom-rule content.

**Verification rubric:**

| Criterion | Weight | Description |
|---|---:|---|
| Correctness | 0.35 | Manifest, version, platform, throttling, staging, checksum, disk, and signature contracts are implemented exactly. |
| Safety/data preservation | 0.30 | Existing app/data stay untouched until every verification passes; failures clean staging and return safe status. |
| Tests | 0.25 | Local fixture HTTP tests cover valid/invalid manifests, downgrade, throttle, checksum, disk, and diagnostic privacy. |
| Simplicity/integration | 0.10 | Standard library and existing version/config patterns are reused without a new runtime dependency. |

### Step 3: Companion updater, per-user install, replacement, and rollback [DONE]

**Type:** Critical — panel of 2 judges, target 4.5/5.0

**Dependencies:** Steps 1–2 staged-asset and application/data boundaries.

**Expected artifacts:**

- `chartcleaner/updater.py` or a narrowly scoped updater core.
- `chart-cleaner-updater` frozen entry point and platform-specific minimal branches.
- Per-user installer definitions/scripts for `Install Chart Cleaner.app` and `ChartCleanerSetup.exe`, reusing the updater core.
- Focused disposable-installation integration tests under `tests/`.

**Acceptance criteria:**

- Updater accepts only PID, installed path, staged asset path, and expected version; it never opens or mutates the user-data directory.
- It waits for shutdown, takes a rollback backup, atomically replaces the application, launches the new version, waits for a version-specific health marker after data initialization/local-server startup, removes backup only after health success, and performs one rollback/relaunch on timeout or failure.
- A lock prevents concurrent updates; unsafe archive paths, truncated assets, insufficient space, file locks, interrupted replacement, and missing markers leave the prior installation usable.
- macOS app-bundle and Windows file-lock behavior are handled with small platform branches and no admin requirement.
- Installers target `~/Applications/Chart Cleaner.app` and `%LOCALAPPDATA%\\Programs\\Chart Cleaner`, add the required Windows Start-menu integration, launch the installed app, and leave user data on uninstall unless explicitly removed.

**Verification rubric:**

| Criterion | Weight | Description |
|---|---:|---|
| Correctness | 0.35 | Replacement, launch/health handshake, rollback, locking, and per-user install behavior match the design. |
| Safety/data preservation | 0.30 | Backup and rollback protect the prior app; updater cannot access or overwrite mutable user data. |
| Tests | 0.25 | Disposable integration tests cover success, checksum/signature/path failures, space, locks, interruption, marker timeout, and rollback. |
| Simplicity/integration | 0.10 | One updater core serves update and installer flows with only necessary platform branches. |

### Step 4: UI update settings and health lifecycle [DONE]

**Type:** Critical — panel of 2 judges, target 4.5/5.0

**Dependencies:** Steps 1–3 APIs.

**Expected artifacts:**

- `app.py` changes for Settings/About/update handoff and asynchronous lifecycle.
- Health-marker integration at the point data initialization and local-server startup are complete.
- Focused UI/lifecycle tests using existing page/build testing patterns.

**Acceptance criteria:**

- Settings shows current version, last-check status, automatic-check preference, and a manual **Check for updates** action.
- Automatic checks begin only after UI readiness, never block startup or cleaning, and fail quietly to local status; manual checks show specific retryable errors.
- Update confirmation starts the companion updater and exits the main app only after staged verification succeeds.
- New versions write a version-specific health marker only after data initialization and local-server readiness.
- About text states that clinical data stays local and update checks are the only built-in outbound request; it does not claim that the app makes no network calls.

**Verification rubric:**

| Criterion | Weight | Description |
|---|---:|---|
| Correctness | 0.35 | UI state, async timing, handoff, health marker, and About wording match the design. |
| Safety/data preservation | 0.30 | Cleaning remains available during checks; no clinical or configuration content leaves the app. |
| Tests | 0.25 | Focused tests cover ready/disabled/manual/error/update/health states without network access. |
| Simplicity/integration | 0.10 | Existing NiceGUI state and page patterns are reused with no second UI framework. |

### Step 5: Packaging, signing, and release CI [DONE]

**Type:** Critical security/release step — panel of 2 judges, target 4.5/5.0

**Dependencies:** Steps 1–4.

**Expected artifacts:**

- Updated `Chart Cleaner.spec`, `build_app.sh`, and `build_app.bat` or equivalent minimal packaging files for main/updater artifacts.
- GitHub Actions workflow(s) for Apple Silicon macOS and Windows x64 tag builds.
- Installer/signing/notarization configuration and manifest-generation script/job.
- Release documentation describing required secrets and draft-only publication.

**Acceptance criteria:**

- Tag builds run the complete pytest suite, build main app/updater, install in temporary per-user locations without elevation, launch, and verify the expected version through the local UI.
- macOS signs nested executables/app, submits/notarizes/staples, and verifies the final artifact; Windows signs and verifies Authenticode publisher.
- Final job computes SHA-256 values and writes the manifest from already signed artifacts; publication occurs only when both platform jobs succeed, otherwise release remains a draft.
- Credentials stay in GitHub Actions secrets. Missing signing credentials fail the signing gate and do not silently produce a publishable unsigned release.

**Verification rubric:**

| Criterion | Weight | Description |
|---|---:|---|
| Correctness | 0.35 | Builds, installers, signing order, manifest generation, and publication gating are coherent and executable. |
| Safety/data preservation | 0.30 | CI uses temporary per-user locations, preserves sentinel data, and prevents unsigned publication. |
| Tests | 0.25 | Workflow includes complete tests, install/launch checks, and signed-artifact verification where credentials/platform exist. |
| Simplicity/integration | 0.10 | Extends existing PyInstaller scripts and avoids a second framework or runtime dependency. |

### Step 6: Final verification and release-readiness evidence [DONE]

**Type:** Standard integration/release gate — single judge unless security/release review requires the panel; target 4.0/5.0 (use 4.5/5.0 for security-sensitive findings).

**Dependencies:** Steps 1–5.

**Expected artifacts:**

- End-to-end tests/fixtures for a previous-version upgrade containing sentinel settings, history, token maps, and custom rules.
- Verification report covering all local tests, privacy diagnostics, update failure behavior, and rollback.
- Explicit gate record for unavailable credentials or clean target operating systems.

**Acceptance criteria:**

- Complete pytest suite passes, focused updater/storage/UI tests pass, and no test requires GitHub or clinical content.
- Upgrade fixture confirms every sentinel survives successful update, failed verification, and rollback.
- Broken-startup fixture proves rollback before release publication.
- Logs contain only approved generic metadata and no chart-derived content, config values, usernames, or document paths.
- Unavailable Apple signing/notarization, Windows signing, clean-platform, or GitHub publication gates are marked BLOCKED with evidence; they are never marked PASS.

**Verification rubric:**

| Criterion | Weight | Description |
|---|---:|---|
| Correctness | 0.35 | End-to-end behavior and all design-level acceptance criteria are demonstrated. |
| Safety/data preservation | 0.30 | Sentinel data, rollback, privacy, and failure isolation are evidenced. |
| Tests | 0.25 | Local suite and integration fixtures produce fresh, reproducible evidence. |
| Simplicity/integration | 0.10 | Final implementation remains within the existing stack and documented boundaries. |

## Definition of Done (Task Level)

- [X] Steps 1–6 are implemented and each step's verification threshold is met.
- [X] `chartcleaner/update.py`, storage/migration, updater, UI, packaging, and test artifacts are present at the paths recorded by implementation agents.
- [X] `rtk pytest -q` passes in the implementation environment, or any pre-existing unrelated failure is recorded with exact evidence.
- [X] New modules compile/import cleanly with `rtk python -m compileall chartcleaner tests`.
- [X] Existing source-checkout behavior is preserved and the pre-existing `config.json` modification remains untouched.
- [X] User data remains outside the executable and sentinel data survives upgrade, failed verification, and rollback fixtures.
- [X] Update traffic and diagnostics are verified to exclude chart text, configuration contents, statistics, paths, token maps, and custom-rule contents.
- [X] Unsigned or unverified assets cannot become installable, and rollback is bounded to one attempt.
- [X] CI builds both target platforms and publishes only from successfully signed artifacts when credentials and runners are available.
- [X] Apple Developer ID/notarization and clean Apple Silicon Gatekeeper validation are separately marked PASS only with fresh target evidence; otherwise BLOCKED.
- [X] Windows Authenticode and clean Windows x64 signature/reputation validation are separately marked PASS only with fresh target evidence; otherwise BLOCKED.
- [X] GitHub Release publication is separately marked PASS only with fresh repository evidence; otherwise BLOCKED.
- [X] No application code, commit, push, or release publication is performed as part of task preparation.
