# Step 2 final review remediation

Implementation scope: `chartcleaner/update.py`, `tests/test_update.py`, this report.
Public function signatures and the `(archive_path, extracted_root)` staging
handoff are unchanged. Other agents' files and `config.json` were not edited.

## Changes

- After deferred archive links are created, resolve every link against the
  complete graph. Reject unresolved links, cycles and targets outside extraction.
  Reject entry creation through a link parent before creating it. Failed new
  extraction trees are removed. Regression fixtures include chained escape,
  circular links, escape-parent writes, and valid macOS framework links.
- macOS now uses `codesign --verify --deep --strict --test-requirement` with
  Apple generic anchor, Developer ID CA/application certificate OIDs, and the
  exact expected CN. The requirement value is UTF-8 encoded as a hexadecimal
  literal, avoiding parser injection and local compiler differences around
  backslash escaping. Gatekeeper assessment is a separate plain `spctl --assess
  --type execute` command; both commands must succeed.
- Windows uses `-EncodedCommand` with a fixed verification program. Path and
  publisher are separately encoded JSON data, decoded at runtime; neither is
  interpolated into executable PowerShell syntax. Signature-provider errors,
  invalid signatures, absent certificates and mismatched publishers fail closed.
- SemVer numeric components require ASCII digits. URL validation evaluates ports
  and accepts only default/443 HTTPS ports, rejecting malformed/empty ports and
  raw whitespace/control characters.

## Fresh verification

- `rtk proxy .venv/bin/python -m pytest --confcutdir=tests -q tests/test_update.py`
  after remediation: **29 passed**.
- Final combined run of `tests/test_update.py tests/test_storage.py
  tests/test_store.py`: **70 passed, 1 skipped**. The storage agent added tests
  during this work; this count is the final observed shared-tree result.
- `rtk proxy .venv/bin/python -m py_compile chartcleaner/update.py
  tests/test_update.py`: passed.
- `rtk git diff --check`: passed.
- Installed `/usr/bin/csreq` compiled the generated publisher requirement,
  including a CN with apostrophe, quotes and backslash. Installed `codesign`
  rejected a nonmatching system binary for failing the requirement rather than
  a syntax error. Installed `spctl` accepted the corrected command syntax.
- Installed PowerShell executed the generated signature script against a fixture
  signature provider. Paths/publishers containing spaces, apostrophes, quotes,
  brackets, semicolons and PowerShell expressions remained literal. Matching
  valid identity succeeded; mismatch and unsigned outcomes failed. No injected
  marker file was created.

The existing unknown `main_file` pytest configuration warning remains.

## Limits

Positive Developer ID/notarization and native Windows Authenticode validation
still require their target-platform signing artifacts and credentials. The
PowerShell fixture validates the real interpreter and data binding, not a real
Windows certificate. These external gates remain unverified. The implementation
does not self-mark Step 2 complete; independent judges own the next assessment.

Reference: Apple's [requirement-language documentation](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/RequirementLang/RequirementLang.html)
and the installed `codesign(1)`/`spctl(8)` manuals were consulted; actual local
compiler behavior determined the tested literal encoding.
