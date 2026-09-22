# Step 2 Judge B — Release manifest, staging, and signature verification

## Verdict

**FAIL** — weighted score **3.0/5.0**, below the critical threshold of 4.5.

The manifest and byte-staging core is compact and the focused tests pass, but the release path is not yet viable for the specified signed macOS/Windows ZIP artifacts. Native verification is performed on the archive file rather than the executable/bundle inside it, and the redirect trust boundary will reject the normal GitHub release-asset delivery host.

## Evidence

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q --confcutdir=tests tests/test_update.py`: **14 passed**, with only unavailable optional pytest-config warnings.
- `.venv/bin/python -m py_compile chartcleaner/update.py tests/test_update.py`: passed.
- `git diff --check`: passed.
- A direct staging probe showed the injected verifier receives the staged ZIP bytes at `chartcleaner/update.py:294-299`; no extraction occurs before signature assessment.
- `safe_extract_archive()` is only exercised directly by tests and is not called by `UpdateClient.stage()` or any current application caller.

## Rubric scores

| Criterion | Weight | Score | Justification |
|---|---:|---:|---|
| Correctness | 0.35 | 2.8 | `ReleaseManifest.parse()` enforces an exact top-level shape, strict SemVer, positive sizes, SHA-256 format, HTTPS GitHub hosts, and exact supported platform names (`chartcleaner/update.py:27-122`). `UpdateClient.check()` handles downgrade, minimum-version, platform selection, manual checks, and 24-hour automatic throttling (`chartcleaner/update.py:236-255`). The major correctness failure is that `stage()` calls the native verifier on the downloaded ZIP path after checksum validation (`chartcleaner/update.py:281-304`); macOS Developer ID/codesign and Windows Authenticode verification need to assess the extracted app/executable or a signed installer format, not the ZIP archive. The standard GitHub release download redirect host `release-assets.githubusercontent.com` is absent from `_ALLOWED_HOSTS` (`chartcleaner/update.py:32`, `125-129`), so valid release assets can be rejected as `untrusted_redirect`. Disk requirements rely on caller-supplied `extracted_size` and `rollback_size`, both defaulting to zero (`chartcleaner/update.py:257-280`), rather than being derived or required. |
| Safety/data preservation | 0.30 | 2.7 | Temporary download files, byte count, SHA-256, generic error codes, and cleanup are implemented (`chartcleaner/update.py:267-316`). `safe_extract_archive()` rejects ordinary traversal and relative symlink escapes, and writes regular files before creating links (`chartcleaner/update.py:149-175`). It does not preserve executable mode bits from ZIP metadata, which breaks executable app bundles and can invalidate the later signature check. Its symlink validation is POSIX-only (`posixpath` plus `startswith("/")`); on Windows targets such as `C:\\outside` or `..\\outside` can pass validation and create links outside the extraction root. Unsafe extraction also leaves any earlier extracted files in the destination because there is no temporary extraction/cleanup transaction. Finally, the public `UpdateClient` accepts an arbitrary `signature_verifier` callback (`chartcleaner/update.py:223-234`), so a production caller can bypass native verification despite the identity check in `stage()`; a private test seam or production-only native verifier is needed for a no-bypass contract. |
| Tests | 0.25 | 3.2 | The 14 tests cover manifest/semver basics, downgrade/minimum/platform rejection, invalid asset fields, automatic throttling, byte count/hash failure, insufficient space, redirect rejection, generic staging errors, and basic archive traversal/symlink cases (`tests/test_update.py:46-184`). They do not test native verifier command construction on either target OS, signature rejection and cleanup, actual GitHub release redirects, extracted/rollback-space arithmetic, executable mode preservation, Windows-style symlink targets, extraction cleanup after a late unsafe entry, malformed/empty platform sets, or a local HTTP fixture as specified by the design. |
| Simplicity/integration | 0.10 | 4.0 | The implementation uses the standard library and a small client with generic status codes. The current public stage boundary is easy to call, but signature verification and archive extraction are split across unconnected helpers, leaving the central production flow incomplete. Several public methods are long one-liners and the callback bypass seam is broader than the required trust boundary. |

Weighted calculation: `(2.8 × .35) + (2.7 × .30) + (3.2 × .25) + (4.0 × .10) = 2.975`, rounded to **3.0/5.0**.

## Required fixes

1. Define the signed artifact contract and verify the native executable/app bundle after safe extraction (or use a genuinely natively signed artifact format); do not run `codesign`/Authenticode against a ZIP archive. Make the same verification happen again in the companion updater.
2. Resolve GitHub release redirects without rejecting the normal release-asset host, while keeping HTTPS and exact-host validation; add a redirect test using the production delivery host.
3. Make extraction transactional and cross-platform: reject POSIX and Windows absolute/escaping link targets, preserve executable mode bits, reject special entries, and clean the entire temporary extraction tree on any failure.
4. Remove the production bypass by keeping the native verifier fixed/private and exposing only a test seam that cannot be selected by release application code.
5. Require or derive extracted and rollback sizes before the disk-space gate, and add tests for all three space components and signature-failure cleanup.

## Round 2 — independent rejudge after fixes

### Verdict

**FAIL** — weighted score **4.1/5.0**, below the critical threshold of 4.5.

The second pass correctly moves native verification onto extracted `.app`/`.exe` targets, removes the public verifier callback, stages archive and extraction transactionally, preserves executable modes, derives ZIP expansion size, requires rollback context, and allows the release CDN for asset downloads. The stable GitHub Releases manifest path is still broken when it follows the same CDN redirect, and the Windows UNC-link case is not actually rejected by the implementation or isolated by the regression test.

### Fresh evidence

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q --confcutdir=tests tests/test_update.py`: **18 passed**, two unavailable optional pytest-config warnings.
- `.venv/bin/python -m py_compile chartcleaner/update.py tests/test_update.py`: passed.
- `git diff --check`: passed.
- A direct probe using a manifest response whose final URL is `https://release-assets.githubusercontent.com/...` returns `UpdateStatus(state="error", code="untrusted_redirect")`: `check()` calls `_validate_response_url(..., asset=False)` (`chartcleaner/update.py:287-306`) even though the release manifest is fetched from a GitHub Releases download URL.
- `Version.parse()` currently accepts `1.0.0-alpha.`, `1.0.0-alpha..1`, and `1.0.0+build..1`, which are not strict SemVer forms (`chartcleaner/update.py:31`, `55-65`).

