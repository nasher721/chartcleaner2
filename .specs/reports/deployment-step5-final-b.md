# Step 5 independent final judge B

Task: `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md`, Step 5. CLAUDE_PLUGIN_ROOT unavailable. This judge did not author the packaging implementation. Reviewed the PyInstaller spec, platform build wrappers, all four release scripts, workflow, release documentation, and packaging/deployment regression tests after the first-release fixture change settled. No application or packaging edits.

## Result

**PASS for implementation, weighted 4.58/5.0** against 4.5. No remaining concrete packaging defect found in the reviewed implementation. This is not signed release approval; the external gates below remain blocked.

| Criterion | Weight | Evidence before score | Score |
|---|---:|---|---:|
| Correctness | .35 | Three products share the existing spec; companion layout/version resources match runtime contracts; signing precedes archive hashes and sealed installer construction; bootstrap fixture removes the circular first-release prerequisite. | 4.6 |
| Safety/data preservation | .30 | Compiled literal trust pins, mandatory native verification, credential failure and unsigned-marker rejection, isolated per-user smoke homes, synthetic sentinel preservation, and draft-first both-platform publication dependencies are present. | 4.7 |
| Tests | .25 | Ten fresh packaging/deployment tests pass; scripts/spec compile; workflow parses and gate assertions pass. Full signed orchestration and clean target presentation require native credentials/runners. | 4.4 |
| Simplicity/integration | .10 | Python stdlib plus existing PyInstaller stack; one source transaction smoke harness and narrow native signing branches. Bootstrap reuses build/package commands. | 4.6 |

## Fresh evidence

- `.venv/bin/python -m pytest -q tests/test_release_tools.py tests/test_deployment.py`: **10 passed in 0.46s**. This includes four disposable transaction outcomes with actual child processes, synthetic nested-data sentinels, archive round-trip/link safety, literal trust pins, final-byte hashes, and unsigned/missing-credential rejection.
- `.venv/bin/python -m py_compile scripts/build_release.py scripts/prepare_signing.py scripts/prepare_previous_fixture.py scripts/release_smoke.py 'Chart Cleaner.spec'`: passed.
- YAML parse plus assertions for both exact platform keys and `publish.needs == ['draft', 'platform']`: passed. This is a local structure check, not an executed Actions run.
- `git diff --check`: passed.
- Existing external-drive `unsigned-app-smoke.json` reports version `2.3.0`, frozen UI PASS and isolated user-data creation. It is unsigned startup evidence only; the ongoing final rebuild/full-suite run is owned by the final verifier, not silently included here.
- Current official [GitHub runner documentation](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) confirms `macos-15` arm64 and `windows-2022` x64. It also documents that hosted Windows runners are administrators with UAC disabled: ordinary CI per-user installation must not be presented as proof of clean standard-user acceptance.

## First-release and trust trace

1. With a real prior tag, CI downloads its exact version/platform archive. With no tag, `prepare_previous_fixture.py` takes `git archive HEAD` from the tagged checkout, changes only the canonical version in that isolated source copy, and identifies its provenance as `synthetic_same_source_previous_version`.
2. The default target `2.3.0` yields synthetic prior `2.2.0`; explicit/default minimum, prior, and target must satisfy `minimum <= prior < target`. Invalid ranges fail before build. The same minimum is embedded in candidate installers and final public metadata. Both platform smoke reports must agree on prior/minimum/provenance.
3. Synthetic fixture app and companion use real release build/package commands and the same required publishers. They are natively signed (and on macOS notarized/stapled) rather than made installable by a signature stub. The runtime validates the prior signed payload/version before using it.
4. Candidate app/companion are signed and verified before final ZIP/hash generation. Signed setup embeds those exact bytes/manifest. macOS seals resources in its signed bundle; Windows setup is onefile. Missing credentials fail closed.
5. Smoke runs the unmodified frozen installer and candidate UI. Source transaction upgrade/rollback retains native verification and starts actual frozen applications; only draft metadata discovery and broken-startup arguments are supplied by the harness. Sentinel bytes and prior-version recovery are checked. Documentation explicitly distinguishes this from historical schema compatibility and a production frozen-companion/network update.
6. Only successful platform jobs upload distribution artifacts. Publish requires both jobs and their PASS proof keys before recomputing final hashes, uploading to the existing draft, then marking it published/latest. No missing-prior-release dependency remains for the first signed release.

## External gates — BLOCKED

- Actual Apple Developer ID signing, notarization, stapling and clean Apple Silicon Gatekeeper presentation.
- Actual Windows Authenticode execution, real Windows file-lock/installer behavior, and clean Windows standard-user/reputation presentation.
- Signed end-to-end native smoke on both release runners, including the newly added signed synthetic bootstrap path.
- Production frozen-companion/GitHub-network upgrade and rollback, and actual GitHub publication.

No certificate, signing service, Windows runner, clean-platform acceptance, or GitHub release execution was claimed from the local tests. The implementation can pass its review while these release gates remain explicitly blocked.
