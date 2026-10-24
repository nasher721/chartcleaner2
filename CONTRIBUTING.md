# Contributing

## Safety requirements

- Use synthetic charts and fixtures only. Never commit PHI, real clinical charts, credentials, signing material, tokens, or local configuration.
- Do not put chart content in logs, test failure messages, issues, or commits.
- Preserve loopback-only UI/API binding and the no-telemetry design.
- A blocked validation decision must withhold cleaned output on every interface. Tests must cover new output paths.
- Destination overrides apply only to one run and must not mutate saved rules.
- Custom Python rules are trusted arbitrary code, not sandboxed plugins.

## Development checks

```bash
python -m pip install --require-hashes -r requirements-dev.lock
python -m pytest -q
python -m compileall -q chartcleaner app_pages scripts tests integrations
node --test integrations/browser-extension/request.test.mjs
git diff --check
```

The accuracy and performance scripts use synthetic fixtures. Clinician review and representative real-chart validation require authorized people and locally retained data; do not upload that evidence to the repository.

## Dependency locks

After intentionally changing `requirements.txt` or `requirements-dev.in`, regenerate both files with pip-tools:

```bash
python -m piptools compile --allow-unsafe --generate-hashes requirements.txt -o requirements.lock
python -m piptools compile --allow-unsafe --generate-hashes requirements-dev.in -o requirements-dev.lock
```

CI installs with `--require-hashes`. GitHub Actions must remain pinned to full commit SHAs. Dependabot proposes dependency and Action updates; review compatibility and hashes rather than disabling the gates.

## Change checklist

1. State the safety invariant and add a failing regression test where practical.
2. Keep legacy behavior when no destination is selected.
3. Run focused tests, then the complete Python and browser-extension suites.
4. Confirm tests leave tracked files unchanged and review the final diff.
5. Update documentation and measured evidence without claiming unperformed clinical, signing, or platform validation.
