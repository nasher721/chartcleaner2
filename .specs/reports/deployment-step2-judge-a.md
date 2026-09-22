# Step 2 Judge A — Release manifest, staging, and signature verification

## Result

**FAIL** — weighted score **3.35/5.0**, below the critical threshold of 4.5.

Focused execution: `./.venv/bin/pytest -q tests/test_update.py` → **14 passed**.
Compilation: `./.venv/bin/python -m compileall -q chartcleaner/update.py tests/test_update.py` → **pass**.

The tests exercise the intended happy paths and several failures, but the current
implementation is not release-ready because production downloads can fail on the
normal GitHub asset redirect path and the public API permits signature bypass.

## Criterion scores

| Criterion | Weight | Score | Evidence |
|---|---:|---:|---|
| Correctness | 0.35 | **3.0** | Manifest field/hash/size validation, SemVer ordering, exact platform selection, throttling, byte counts, and SHA-256 are implemented in `chartcleaner/update.py:43-122`, `140-146`, and `222-304`. However, `_ALLOWED_HOSTS` at `:32` omits the normal `release-assets.githubusercontent.com` GitHub release-asset host, so valid release downloads are rejected by `:132-137`/`:284-286`. Disk reservation is caller-supplied with defaults of zero (`:257-279`), so extracted and rollback needs are not enforced unless every caller supplies estimates. The updater module also exposes only synchronous checks (`:236-255`); readiness/asynchronous integration is not present in this artifact. |
| Safety/data preservation | 0.30 | **2.75** | Staging is temporary, hashes/byte counts are checked before return, and failures clean `.part`/verified paths (`:281-315`). Native verification is required only by convention: callers can pass `signature_verifier=lambda *_: None` and any nonempty identity, and `stage()` accepts the asset (`:223-233`, `:297-303`). That is an explicit production bypass despite the no-bypass requirement. `safe_extract_archive()` rejects POSIX traversal but accepts Windows backslash traversal in symlink targets (`:160-175`); a fixture with `..\\outside` was accepted. Also, `_default_transport()` uses `urllib.request.urlopen()` before validating the final URL (`:198-207`), so an untrusted redirect is followed before it is rejected. |
| Tests | 0.25 | **3.25** | The local suite covers manifest basics, downgrade/minimum/platform failures, invalid asset fields, platform selection, throttling/privacy, checksum/space cleanup, one external redirect fixture, generic error text, and POSIX symlink traversal (`tests/test_update.py:46-184`). It does not use a local HTTP server as specified by the design (`design.md:160-162`), does not test manifest redirect handling, signature rejection/mandatory native verification, real GitHub release-asset redirects, Windows path separators/UNC symlinks, archive/extracted/rollback disk accounting, or asynchronous readiness/manual-disabled behavior. |
| Simplicity/integration | 0.10 | **4.5** | The module is standard-library-only and narrowly scoped (`chartcleaner/update.py:9-25`), with a compact status object and no new runtime dependency. The injected transport/verifier hooks make tests easy, but the verifier hook also weakens the required security boundary. |

Weighted calculation: `(3.0 × .35) + (2.75 × .30) + (3.25 × .25) + (4.5 × .10) = 3.35`.

## Findings

### [HIGH] Normal GitHub release asset redirects are rejected

File: `chartcleaner/update.py:32`, `chartcleaner/update.py:125-137`, `chartcleaner/update.py:198-207`

`_is_github_https()` only permits `github.com`, `objects.githubusercontent.com`,
and `githubusercontent.com`. GitHub release downloads commonly redirect to
`release-assets.githubusercontent.com`; a direct check in the implementation
returned `False` for that host. Since `urlopen()` follows the redirect and then
`:132-137` rejects the final URL, every normal release asset can fail with
`untrusted_redirect`.

Fix: pin an explicit exact allowlist that includes the actual GitHub release asset
host(s), and add an HTTP fixture that follows the same redirect shape. Keep the
redirect handler restricted to those exact HTTPS hosts and validate before any
untrusted redirect is followed.

### [HIGH] Public staging API permits signature verification bypass

File: `chartcleaner/update.py:222-233`, `chartcleaner/update.py:297-303`

`UpdateClient` accepts an arbitrary `signature_verifier` callback. A caller can
construct the client with `signature_verifier=lambda *_: None` and a nonempty
publisher identity; `stage()` then returns the asset as verified without native
signature verification. The current tests use exactly that bypass at
`tests/test_update.py:114-125`. This conflicts with the acceptance criterion that
native verification is mandatory and has no bypass.

Fix: make production staging call `verify_native_signature` unconditionally and
move test substitution behind an explicitly test-only seam that production code
cannot select, or test by monkeypatching the native verifier without exposing a
public constructor bypass. Add a test proving a missing/failed native verifier
always prevents a staged asset from being returned.

### [HIGH] Redirect validation happens after the request has followed the redirect

File: `chartcleaner/update.py:198-207`

`urllib.request.urlopen()` automatically follows redirects before
`_validate_response_url()` inspects `response.geturl()`. That detects an unsafe
final URL after a request has already been sent to it; it does not enforce trust
before following the redirect. This is especially relevant because the criterion
calls for transport redirect trust.

Fix: use a redirect handler/opener that rejects each `Location` unless it is an
allowed HTTPS GitHub host, then retain final-URL validation as defense in depth.