### Round 2 rubric scores

| Criterion | Weight | Score | Justification |
|---|---:|---:|---|
| Correctness | 0.35 | 4.0 | Manifest schema, supported-platform selection, compatibility checks, throttling, byte/hash checks, ZIP expansion accounting, rollback context, transactional extraction, and extracted-target signature verification are now connected (`chartcleaner/update.py:101-123`, `287-361`). The manifest fetch still rejects a normal release-asset CDN final URL because metadata validation uses `_METADATA_HOSTS` only (`chartcleaner/update.py:32-33`, `126-142`, `292-294`); that blocks the documented GitHub Releases manifest flow. The SemVer parser is also not strict for empty prerelease/build identifiers. The second disk-space check reserves `asset.size` again after the archive is already present (`chartcleaner/update.py:349-354`), which is conservative but can reject otherwise sufficient installs. |
| Safety/data preservation | 0.30 | 4.2 | The fixed verifier is invoked on extracted signed targets, staging is cleaned as one transaction, special archive entries and executable modes are handled, and signature failure leaves no staging tree (`chartcleaner/update.py:154-194`, `308-373`). The symlink guard normalizes backslashes into `win_target` but checks absolute-root status on the original `link_text` (`chartcleaner/update.py:169-175`). A UNC target such as `\\\\server\\share` therefore avoids `startswith(("/", "//"))` and can be created outside the extraction root on Windows. The regression combines that case with an earlier `..\\outside` case, so it passes without proving the UNC branch. |
| Tests | 0.25 | 4.0 | The 18 tests now cover release-CDN asset redirects, archive/extraction handoff, mode preservation, signature-failure cleanup, rollback-context enforcement, malformed URLs, and Windows-style link inputs (`tests/test_update.py:109-243`). They do not isolate the UNC case, test a release-CDN redirect for the manifest itself, exercise native `codesign`/`spctl` or PowerShell command behavior on target OSes, test strict SemVer empty identifiers, or verify the conservative disk-space arithmetic across both gates. |
| Simplicity/integration | 0.10 | 4.2 | The standard-library-only client has a narrow `(archive_path, extracted_root)` handoff and no public verifier callback. The extraction/signature flow is now integrated cleanly. The remaining complexity is justified by transactional safety; minor cleanup remains around the duplicate archive reservation and long single-line APIs. |

