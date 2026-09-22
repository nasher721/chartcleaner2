# Step 3: companion updater and installer implementation

Task: `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md`, Step 3.
Source design: `.specs/plans/cross-platform-deployment-updater.design.md`.
CLAUDE_PLUGIN_ROOT: unavailable; native implementation agent used directly.

## Artifacts and interfaces

- `chartcleaner/updater.py`: standard-library-only per-user install/update transaction, OS lock, PID wait, validated replacement, fresh health nonce, bounded rollback, signed embedded installer, Start-menu shortcut.
- `chartcleaner/release_identity.py`: immutable build-time trust pins; fixed public repository manifest URL, version from existing package version, empty publisher literals fail closed until signed CI builds set them.
- `updater_entry.py`: separately frozen four-argument companion entry point.
- `installer_entry.py`: separately frozen per-user setup entry point.
- `tests/test_updater.py`: 38 focused disposable-installation checks. Native signatures are replaced by test monkeypatches only; production has no verifier switch.

Public integration APIs:

- `default_install_path()` returns `~/Applications/Chart Cleaner.app` on Apple Silicon macOS or `%LOCALAPPDATA%/Programs/Chart Cleaner` on Windows x64. No arbitrary CLI installation root.
- `staging_root()` returns the installation parent plus `.chart-cleaner-staging`. Staging/archive inputs outside that root, symlinks, and Windows junction/reparse-point ancestry are rejected.
- `installation_size(install)` counts only the canonical executable tree for rollback-space planning.
- `release_client()` uses the pinned URL and native publisher selected by the actual supported platform.
- `handoff_update(pid, install_path, staged_archive, expected_version)` verifies/copies/verifies the entire companion onedir runtime to `companion-*/updater` under staging and starts it. It returns the child `Popen`; UI must exit only after this succeeds.
- `update_install(pid, installed_path, staged_archive, expected_version)` independently fetches the pinned manifest, copies/hashes the archive, validates byte count/version/disk/extraction/native publisher, waits for the app PID, performs same-volume rename replacement, and health-checks the new child.
- `write_health_marker(version)` is called by the app only after data initialization and proven local-server readiness. No update environment means a no-op returning false.

Health environment: `CHART_CLEANER_UPDATE_HEALTH` points to `staging_root()/install-*/health.json`; `CHART_CLEANER_UPDATE_NONCE` is a new 64-character lowercase hexadecimal value. Health JSON must equal `{"version": expected_version, "nonce": fresh_nonce}`. The writer additionally requires its executable inside the canonical installed app. Missing, stale, malformed, symlinked, wrong-version, or wrong-nonce markers do not pass. A child which exits before health also fails. Startup timeout is 90 seconds, PID shutdown timeout 60 seconds. Failed new children are terminated/waited before restoring and relaunching the old installation once.

## Packaging contract

Archive top-level root is exactly `Chart Cleaner.app` for macOS or `Chart Cleaner` for Windows, with no other top-level entries.

- macOS main: `Contents/MacOS/Chart Cleaner`; updater runtime: `Contents/Helpers/updater/chart-cleaner-updater` and its complete supporting onedir files.
- Windows main: `Chart Cleaner.exe`; updater runtime: `updater/chart-cleaner-updater.exe` plus `_internal` runtime.
- macOS application version comes from sealed `Contents/Info.plist` `CFBundleShortVersionString`.
- Windows application version comes from the signed main executable's `VersionInfo.ProductVersion`.
- Set publisher constants before freezing/signing, never via runtime config/environment/CLI.
- Signed installer embeds `installer-payload.zip` and normal-schema `installer-manifest.json` at the PyInstaller resource root. Manifest version must equal the installer's own compiled package version.
- Initial installer verifies its own outer native signature before reading embedded metadata, then uses the same private transaction without a network request. The public updater always independently fetches the public manifest.
- macOS embedded resources must resolve inside the sealed installer `.app`; framework/resource symlinks are permitted only within that signature boundary.
- Windows installer must be onefile so payload/metadata are embedded in the signed executable; mutable adjacent onedir sidecars are rejected. PyInstaller extraction must be `_MEI*` under the normal temp directory.
- Initial installer refuses to overwrite an existing installation and directs updates through the app. It launches the newly installed app and creates the Windows per-user Start-menu shortcut.

## Recovery and data boundary

The updater never imports the storage layer or computes/opens the user-data directory. Tests keep config/history/token-map/custom-rule sentinel bytes outside the install and assert byte-for-byte preservation after every fixture.

OS-owned lock handles release on process death; the lock file remains intentionally to avoid inode replacement races. The backup is a fixed sibling `.Chart Cleaner.app.rollback`/`.Chart Cleaner.rollback`, outside mutable data. A prior interrupted transaction with a remaining backup triggers native verification and one recovery/relaunch instead of overwriting its recovery copy. Recoverable exceptions including `KeyboardInterrupt` during the replacement restore the prior app. If the OS prevents rollback renames, the backup is retained; cleanup never removes it. Health-success alone permits backup deletion.

Verified archive copies, extracted app, health marker, and failed replacement trees are removed after the transaction. Owned incoming `update-*` staging folders are also cleaned. The active copied companion runtime remains outside the install (Windows cannot delete its own mapped onedir runtime); later staging maintenance can prune these retired helper folders when no companion is active. This is a bounded disk-cleanup limitation, not an install/data safety bypass.

## Fresh verification

- `rtk proxy env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q --confcutdir=tests tests/test_updater.py tests/test_update.py`: **67 passed**, 4.53 seconds. Two warnings concern optional absent root pytest plugin configuration.
- `rtk proxy python3 -m py_compile chartcleaner/updater.py chartcleaner/release_identity.py updater_entry.py installer_entry.py tests/test_updater.py`: passed.
- `rtk git diff --check`: passed.

Coverage includes success; wrong/truncated hash; signature rejection; unsafe archives; pre-copy/extraction disk shortages; concurrent lock; process timeout; simulated Windows old/new file locks; replacement interruption; previous interrupted-backup recovery; malformed/stale/missing health; exact one rollback/relaunch; signed payload version mismatch; rejected user-data/external archive paths; symlink install; missing publisher; whole onedir helper copying; fresh install; existing-install refusal; no CLI publisher override; generic diagnostics; Windows payload/shortcut contract; offline signed embedded installer; rejected Windows sidecars; source installer rejection.

Actual subprocess checks prove a running child creates a correct marker, then prove a child missing a health marker is stopped before rollback/relaunch. Archive/native verification uses isolated test fixtures; no installed user application is touched.

## Explicit external gates

Native Apple Developer ID/notarization and Windows Authenticode verification, real Windows file mapping/Start-menu behavior, clean per-user installer launch on both target platforms, and signed release publication remain BLOCKED pending target-platform/signing evidence. Their command contracts and failure paths are covered by tests, not represented as signed-platform execution. No commits, release publication, or task DONE markers were made by this agent.