### [MEDIUM] Windows symlink traversal is not rejected

File: `chartcleaner/update.py:160-165`

Symlink target validation uses `posixpath` and only checks `/` absolute paths and
`../` traversal. A symlink target of `..\\outside` was accepted by the current
implementation. On Windows that target escapes the extraction root.

Fix: normalize and validate both POSIX and Windows separators, reject drive-rooted
and UNC targets, and resolve the candidate target with the platform path rules
before creating the link. Add Windows-style separator and UNC fixtures.

### [MEDIUM] Disk safety can be silently under-accounted

File: `chartcleaner/update.py:257-279`

`extracted_size` and `rollback_size` default to zero, and the required-space
calculation trusts caller-provided values. A caller that uses the defaults checks
only archive bytes, contrary to the requirement to reserve archive, extracted,
and rollback space.

Fix: require nonzero validated estimates from the manifest/updater boundary or
inspect the archive to calculate its expanded size before staging; reject unknown
space requirements instead of treating them as zero.

### [LOW] Malformed URL parsing can leak a raw exception

File: `chartcleaner/update.py:125-129`

Malformed URLs such as `https://[bad` cause `urllib.parse` to raise `ValueError`
from `.hostname` instead of returning `False` and producing the generic
`invalid_asset`/`invalid_manifest` code. This is a small privacy/error-contract
inconsistency.

Fix: catch URL parsing `ValueError` in `_is_github_https()` and return `False`.

## Recommendation

**REQUEST CHANGES.** Address the three HIGH findings before considering Step 2
verified. Then add the missing redirect, native-signature, Windows-symlink, and
disk-accounting fixtures and rerun the focused suite.

## Cycle 2 — independent recheck

### Evidence

- Focused run: `./.venv/bin/python -m pytest --confcutdir=tests -q tests/test_update.py` -> **18 passed**, with one pre-existing unknown `main_file` pytest option warning.
- `./.venv/bin/python -m py_compile chartcleaner/update.py tests/test_update.py` -> pass; `git diff --check` -> pass.
- The public `signature_verifier` callback is gone from `UpdateClient.__init__()` (`chartcleaner/update.py:274-285`); staging calls `verify_native_signature()` for every extracted `.app`/`.exe` target (`:212-239`, `:351-361`).
- Redirects are checked by `_PinnedRedirectHandler` before following and the release CDN is allowlisted (`:32-34`, `:242-259`). A direct handler probe rejected `https://evil.example/x` with `untrusted_redirect`.
- ZIP expansion is derived and positive rollback context is required (`:197-209`, `:308-355`); executable modes are retained (`:177-187`); Windows/drive/UNC symlink cases are covered (`tests/test_update.py:210-243`).
- Direct SemVer probes show invalid versions `1.0.0-alpha..1`, `1.0.0-alpha.`, and `1.0.0-.alpha` are still accepted by `Version.parse()` (`chartcleaner/update.py:31`, `55-65`).

### Remaining findings

#### [MEDIUM] SemVer parser is not strict SemVer 2.0

File: `chartcleaner/update.py:31`, `chartcleaner/update.py:55-65`

The prerelease/build regex uses `[0-9A-Za-z.-]+`, allowing empty dot-separated
identifiers and leading dots. The implementation accepts `1.0.0-alpha..1`,
`1.0.0-alpha.`, and `1.0.0-.alpha`, all invalid SemVer. The task explicitly
requires semantic-version comparison and the implementation report claims strict
SemVer.

Fix: match prerelease and build metadata as nonempty dot-separated identifiers
(`(?:[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)`) and retain the numeric prerelease
leading-zero check. Add rejection tests for empty/consecutive-dot identifiers.

#### [LOW] Redirect and native-signature tests remain seam-only

File: `tests/test_update.py:47-49`, `tests/test_update.py:150-176`,
`tests/test_update.py:200-207`

The focused tests monkeypatch `verify_native_signature` globally and use an
in-memory `RedirectResponse`; they do not exercise the actual `urllib` opener
against a local HTTP server or prove a real redirect is rejected before a second
request. The implementation is materially improved and the handler direct probe
works, but the design calls for local fixture HTTP coverage (`design.md:160-162`).

Fix: add a tiny `http.server` fixture that emits an allowed GitHub-shaped redirect
and an untrusted redirect, asserting the untrusted target is never contacted.
Keep native OS signing evidence as a target-platform release gate.

### Cycle 2 scores

| Criterion | Weight | Score | Weighted |
|---|---:|---:|---:|
| Correctness | 0.35 | 4.1 | 1.435 |
| Safety/data preservation | 0.30 | 4.4 | 1.320 |
| Tests | 0.25 | 4.0 | 1.000 |
| Simplicity/integration | 0.10 | 4.5 | 0.450 |
| **Overall** | **1.00** |  | **4.205 / 5.0** |

### Cycle 2 verdict

**FAIL** — the prior HIGH findings are resolved in the inspected code, but the
remaining strict-SemVer defect and missing real HTTP fixture leave the score below
the critical 4.5/5.0 threshold. Tighten the parser, add the local redirect test,
and rerun the focused suite before marking Step 2 complete. Native Developer ID /
notarization and Authenticode validation remain target-platform release gates and
are not treated as waived by this unit review.
