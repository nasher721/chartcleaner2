# Design: signed per-user deployment and one-click updates

Decided 2026-09-22: distribute Chart Cleaner as a normal installed app for
Apple Silicon macOS and 64-bit Windows. Installation and updates require no
administrator rights. Releases come from public GitHub Releases, user data
lives outside the executable, and a small companion updater performs verified,
rollback-safe replacement.

This keeps the existing Python, NiceGUI, and PyInstaller stack. It does not add
a second application framework or a runtime dependency.

## Goals

- Signed, normal app installation in `~/Applications/Chart Cleaner.app` on
  macOS and `%LOCALAPPDATA%\Programs\Chart Cleaner` on Windows.
- Per-user installation without elevation; internet is needed only for initial
  download and later update checks.
- One-click update from the app, sourced from GitHub Releases.
- Preserve rules, presets, history, token maps, and custom scripts across every
  install, update, failed update, and rollback.
- Keep chart content and configuration out of update traffic and diagnostics.

Not in the first release: Intel Macs, system-wide installation, silent
background replacement, private release authentication, or a portable edition.

## Installed layout and data ownership

Frozen builds store mutable state in the platform user-data directory:

```text
macOS
~/Applications/Chart Cleaner.app
~/Library/Application Support/Chart Cleaner/
  config.json, data/, presets/, custom_rules/

Windows
%LOCALAPPDATA%\Programs\Chart Cleaner\
%LOCALAPPDATA%\Chart Cleaner\
  config.json, data\, presets\, custom_rules\
```

Source checkouts retain repository-relative paths so development and existing
tests remain predictable. Updates replace application code only. Uninstalling
also leaves user data in place unless the user explicitly chooses to remove it.

On first frozen launch, the app detects a portable installation beside the
executable. If the destination is empty, it copies `config.json`, `data/`,
`presets/`, and `custom_rules/` into a temporary directory, validates the
copied configuration and filenames, then atomically installs the new data
directory. The source files are retained as a rollback copy. An existing
destination always wins; the app never merges or overwrites two data sets
automatically.

Future configuration schema changes use the existing backup mechanism. A
migration writes a backup, applies only known changes, validates the result,
and retains the old configuration on failure. Bundled factory defaults may
change without resetting user configuration.

## Components

`chartcleaner/update.py` owns update checks, version comparison, platform asset
selection, download staging, byte-count validation, and SHA-256 verification.
It uses the Python standard library.

A separately frozen `chart-cleaner-updater` executable owns installation and
replacement after the main app exits. Common logic waits for shutdown, creates
a backup, replaces the installed application, waits for a successful startup
marker, rolls back on failure, and relaunches. Small platform branches handle
macOS app bundles and Windows file-lock behavior. The updater receives only the
process ID, installed path, staged asset path, and expected version; it never
opens the Chart Cleaner data directory.

The same updater core powers the initial per-user installers:

- macOS: signed and notarized `Install Chart Cleaner.app` installs into
  `~/Applications` and launches the installed app.
- Windows: signed `ChartCleanerSetup.exe` installs into
  `%LOCALAPPDATA%\Programs\Chart Cleaner`, adds Start-menu integration, and
  launches the installed app.

The Settings page shows the current version, last-check status, automatic-check
preference, and a manual **Check for updates** action. Checks run asynchronously
after the UI is ready and at most once per 24 hours. Disabling automatic checks
does not remove the manual action.

## Release manifest and update flow

The app reads a stable public GitHub Releases URL for
`update-manifest.json`. The manifest contains only release metadata:

```json
{
  "version": "2.4.0",
  "minimum_supported_version": "2.3.0",
  "notes_url": "https://github.com/nasher721/chart-cleaner/releases/tag/v2.4.0",
  "platforms": {
    "macos-arm64": {"url": "...", "sha256": "...", "size": 123},
    "windows-x64": {"url": "...", "sha256": "...", "size": 456}
  }
}
```

The app rejects malformed manifests, downgrades, incompatible minimum versions,
and platforms without an exact asset match. After user confirmation it checks
available disk space, downloads to a temporary filename, verifies size and
SHA-256, then performs native signature verification. macOS uses Developer ID
and notarization assessment; Windows requires the expected Authenticode
publisher. Verification has no bypass.

Once verified, the app starts the companion updater and exits. The updater
renames the current application to a backup, installs the staged release, and
launches it. The new version writes a version-specific health marker only after
data initialization and local-server startup. The updater removes its backup
after seeing that marker. A timeout restores and relaunches the prior version.
Only one rollback attempt is made, preventing retry loops.

Update requests contain the normal HTTPS/GitHub metadata and current app
version only. They never contain chart text, user configuration, statistics,
paths, token maps, or custom-rule contents. The About text must replace the
current absolute “no network calls” statement with the narrower fact that
clinical data stays local and update checks are the only built-in outbound
request.

## Release automation

A version tag starts GitHub Actions jobs on Apple Silicon macOS and Windows
x64. Each job runs the complete test suite, builds the application and updater,
installs into a temporary per-user location without elevation, launches the
app, and confirms the expected version through the local UI.

The macOS job signs nested executables and the app, submits it for notarization,
staples the result, and verifies the final artifact. The Windows job applies
and verifies Authenticode signatures. Release credentials remain GitHub Actions
secrets. A final job creates SHA-256 values and the manifest from the already
signed artifacts. It publishes the release only when both platforms succeed;
otherwise the release stays a draft.

Apple Developer ID/notarization credentials and a Windows code-signing
certificate are release prerequisites, not optional fallback paths.

## Failure handling

Update checks never block startup or cleaning. Offline access, rate limits,
timeouts, or malformed responses leave the current app untouched. Automatic
checks fail quietly with a local status; manual checks show a specific error and
retry action.

The installer checks for sufficient space for the archive, extracted app, and
rollback copy. Downloads become installable only after all verification passes.
A lock prevents concurrent updates. Interrupted downloads and failed
verification delete staging files. Failed replacement or a missing startup
marker restores the previous app.

Update logs contain versions, timestamps, platform, verification stages, and
generic error codes. They exclude chart-derived content, configuration values,
usernames, and document paths.

## Verification

Unit tests cover manifest validation, semantic-version comparison, platform
selection, check throttling, downgrade rejection, checksum failure, and safe
diagnostics. They use a local fixture HTTP server and never require GitHub.

Storage tests cover a fresh install, portable-data migration, an existing
destination, malformed configuration, interrupted copying, and permissions.
They assert that original user data remains byte-for-byte intact.

Updater integration tests use disposable fake installations and cover:

- successful replacement and relaunch;
- truncated or wrong-checksum assets;
- rejected signatures and unsafe archive paths;
- insufficient space and concurrent attempts;
- Windows file locks;
- missing health markers and verified rollback.

Each release job also upgrades a previous-version fixture containing sentinel
settings, history, token maps, and a custom rule, then confirms every sentinel
survives. A deliberately broken startup proves rollback before publication.
Final manual gates cover Gatekeeper on a clean Apple Silicon Mac and Windows
signature/reputation presentation on a clean Windows x64 user account.

## Alternatives rejected

- A permanent launcher with side-by-side version payloads improves rollback but
  adds another long-lived application layer and signing surface.
- Separate native updater frameworks create two implementations and new runtime
  dependencies.
- Downloading and opening a conventional installer is simpler but does not meet
  the one-click replacement goal.
- A local web service, portable-only build, or application-shell rewrite adds
  installation burden or duplicates the existing working app.
