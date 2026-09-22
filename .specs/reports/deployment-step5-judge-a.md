# Step 5 independent judge A

Task: `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md`, Step 5. Design: `.specs/plans/cross-platform-deployment-updater.design.md`. `CLAUDE_PLUGIN_ROOT` unavailable; used task rubric. Reviewer did not author packaging. Only this report written.

## Verdict: PASS for implementation, 4.555/5

| Criterion | Evidence | Score | Weight |
|---|---|---:|---:|
| Correctness | Traced spec, wrappers, identity generation, version resources, native three-product builds, nested signing order, notarization/stapling, final package and embedded installer payload, previous-version smoke, and both-platform publish dependency. Native runtime claims remain external gates. | 4.6 | .35 |
| Safety/data preservation | Local unsigned output is marked and rejected by signing package path; exact publisher pins compiled before freezing; setup resources sealed in Mac bundle/Windows onefile signature; missing secrets fail before publication; synthetic per-user sentinel data and rollback checked; draft exists before native gates and publish requires both proofs. | 4.7 | .30 |
| Tests | Five release-specific tests pass, scripts compile, spec parses, workflow structure validated. Tests cover archive modes/symlinks, hash exactness, identity literal encoding, missing credentials, unsigned rejection. Full real signing/installer execution and command-level mocks for all build orchestration are not evidenced here. | 4.3 | .25 |
| Simplicity/integration | Existing PyInstaller spec and Python stdlib reused across main app, onedir companion and installer, with two necessary native signing branches. No second application framework/runtime dependency. | 4.6 | .10 |

Weighted calculation: 4.6×.35 + 4.7×.30 + 4.3×.25 + 4.6×.10 = **4.555**, above 4.5.

## Fresh evidence

- `.venv/bin/python -m pytest -q tests/test_release_tools.py`: **5 passed in 0.04s**.
- `.venv/bin/python -m py_compile scripts/build_release.py scripts/prepare_signing.py scripts/release_smoke.py installer_entry.py updater_entry.py`: PASS.
- `git diff --check`: PASS.
- AST parse of `Chart Cleaner.spec`: PASS.
- YAML parse and checks of tag trigger, two native matrix entries and `publish.needs == [draft, platform]`: PASS. This is a structure check, not a claim of full GitHub workflow execution.
- Current official [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) confirms the `macos-15` standard runner label maps to arm64 and `macos-15-intel` to Intel. The matrix choice is valid; `build_release.py` additionally rejects wrong Mac native architecture.

## Boundary trace

1. `prepare_signing.py` reads required secrets, imports temporary Mac keychain or locates Windows SignTool, and exports signing paths through `GITHUB_ENV`. It suppresses subprocess argument/error output containing credentials; workflow always removes temporary signing files.
2. `build_release.py` generates literal publisher pins before each frozen build. Mac Info.plist and Windows ProductVersion originate from the canonical package version. App build copies complete companion onedir tree into the exact updater runtime layout.
3. Mac signs native files, nested bundles/frameworks, then outer bundle; submits/notarizes, staples and validates, then checks expected Developer ID/Gatekeeper. Windows signs and verifies every EXE, then checks exact Authenticode subject. Signed final payload archives are hashed into the embedded installer manifest before installer freezing/signing.
4. Installer `datas` are sealed inside a Mac signed bundle or Windows signed onefile executable; core `_embedded_installer` checks outer signature and resource placement before installing.
5. Native smoke runs frozen installer with disposable HOME/USERPROFILE/LOCALAPPDATA/APPDATA; validates UI version; exercises actual source updater transaction with signed previous archive and native verification; verifies byte-preserved settings/history/token-map/custom-rule/preset sentinels after successful upgrade and broken-startup rollback. The staging fixture is directly under staging root, so core transaction cleanup does not delete it before rollback replay.
6. Production frozen companion/network path is explicitly `NOT_RUN`; smoke uses source-only release discovery and broken launch patches because draft payloads are private. Documentation accurately preserves this separate post-publication gate rather than calling it tested.
7. Draft-first workflow, platform upload on success only, aggregate `needs`, required proof keys and manifest from exact final archives prevent a failed platform from publishing. No unsigned path to publish was found.

## Findings and remaining gates

No concrete broken API or implementation blocker found in Step 5 during this review. Offline tests are narrower than native orchestration; real signed builds remain the decisive validation.

**BLOCKED externally:** trusted Apple signing/notarization execution, Windows signing execution, clean Apple Silicon Gatekeeper and Windows standard-user/reputation presentation, native release smoke with a prior signed seed, production frozen-companion/network upgrade, and GitHub publication. A local unsigned build or these offline passes cannot satisfy those gates. The previous signed seed prerequisite is explicitly documented and fails closed when absent.