Weighted calculation: `(4.0 × .35) + (4.2 × .30) + (4.0 × .25) + (4.2 × .10) = 4.08`, rounded to **4.1/5.0**.

### Remaining blockers

1. Validate the manifest response with the release-asset host policy (while keeping notes URLs metadata-only), and add a manifest CDN redirect regression test.
2. Check `win_target.startswith("/")` after backslash normalization and isolate the UNC test; add a target-platform regression test.
3. Tighten the SemVer regex to reject empty dot-separated identifiers and confirm the intended post-download disk-space formula.

## Round 3 — final independent rejudge

### Verdict

**PASS** — weighted score **4.5/5.0**, meeting the critical threshold.

The final pass resolves the prior correctness and safety findings. The manifest response accepts the GitHub release CDN, SemVer rejects empty identifiers, and each normalized POSIX/backslash/drive/UNC symlink case is tested independently. The remaining minor concern is conservative post-download disk accounting that reserves the archive size a second time; it can reject some updates despite sufficient space, but it does not allow an unsafe or under-provisioned install. Startup-ready asynchronous scheduling remains an application/UI responsibility for Step 4 and is not present in this client boundary.

### Fresh evidence

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q --confcutdir=tests tests/test_update.py`: **20 passed**, two unavailable optional pytest-config warnings.
- `.venv/bin/python -m compileall -q chartcleaner tests`: passed.
- `git diff --check`: passed.
- `UpdateClient.check()` now validates resolved manifest responses with the asset host policy (`chartcleaner/update.py:287-306`), while the initial manifest URL remains restricted to `github.com` (`chartcleaner/update.py:274-284`).
- SemVer identifiers require nonempty dot-separated components (`chartcleaner/update.py:31-65`).
- Symlink validation checks the normalized Windows separator form and rejects POSIX roots, UNC roots, drive roots, and escaping `..` paths (`chartcleaner/update.py:162-175`); tests isolate each case (`tests/test_update.py:286-293`).
- Staging retains archive and extracted roots transactionally, derives ZIP expansion size, checks rollback context, preserves modes, and verifies every extracted `.app`/`.exe` with the fixed native verifier (`chartcleaner/update.py:197-239`, `308-373`).

### Round 3 rubric scores

| Criterion | Weight | Score | Justification |
|---|---:|---:|---|
| Correctness | 0.35 | 4.5 | Exact manifest shape, strict SemVer, supported platform selection, downgrade/minimum checks, CDN redirect handling, 24-hour automatic throttling, byte/hash validation, ZIP expansion accounting, rollback-space requirement, extraction, and native target verification are connected. The post-download space check counts `asset.size` again after the archive has already consumed that space (`chartcleaner/update.py:349-354`), a conservative false-negative risk. Async startup scheduling is intentionally deferred to the Step 4 UI/lifecycle boundary. |
| Safety/data preservation | 0.30 | 4.6 | Failed staging removes the transaction, archive traversal/special entries are rejected, normalized Windows and POSIX symlink roots are constrained, executable modes are retained, and native verification has no public callback bypass (`chartcleaner/update.py:154-194`, `308-373`). Diagnostics remain generic and the client receives no chart or user-data paths. Native Developer ID/notarization and Authenticode behavior still require target-platform release evidence. |
| Tests | 0.25 | 4.5 | The 20 focused tests cover manifest/CDN redirects, malformed URLs, strict SemVer, compatibility failures, throttling/privacy, byte/hash/space failures, transaction cleanup, rollback-context enforcement, signature failure, mode preservation, local HTTP redirect rejection, traversal, special/safe links, and isolated Windows link roots (`tests/test_update.py:63-293`). Native commands remain seam-tested rather than executed on macOS/Windows, which is appropriate for this environment and remains a release gate. |
| Simplicity/integration | 0.10 | 4.5 | The implementation remains standard-library-only with a narrow `(archive_path, extracted_root)` handoff and fixed native verification. The extra extraction, redirect, and archive-size helpers directly support the stated trust and rollback contracts. |

Weighted calculation: `(4.5 × .35) + (4.6 × .30) + (4.5 × .25) + (4.5 × .10) = 4.545`, rounded to **4.5/5.0**.

### Residual note

Consider changing the second disk check to `expanded_size + rollback_size` after download, or document the deliberate double reservation. Verify the actual native commands later on clean Apple Silicon and Windows x64 targets; those gates are not proven by this local run.
