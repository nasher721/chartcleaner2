# Step 6 — integration and release-readiness evidence

## Fresh local verification

- `PATH="$PWD/.venv/bin:$PATH" rtk pytest -q`: **328 passed, 0 failed, 3 skipped**, with normal pytest/NiceGUI plugins. Evidence: `/Volumes/1TB ssd/Remix/chart cleaner/release-validation/final/full-pytest.log`.
- Focused deployment suite (storage, update client, updater, UI lifecycle, release tools, combined deployment): **128 passed, 1 skipped in 5.37s**. Skip: Windows junction semantics on macOS. The other full-suite skips are two optional medspaCy tests (package not installed).
- `rtk python -m compileall -q chartcleaner tests scripts`, release script/spec AST parsing, workflow YAML parse, and `git diff --check`: **PASS**. No lint/typecheck configuration or installed ruff/mypy/flake8/pyright tool exists; no new static-analysis dependency was introduced.
- User's pre-existing `config.json` SHA-256 remains **ce470295e099ae780baf753f78d7a72ca056c76ef6043fe31fb0b3ec4262dca9** after the full suite.

## Combined sentinel and failure proof

`tests/test_deployment.py::test_full_transaction_preserves_nested_user_data` adds four coherent source transactions: successful upgrade, bad archive checksum, failed native verification, and deliberately broken startup. Each uses the production updater transaction and nested settings/history/token-map/custom-rule/preset files outside the application directory. Every sentinel is compared byte-for-byte. Successful startup uses a real subprocess and the production health writer with version/nonce; broken startup proves the failed child has stopped before the single rollback relaunch. Verification failures preserve the old installed version without launching anything. Native publisher validation is a mocked boundary in these local fixtures, not a runtime bypass.

Existing focused tests add portable migration preservation, immutable-source copies, corruption/permissions/interruption cases, exact checksum/size/redirect checks, generic diagnostics, concurrent locks, low disk, Windows file-lock simulations, actual child-exit waiting, stale/missing health markers, companion DLL hash integrity, and offline pending-backup recovery. Privacy evidence includes `tests/test_update.py::test_check_throttles_automatic_and_keeps_diagnostics_private`, `test_unexpected_staging_errors_are_generic`, and `tests/test_updater.py::test_generic_failure_output_omits_paths`; no clinical source was used.

## First-release gate correction

Review identified that requiring an already-published signed predecessor made the first signed release impossible. `scripts/prepare_previous_fixture.py` now supports an explicit signed synthetic predecessor from an isolated `git archive HEAD` copy with only the canonical version lowered. The workflow builds/signs that fixture using the same native publisher and runs the same upgrade/rollback checks. Later releases can use `PREVIOUS_RELEASE_TAG` instead.

The default target `2.3.0` produces a `2.2.0` fixture and minimum-supported version `2.2.0`. Optional variables can change the fixture/minimum, but `minimum <= previous < target` is enforced before building and again in the smoke harness. Embedded installer manifests and the final published manifest share that minimum. `tests/test_deployment.py::test_first_signed_release_fixture_has_a_compatible_default_interval` checks the working bootstrap default, real predecessor mode, and rejection of impossible/incompatible intervals. Reports identify `synthetic_same_source_previous_version`; this proves signed transaction behavior, not historical schema compatibility. No minimum-version check is waived.

Changed for this correction: `scripts/prepare_previous_fixture.py`, `.github/workflows/release.yml`, `scripts/release_smoke.py`, `docs/releases.md`, and `tests/test_deployment.py`.

## Native build freshness

A fresh unsigned macOS arm64 app/companion rebuild started **2026-09-22T22:07:09.692740+00:00** using current core/UI sources. Complete input SHA-256 values are recorded in `/Volumes/1TB ssd/Remix/chart cleaner/release-validation/final/build-inputs.json`; build output/log remains on the external drive. The completed input hash comparison shows no source changes during the build. Fresh frozen startup evidence follows below.

## Release gates still blocked

| Gate | Status | Evidence boundary |
| --- | --- | --- |
| Source implementation/tests and unsigned Apple Silicon build | PASS | Local executable/resource proof only. |
| Apple Developer ID signing, notarization, stapling | BLOCKED | No release credentials used in this session. |
| Native Windows x64 build/Authenticode | BLOCKED | Current execution host is macOS arm64. |
| Signed frozen installer/update/rollback on both CI runners | BLOCKED | Requires target runners and signing secrets. |
| Clean-machine Gatekeeper and Windows signature/SmartScreen presentation | BLOCKED | No clean target-machine evidence. |
| Published release and production frozen companion/GitHub-network update | BLOCKED | No commit, push, workflow dispatch, or publication performed. |

The CI smoke harness injects draft discovery only in source code; native signatures stay active in signed runner tests. Windows onedir support files rely on the exact archive SHA-256 from the pinned HTTPS manifest, with EXE publisher checks as an additional gate. No local source test or unsigned build is counted as signed distribution evidence.

## Completed latest-source frozen smoke

Finished **2026-09-22T22:10:32.139916+00:00**. The rebuilt app was copied into an isolated canonical `HOME/Applications/Chart Cleaner.app` on the external drive and launched on an ephemeral loopback port. `/settings` showed version `2.3.0`; the actual production startup path wrote a health marker containing that version and the supplied 64-character nonce. The isolated external user-data configuration was created. The process terminated cleanly. No source hash changed between recorded build inputs and smoke.

- Evidence: `/Volumes/1TB ssd/Remix/chart cleaner/release-validation/final/frozen-smoke.json`, `build-inputs.json`, `build.log`, and `frozen-smoke.log`.
- Main executable SHA-256: `d06906605f83d5ecb4d57a3a9687f9b2365045c6f46ffc38a9c46bcfed08c0b1`.
- Companion executable SHA-256: `ac2e0b58842d91e9a942fc0d17481f3c7084ed310b69c4efd994c10d166c669e`.
- Build artifact: `/Volumes/1TB ssd/Remix/chart cleaner/release-validation/final/build/Chart Cleaner.app`; marked unsigned and NOT-DISTRIBUTABLE.
- Free space after validation: external drive **869 GiB**, internal data volume **8.8 GiB**. Outputs/cache remain on external storage.

The full-suite and deployment-suite results above are local source evidence; signed Windows/macOS release and clean-machine checks remain BLOCKED exactly as recorded.

Native fail-closed evidence: at **2026-09-22T22:11:45.462247+00:00**, production `verify_native_signature` rejected the actual unsigned rebuilt bundle with `signature_invalid` against a synthetic Developer ID publisher. Evidence: `release-validation/final/negative-signature.json`. This is negative trust proof, not successful signing evidence.

Cleanup verified `pytest-of-Nash/` contained only task-created test fixtures: a 125-byte traversal-test ZIP with `../escape.txt`, a three-byte `abc` staging fixture, and pytest's internal directory links. Removed that directory only. Final `git status --short` contains intentional deployment source/tests/docs/spec/workflow artifacts plus the pre-existing modified `config.json`; its SHA-256 remains unchanged. Native build/evidence outputs remain on the external drive. No commit was created.
