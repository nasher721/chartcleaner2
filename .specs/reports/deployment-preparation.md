# Cross-platform deployment updater preparation

Prepared 2026-09-22 from `.specs/plans/cross-platform-deployment-updater.design.md`.

## Repository and workspace evidence

- Repository: `/Volumes/1TB ssd/Remix/chart cleaner/chart-cleaner`
- Branch: `main`, tracking `origin/main`; local branch is 18 commits ahead.
- Pre-existing working-tree change: `config.json` is modified and contains learned rules. It is unrelated to this preparation and must be preserved.
- Disk: `/Volumes/1TB ssd` has approximately 871 GiB available.
- Existing application stack: Python, NiceGUI, PyInstaller, and repository-relative source paths for development.
- Existing build entry points: `build_app.sh`, `build_app.bat`, and `Chart Cleaner.spec`.
- Existing installation entry points: `install.sh`, `install.bat`, and `install.ps1`.
- Existing tests: pytest suite under `tests/`, configured by `pytest.ini` and `conftest.py`.
- No `pyproject.toml`, `setup.cfg`, `tox.ini`, `Makefile`, package manifest, or existing GitHub Actions workflow was found.
- No updater module, update manifest, companion updater executable, installer project, or release workflow was found in the current tree.

## Verification commands for implementation

Run the focused tests introduced by each step, then the complete suite:

```text
rtk pytest -q
```

Run syntax/import checks for new Python modules as appropriate:

```text
rtk python -m compileall chartcleaner tests
```

Run the existing platform build scripts only on their matching platforms and with the project environment installed:

```text
rtk ./build_app.sh
rtk ./build_app.bat
```

Release workflow validation should use the repository's CI linter or GitHub Actions validation available in the implementation environment. A clean Apple Silicon Gatekeeper run, notarization, and clean Windows signature/reputation run remain explicit external gates.

## Step ownership and boundaries

1. Storage and migration owns mutable user-data paths, portable migration, schema backup/migration, validation, and source/destination precedence. It must not own update downloads or replacement.
2. Manifest/download/signature staging owns release metadata parsing, semantic versions, platform selection, throttling, HTTPS staging, byte-count/SHA-256 checks, signature verification calls, disk-space checks, and privacy-safe diagnostics. It must not replace the installed application.
3. Companion updater owns process shutdown, backup, replacement, health-marker wait, rollback, relaunch, locks, and platform file/bundle mechanics. It receives only PID, installed path, staged asset path, and expected version; it never opens the data directory.
4. UI lifecycle owns asynchronous startup checks, settings state, manual checks, confirmation, update handoff, health marker creation after data/local-server readiness, and the revised About wording. It must never block cleaning or send clinical data.
5. Packaging/release owns frozen main/updater artifacts, per-user installers, signed release jobs, manifest generation from signed assets, and draft-only publication gating. It must not weaken runtime verification to accommodate missing credentials.
6. Final verification owns integration coverage, sentinel-data upgrade fixtures, failure/rollback exercises, privacy review, and documenting blocked platform/manual gates.

Steps 1 and 2 can be designed and implemented in parallel after the storage/update API boundary is agreed. Step 3 depends on the staged-asset contract from Step 2 and the install/data boundary from Step 1. Step 4 depends on Steps 1–3. Step 5 depends on all runtime contracts. Step 6 depends on all implementation steps.

## External and platform gates

The following are intentionally separate from local test completion and must remain visibly blocked when credentials or target platforms are unavailable:

- Apple Developer ID signing, notarization submission, stapling, and clean Apple Silicon Gatekeeper assessment.
- Windows Authenticode signing with the expected publisher and clean Windows x64 reputation presentation.
- Full native installer execution on each clean target OS when that OS is unavailable.
- GitHub Release publication using the actual repository credentials.

No task step may claim these gates pass based on source inspection or unsigned local artifacts.

## Skill/orchestration discovery

The requested skill resolves to `/Users/Nash/.agents/skills/sdd-implement/SKILL.md`. It is a standalone skill file, not a plugin directory exposing `CLAUDE_PLUGIN_ROOT`. Narrow searches under `/Users/Nash/.agents` and `/Users/Nash/.codex` found no `prompts/judge.md` or `prompts/developer.md` associated with this skill. The implementation task therefore records judge requirements and rubrics directly, while the parent agent should use the available native orchestration surface for execution.
