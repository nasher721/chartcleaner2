# Security policy

## Report a vulnerability privately

Use GitHub's private vulnerability reporting for `nasher721/chartcleaner2`, or contact the repository owner through GitHub. Do not open a public issue for an unpatched vulnerability. Include the affected version, a synthetic reproduction, impact, and a suggested remediation if available.

Never include real clinical charts, patient identifiers, credentials, signing material, access tokens, or local configuration in a report. Use synthetic data only. Do not place chart content in logs, issue text, crash reports, or repository history.

## Scope and deployment boundary

Chart Cleaner is designed for local use on `127.0.0.1`, without telemetry. Exposing the UI or API to other hosts changes the security boundary and is not a supported deployment. Custom Python rules execute with the user's full privileges; they are trusted code, not a sandboxed rule format.

Blocked validation results must not release cleaned output through the UI, CLI, REST, MCP, batch, clipboard, watcher, or exporter paths. Security fixes should preserve that fail-closed behavior and add regression coverage.

## Supported versions

Security fixes target the current `main` branch and the next release. Native desktop releases additionally require the signing, notarization, installer, upgrade, and rollback checks documented in `docs/releases.md`.
