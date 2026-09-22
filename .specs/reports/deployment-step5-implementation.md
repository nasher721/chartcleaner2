# Step 5 implementation evidence

Implemented portable PyInstaller app/updater/installer packaging in `Chart Cleaner.spec`, both local wrappers, `scripts/build_release.py`, `scripts/prepare_signing.py`, `scripts/release_smoke.py`, `.github/workflows/release.yml`, `docs/releases.md`, and `tests/test_release_tools.py`.

- The spec uses checkout-relative resources and canonical app version; it bundles only factory defaults/rule packs, never user configuration/custom scripts. The companion onedir is embedded before the outer app is signed.
- Release tooling compiles literal publisher identities before freezing, signs native nested products, notarizes/staples both Mac app and installer, verifies native publisher identity, seals exact signed payload/hash metadata into installers, and hashes final archives only after signing. Missing credentials and unsigned local output fail closed. Subprocess failures suppress credential-bearing argv.
- Tag workflow creates a draft first. It requires the full tests, native signed install/UI, signed older-release sentinel upgrade, and broken-startup rollback on both platforms before final publication. A failure leaves the release draft. The smoke harness injects draft discovery only at the source transaction layer, retaining actual native verification and frozen app launch.
- Packaging tests: `.venv/bin/python -m pytest tests/test_release_tools.py -q` **5 passed**. Archive tests preserve executable bits/framework symlinks and reject external links; metadata tests hash final bytes and reject missing assets; trust-pin tests retain literals; missing credentials/unsigned outputs reject signing.
- `.venv/bin/python -m compileall -q scripts 'Chart Cleaner.spec'` and `git diff --check` **PASS**. Workflow parsed with existing PyYAML and both-platform publish dependency verified.
- PyInstaller 6.22.3 installed as the existing build tool, without adding a runtime dependency. Local unsigned Apple Silicon build is being checked using external-drive scratch/output at `/Volumes/1TB ssd/Remix/chart cleaner/release-validation/`; the separate companion has built successfully. Final result will be appended.

## Explicit external gates

Apple Developer ID/notarization, Windows Authenticode, signed native smoke on both runners, clean-machine Gatekeeper/SmartScreen presentation, and GitHub publication are **BLOCKED / NOT RUN** in this local session. No certificate values were inspected and no commit/push/release was performed. Configure documented secrets and `PREVIOUS_RELEASE_TAG` (older signed seed with both payloads) before a publishable release; absent seed is an intentional blocking gate. Production frozen-companion network upgrades are separately NOT RUN because draft assets are not public.

## Completed unsigned native validation

- PyInstaller 6.22.3 built main app, separate onedir companion, and embedded-payload installer successfully on macOS arm64. `build.log` and `installer-build.log` are under the external-drive validation directory above.
- Launched `Chart Cleaner.app/Contents/MacOS/Chart Cleaner --no-browser --port <ephemeral>` with isolated HOME. HTTP `/settings` returned Chart Cleaner and expected version `2.3.0`; `~/Library/Application Support/Chart Cleaner/config.json` was created inside that disposable HOME. Process terminated cleanly. Evidence: `unsigned-app-smoke.json` reports `frozen_ui: PASS`, `user_data_created: true`.
- Inspected both built app `Info.plist` files: `CFBundleShortVersionString=2.3.0`. Verified companion runtime executable is present. Both installer resources resolve inside the installer bundle (PyInstaller's Frameworks/Resources links remain sealed internally).
- This proves native freeze/resource layout and local frozen UI startup only. It does not satisfy Developer ID/notarization or signed install/update/rollback gates; the output is explicitly marked NOT-DISTRIBUTABLE.
