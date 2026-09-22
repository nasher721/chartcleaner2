# Step 5 final independent judge A

Task `.specs/tasks/in-progress/cross-platform-deployment-updater.feature.md`; source design `.specs/plans/cross-platform-deployment-updater.design.md`; CLAUDE_PLUGIN_ROOT unavailable. Reviewed current spec, wrappers, all four release scripts, workflow, release tests, deployment fixture, and release documentation. This reviewer authored no application/build/test code. Includes the first-release bootstrap delta after the earlier Step 5 review.

**PASS for implementation — 4.605/5**, critical threshold 4.5. Actual signed release readiness remains BLOCKED.

| Criterion | Weight | Evidence | Score |
|---|---:|---|---:|
| Correctness | .35 | Main app, external onedir companion, and sealed platform installer layouts match runtime paths. Signing precedes app archive hashes and installer construction. Native smoke verifies install UI, previous-version upgrade and broken-startup rollback. Bootstrap now selects minimum <= previous < target, snapshots tracked source, changes only canonical version, and signs the synthetic prior fixture before smoke. Published manifest retains that same floor. | 4.6 |
| Safety/data preservation | .30 | Missing credentials and unsigned output markers fail closed. Publisher pins are compiled before freezing. Signing secrets stay in runner environment/temporary files and command errors omit arguments. Smoke guards occupied ports, uses isolated per-user roots and synthetic sentinels. Draft publication requires both signed platform proofs and exact final asset hashing. Synthetic provenance is explicit. | 4.7 |
| Tests | .25 | Fresh complete suite 328 passed, 0 failed, 3 skipped includes five packaging checks and five deployment/bootstrap cases. Workflow dependency/matrix and spec parse checks passed. No positive native signing/installer/clean-machine claim is inferred from source or unsigned app evidence. | 4.5 |
| Simplicity/integration | .10 | Existing PyInstaller/NiceGUI stack and standard library retained; one spec and shared updater transaction. Bootstrap is a source snapshot/version change and native build, not a new updater mode or signature bypass. | 4.6 |

Calculation: `4.6*.35 + 4.7*.30 + 4.5*.25 + 4.6*.10 = 4.605`.

## Fresh evidence

- `PATH="$PWD/.venv/bin:$PATH" rtk pytest -q`: **328 passed, 0 failed, 3 skipped**. Root `conftest.py` activates the real NiceGUI testing plugin; no `--confcutdir` bypass or fake fixture was used.
- `PATH="$PWD/.venv/bin:$PATH" rtk python -m compileall -q chartcleaner scripts tests`: PASS.
- `git diff --check`: PASS.
- Parsed workflow: both exact platform matrix entries present; publish depends on `[draft, platform]`. Parsed `Chart Cleaner.spec` as Python AST.
- Direct bootstrap contract check: target `2.3.0` produces previous/floor `2.2.0` with `synthetic_same_source_previous_version` provenance. Regression rejects incompatible minimum, non-older prior versions, prerelease fixture, and `0.0.0` default interval.
- Final unsigned native build input record started `2026-09-22T22:07:09.692740+00:00`; all **42** saved input hashes match current files, including final `app.py` and `chartcleaner/updater.py`. Build launch result is recorded separately in final verification once complete.
- `config.json` remained `ce470295e099ae780baf753f78d7a72ca056c76ef6043fe31fb0b3ec4262dca9`.

## Boundaries

No blocking implementation finding. Native signing/notarization/stapling, clean Gatekeeper/Windows reputation, Windows target execution, signed installer/upgrade smoke, and GitHub publication still require external evidence. Source smoke intentionally substitutes private draft metadata and failed launch arguments while retaining native signature and transaction checks; production frozen-companion/network update remains NOT_RUN. Synthetic prior version proves transaction behavior, not historical schema compatibility. These distinctions are documented and preserved in reports; no gate is waived.
