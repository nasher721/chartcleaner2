# Step 2 implementation report

Implemented the standard-library updater client in `chartcleaner/update.py`.

## Contract

- `ReleaseManifest.parse()` accepts only the documented release metadata shape,
  strict SemVer, exact supported platform names, HTTPS GitHub release hosts,
  positive sizes, and 64-character SHA-256 values.
- `UpdateClient.check()` returns a privacy-safe `UpdateStatus`, supports manual
  checks, throttles automatic checks to 24 hours, rejects downgrades and
  unsupported minimum versions, and selects only the exact current platform.
- `UpdateClient.stage()` requires caller-supplied rollback-space context,
  derives archive expansion size from ZIP metadata, writes a transactional
  archive/extraction tree under a caller-owned staging directory, streams and
  hashes bytes, preserves executable modes, removes failed transactions, and
  invokes mandatory native signature verification on extracted signed targets.
- `verify_native_signature()` requires a pinned publisher identity. macOS uses
  `codesign --verify --deep --strict` plus `spctl` Developer ID assessment;
  Windows uses a valid Authenticode signature and subject identity match.
- `safe_extract_archive()` rejects traversal, special files, and unsafe
  POSIX/Windows absolute or escaping symlink entries while preserving relative
  in-bundle symlinks required by macOS PyInstaller framework layouts. Regular
  files are written before links are created, and failed extraction removes the
  newly-created destination.
- Redirects are revalidated after transport resolution, so an HTTPS request
  cannot silently follow to a host outside the pinned GitHub allowlist.
- Staging validates asset metadata before writing, requires a configured
  publisher identity even with an injected test seam, removes all partial
  files on failure, and converts unexpected transport/filesystem failures to
  generic `staging_failed` diagnostics.

The public staging handoff is `(archive_path, extracted_root)`. The companion
updater may consume only those paths plus expected version, installed
application path, process ID, and the actual rollback-size context. It must
independently recheck archive bytes, expansion/path safety, and native
signature immediately before replacement; these paths and adjacent metadata
are not trust tokens. Publisher identity is supplied by the frozen updater's
release-specific build configuration, while release hashes come from the
pinned HTTPS GitHub manifest. The updater never opens the user-data boundary.

## Judge-cycle-2 resolution

| Panel criterion | Cycle 1 finding | Resolution |
|---|---|---|
| Correctness | 3.0 / 2.8: release-assets redirect host omitted; ZIP itself was signed; disk estimates defaulted to zero. | Added pinned redirect handler with `release-assets.githubusercontent.com`, verifies extracted `.app`/`.exe` targets, derives ZIP expansion size, and requires positive rollback context. |
| Safety/data preservation | 2.75 / 2.7: public verifier bypass, Windows link escapes, modes and extraction cleanup missing. | Removed public verifier injection, preserved executable modes, rejects POSIX/Windows/UNC link escapes and special entries, and cleans transactional extraction on failure. |
| Tests | 3.25 / 3.2: missing redirect, signature, disk, mode, Windows-link, malformed-URL cases. | Focused suite now covers these cases plus signature-failure cleanup and release-assets redirect behavior. |
| Simplicity/integration | 4.5 / 4.0. | Retained standard-library-only implementation and narrow `(archive, extracted_root)` handoff. |

## Verification

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q --confcutdir=tests tests/test_update.py` — 20 passed. The repository root `conftest.py` imports the optional NiceGUI testing plugin, so `--confcutdir=tests` was required in this environment; warnings were limited to unavailable optional pytest config plugins.

`python3 -m py_compile chartcleaner/update.py tests/test_update.py` — passed.

`git diff --check` — passed.

Native Developer ID/notarization and Windows Authenticode checks remain target-OS release gates; unit tests inject only transport and verifier seams and never weaken the production default.

## Judge-cycle-3 resolution

| Panel finding | Resolution |
|---|---|
| SemVer accepted empty prerelease/build identifiers such as `alpha..1`. | SemVer grammar now requires nonempty dot-separated identifiers and retains numeric leading-zero rejection. |
| Manifest response validation rejected a normal GitHub CDN redirect. | Direct manifest URLs remain restricted to `github.com`; resolved responses may use the exact GitHub release asset host allowlist. |
| UNC symlink cases were not isolated by the regression test. | Validation checks normalized Windows separators and drive/UNC roots; POSIX, backslash, drive, and UNC fixtures are tested independently. |
| Redirect tests used only in-memory response seams. | Added a real local `http.server` redirect fixture proving the pinned handler rejects the untrusted `Location` before `/evil` is contacted; production transport remains HTTPS/GitHub-only. |

Cycle-3 focused verification: `tests/test_update.py` — **20 passed**; compileall and `git diff --check` passed. Native Developer ID/notarization and Authenticode remain target-platform release gates.
