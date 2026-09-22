# Step 2 cycle 3 — independent judge A

**FAIL — 4.175/5.0** (critical threshold 4.5).

## Evidence

- `.venv/bin/python -m pytest --confcutdir=tests -q tests/test_update.py`: **20 passed**, one existing unknown `main_file` configuration warning.
- `.venv/bin/python -m py_compile chartcleaner/update.py tests/test_update.py`: passed.
- Strict nonempty SemVer identifiers and the real local HTTP pre-follow redirect rejection test address the prior cycle findings. Normal GitHub CDN metadata is allowed. Native verification is called without a public skip callback; rollback size is mandatory and expanded size comes from ZIP metadata.
- Independent archive probe demonstrates that `safe_extract_archive()` accepts a chained symlink whose resolved target is outside the extraction root.

## Blocking finding

**Unsafe chained symlink escape**, `chartcleaner/update.py:169-190`.

The link checks normalize each textual target independently, then create all links without validating the completed link graph. This permits the following archive:

```text
d/                 directory
a -> d
d/up -> ..
escape -> a/up/..
```

Every textual target passes current validation. After extraction, `out/escape` resolves to `out`'s parent. The local probe returned `True` for `(out / 'escape').resolve() == out.parent.resolve()` and extraction returned normally. The helper therefore fails its promise to preserve only in-bundle links. This can direct subsequent signature checks or updater traversal outside the verified payload.

Fix: validate resolved links against the complete staged link graph before returning; reject loops, resolved escapes, and unsafe link-parent paths, cleaning failed staging. Avoid creating any later symlink through an already escaping parent. Add this chained-link regression while retaining a normal macOS framework-link fixture.

## Scores

| Criterion | Weight | Evidence | Score |
|---|---:|---|---:|
| Correctness | .35 | Prior parser/redirect/signature API issues are resolved; extraction is still not safe for all accepted link graphs. Async readiness and disabled/manual settings belong to the later UI integration step. | 4.5 |
| Safety/data preservation | .30 | Transaction cleanup and verification ordering protect ordinary failures, but the reproduced escape violates the archive trust boundary. | 3.5 |
| Tests | .25 | 20 passing local tests include real redirect rejection, SemVer, hashes, staging failures, and native-verifier rejection seams; chained link resolution is uncovered. | 4.4 |
| Simplicity/integration | .10 | Standard-library-only release client and status model remain appropriately scoped. | 4.5 |

Calculation: `4.5*.35 + 3.5*.30 + 4.4*.25 + 4.5*.10 = 4.175`.

Native Developer ID/notarization and Authenticode verification are external platform/credential gates and remain unverified. This review changed only this report.

## Comprehensive native-command review — superseding score

**FAIL — 3.50/5.0.** Further read-only command checks found two production verification defects hidden by the module-wide verifier mock. These are implementation defects, separate from unavailable signing credentials.

### macOS publisher verification uses the wrong command mode

`chartcleaner/update.py:226-231` invokes `spctl --assess --type execute --requirement <publisher expression> <path>`. The installed `spctl(8)` documents `--requirement` as selecting requirement-source subjects for rule-update operations. It is not an assessment restriction on the following path. Running the exact command shape with fixture publisher text and `/usr/bin/true` returns `invalid API object reference` for the expression.

Required fix: enforce publisher identity through `codesign --verify --deep --strict --test-requirement '=...' <app>` using correctly escaped requirement-language strings and a Developer ID requirement; separately run `spctl --assess --type execute <app>` for Gatekeeper/notarization policy assessment. The installed `codesign(1)` documents that literal requirement source needs an initial `=`; strings use requirement-language quoting, not shell single quotes. Add command-contract tests that ensure publisher mismatches fail and both verification stages must succeed. Full positive signed/notarized evidence remains an external gate.

### Windows positional inputs never bind to `$args`

`chartcleaner/update.py:234-235` passes `-Command`, then script text, then path/publisher. PowerShell treats the trailing strings as more command text rather than positional script arguments. A local installed `pwsh` probe with a harmless stub `Get-AuthenticodeSignature` and the same invocation shape outputs empty path/publisher and then attempts to execute `C:\Program`, exiting 1. Thus normal paths containing spaces cannot be verified with this command.

Required fix: use a temporary fixed PowerShell script invoked with `-File` and literal path/publisher arguments, or another tested mechanism that passes data separately from code. Add a script execution test using a stub signature provider to verify exact binding for spaces, apostrophes and shell metacharacters; test valid/mismatched publisher and rejected-signature outcomes. Native Authenticode positive evidence remains an external gate.

### SemVer ASCII and malformed-port edges

The regex still uses Unicode `\d` for numeric components. `Version.parse('12٢.3.4')` currently succeeds as `122.3.4`; analogous minor/patch inputs also succeed. Strict SemVer requires ASCII digits. Replace `\d` with `[0-9]` or apply `re.ASCII` and add rejection coverage. `_is_github_https('https://github.com:bad/releases/file')` also currently returns True: evaluate the parsed port and restrict accepted ports appropriately so malformed port syntax cannot pass manifest validation.

### Revised numerical assessment

| Criterion | Weight | Final score | Evidence |
|---|---:|---:|---|
| Correctness | .35 | 3.0 | Both production native-verification command contracts fail; parser still accepts Unicode numeric versions. |
| Safety/data preservation | .30 | 3.5 | The fail-closed command failures prevent unsigned installation, but accepted chained symlink escapes violate extraction isolation. |
| Tests | .25 | 3.8 | 20 passing tests cover many failure paths; global native verifier mocking hides both real command defects. |
| Simplicity/integration | .10 | 4.5 | Standard-library design and narrow responsibilities remain appropriate. |

Calculation: `3.0*.35 + 3.5*.30 + 3.8*.25 + 4.5*.10 = 3.50`.

No application code was edited. All findings were produced by reading, disposable archive probes, installed command documentation, and harmless local command-binding tests.
