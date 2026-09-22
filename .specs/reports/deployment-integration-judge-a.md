# Deployment integration judge A

Scope: Steps 1 and 2 only. No implementation files were changed. Step 4 UI files were not reviewed.

## Step 1 — external user-data storage and migration

**Verdict: PASS — 4.625/5.0** (critical threshold 4.5).

| Criterion | Weight | Score | Evidence |
|---|---:|---:|---|
| Correctness | .35 | 4.7 | `paths.py` resolves the specified macOS/Windows user-data roots; source mode remains repository-relative. `storage.py` validates roots/entries, stages to a temporary directory, validates config, and atomically installs only into an empty destination. `store.ensure_dirs()` wires frozen migration and known config migration. |
| Safety/data preservation | .30 | 4.6 | Symlink/reparse roots, descendants, config, backups, unsafe names, interrupted copies, malformed configs, failed backup/replace, and migration callback failures are covered. Source and prior config bytes are preserved. Native Windows junction execution remains unavailable here. |
| Tests | .25 | 4.6 | `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_storage.py tests/test_store.py` → **41 passed, 1 skipped**. The skipped case is Windows-only junction semantics. |
| Simplicity/integration | .10 | 4.5 | Uses standard-library paths/copy/atomic replacement and existing `store` boundaries without a new dependency. |

Weighted score: `4.7*.35 + 4.6*.30 + 4.6*.25 + 4.5*.10 = 4.625`.

## Step 2 — release manifest, staging, and signature verification

**Verdict: PASS — 4.675/5.0** (critical threshold 4.5).

| Criterion | Weight | Score | Evidence |
|---|---:|---:|---|
| Correctness | .35 | 4.7 | Exact manifest fields/platforms, strict ASCII SemVer, downgrade/minimum/platform checks, pinned GitHub HTTPS redirects, mandatory size/hash/disk checks, complete symlink-graph validation, macOS `codesign` plus `spctl`, and Windows literal-safe Authenticode verification are implemented. |
| Safety/data preservation | .30 | 4.7 | Downloads remain transactional; failed checksum, signature, redirect, archive, disk, and extraction paths remove staging. The extracted archive is independently constrained to the staging root before signature checks. Diagnostics use generic status codes and do not include chart/config paths or content. |
| Tests | .25 | 4.6 | `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_update.py` → **29 passed**. Coverage includes malformed manifests, downgrade/throttle, redirects, checksum/space/signature failures, traversal, chained/cyclic symlink graphs, framework links, macOS command sequencing, and PowerShell literal binding where available. |
| Simplicity/integration | .10 | 4.7 | Standard-library-only client with a narrow `(archive, extracted)` staging contract and injectable transport/verifier seams for tests. |

Weighted score: `4.7*.35 + 4.7*.30 + 4.6*.25 + 4.7*.10 = 4.675`.

## Combined evidence and blockers

- Combined focused run: **70 passed, 1 skipped**, with one pre-existing `main_file` unknown-config warning.
- Compilation of `paths.py`, `storage.py`, `store.py`, `update.py`, and focused tests passed; `git diff --check` passed.
- Native positive Developer ID/notarization and Authenticode validation on clean target operating systems remain external release gates requiring target artifacts/credentials. They are **BLOCKED**, not marked PASS.
- No public signature bypass is present in production defaults; test-only injected verifier/transport seams are explicit.
