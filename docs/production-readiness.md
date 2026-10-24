# Production readiness

Status: **v2.4 — hardened local install**. This is the document a hospital IT
department, a security reviewer, or a new maintainer reads before deploying or
supporting Chart Cleaner. Each item names the artifact that proves it, so a
review is a checklist rather than an interview.

The deployment model is deliberately narrow: **one install per clinician, on
their own machine, serving only `127.0.0.1`**. Everything below assumes that.
If that changes (a shared workstation, a server deployment, a browser tab on a
colleague's screen), the threat model changes with it and this document must be
revised before the deployment is approved.

---

## 1. Data handling

| Requirement | How it is met | Evidence |
| --- | --- | --- |
| Chart text never leaves the machine | No outbound chart traffic exists in the app; the only network client is the update check, which sends a version string. The LLM features refuse non-loopback base URLs. | `chartcleaner/local_llm.py` (loopback guard), `chartcleaner/summarizer.py` |
| No telemetry | Nothing is sent anywhere, including on crash. The error screen says so and offers a local traceback download. | `app_pages/common.py` (`_render_error_page`) |
| Stored chart data is encrypted at rest | Token maps, recent charts, known-good charts and the edit log use the OS keystore or an encrypted file backend. | `chartcleaner/secure_store.py`, `chartcleaner/store_data.py` |
| Stored chart data expires | Retention purge on every startup; default 14 days, `0` keeps forever. | `store.purge_old_data`, `tests/test_storage.py` |
| De-identification is not silently degraded | The `nlp_redaction` stage reports when the model is unavailable instead of passing text through. | `chartcleaner/stage_nlp.py`, Doctor page |

## 2. Local attack surface

| Requirement | How it is met | Evidence |
| --- | --- | --- |
| Bind to loopback only | Default `--host 127.0.0.1`; the port probe never falls back to another interface. | `app.py::main` |
| DNS-rebinding is refused | `Host` header must resolve to loopback; websockets are closed with 1008. | `chartcleaner/host_guard.py` |
| REST API is not open to web pages | Loopback client check, `Origin` allowlist (this app or a browser extension), 5 MB body cap, `Authorization: Bearer` compared with `hmac.compare_digest`. | `chartcleaner/api.py` |
| API token is per install and private | 32-byte URL-safe token in `data/api_token`, created `0o600`, rotatable in Settings. | `api.get_token` / `rotate_token` |
| Browser-side containment | CSP (`default-src 'self'`, `connect-src 'self'` + own websocket), `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Permissions-Policy`. | `chartcleaner/security_headers.py`, `tests/test_production_hardening.py` |
| Custom Python rules are not a drive-by execution path | Listing reads metadata with `ast` and never imports; execution requires a SHA-256 match against `data/custom_rule_trust.json`. Editing the file revokes approval. | `chartcleaner/custom_rules.py`, `tests/test_production_hardening.py` |
| A malformed config cannot poison the pipeline | `NaN`/`Infinity` rejected, required keys enforced, prefs coerced to the shape of their defaults. | `engine.load_config`, `store_prefs._coerce_pref`, `tests/test_validation.py` |

**Residual risk, accepted:** custom rules still run with the full privileges of
the user account. That is the feature — a rule is arbitrary Python by design.
The trust record means *the user chose to run that code*, not that the code is
safe. `Scripts` states this in plain language above the editor.

## 3. Operability

| Requirement | How it is met | Evidence |
| --- | --- | --- |
| Logs are bounded | `data/chartcleaner.log`, 2 MB × 3 rotations. Nothing can fill the disk. | `chartcleaner/logging_setup.py` |
| Logs cannot leak PHI by accident | Formatter redacts SSNs, phones, emails, MRN/account numbers and 7+ digit runs, and truncates any single record at 4 000 chars. Chart text is never logged deliberately. | `logging_setup.redact`, `tests/test_production_hardening.py` |
| Frozen builds capture stray output | `sys.stdout`/`sys.stderr` are routed through the same rotating logger instead of an unrotated file. | `logging_setup.capture_console` |
| Liveness vs readiness are distinct | `/api/v1/health` = process up. `/api/v1/ready` = config parses, data and export directories writable, rule set loaded; 503 when not. | `chartcleaner/readiness.py`, `chartcleaner/api.py` |
| A page failure is diagnosable | Error boundary renders a recovery screen and records the traceback; the app never shows a bare 500. | `app_pages/common.py::guarded`, `tests/test_error_boundary.py` |
| Dependencies are checked for known CVEs | Scheduled `pip-audit` job, weekly and on demand. | `.github/workflows/security.yml` |
| Self-diagnosis is available to the user | Doctor page checks every dependency and offers a fix per problem. | `app_pages/doctor.py`, `chartcleaner/doctor.py` |

## 4. Release and upgrade

| Requirement | How it is met | Evidence |
| --- | --- | --- |
| Builds are reproducible and gated | Hash-locked CI/release dependencies; immutable Action commit pins; pytest matrix on Ubuntu 3.10/3.12, macOS, Windows; `compileall` syntax gate; `git diff --exit-code` proves tests leave tracked files alone. | `requirements*.lock`, `.github/workflows/tests.yml`, `.github/dependabot.yml` |
| Releases include dependency inventory | Each signed platform candidate emits a CycloneDX SBOM published with its artifacts. | `scripts/generate_sbom.py`, `.github/workflows/release.yml` |
| Artifacts are signed and notarized | macOS codesign + notarization, Windows Authenticode, credentials imported per run and destroyed afterwards. | `.github/workflows/release.yml`, `scripts/prepare_signing.py` |
| An upgrade is proven before it ships | Release pipeline installs the previous signed release, upgrades it with sentinels, then forces a broken startup to prove rollback. Both platforms must pass before publish. | `scripts/release_smoke.py`, `release.yml::publish` |
| The updater cannot be pointed at arbitrary code | Update manifest is verified; the client checks platform, version and minimum supported version. | `chartcleaner/update.py`, `chartcleaner/updater.py` |
| Version is visible where IT looks | Window title, Settings, `/api/v1/health`, release notes in `docs/releases.md`. | `chartcleaner/__init__.py` |

## 5. Support handoff

| Requirement | How it is met | Evidence |
| --- | --- | --- |
| A user can self-serve the common failures | Doctor page: one card per dependency, with the fix command. Settings: local API token, backups, data deletion. | `app_pages/doctor.py`, `app_pages/settings.py` |
| Support can get the log without a terminal | Log path is `data/chartcleaner.log`; `logging_setup.tail()` is the read path used by the app. | `chartcleaner/logging_setup.py` |
| Accessibility | Skip link, landmarks, `aria-current`, live regions, keyboard-operable controls, `<html lang="en">`. | `app_pages/a11y.py`, `tests/test_a11y.py` |
| Behaviour is protected against regression | 990+ tests including byte-identical golden output for the cleaning pipeline. | `tests/`, `tests/test_golden.py` |

---

## Deliberately not built

These came up in the review and were rejected with reasons, so the next reviewer
does not have to re-litigate them.

- **Rate limiting on `/api/v1/*`.** The caller must already be on this machine
  *and* hold the per-install token. A process that meets both conditions runs as
  the same user and could simply run the CLI instead. A limiter would add state
  and failure modes without changing who can do what. Revisit only if the API is
  ever exposed beyond loopback.
- **Sandboxing custom Python rules.** A sandbox that is not a real security
  boundary is worse than an honest one: it invites trust it cannot earn. The
  digest-approval record plus a plain-language warning is the honest version.
  A declarative JSON rule format is the correct long-term answer and is tracked
  as future work, not a patch.
- **Crash reporting / telemetry.** Excluded by the product's core promise. The
  local log and the traceback download are the substitute.
- **Multi-user auth.** The app is single-user by design. A shared-workstation
  deployment needs a different product, not a login screen.

## Verification commands

```bash
# full behaviour + hardening suite
python -m pytest -q

# the hardening properties specifically
python -m pytest -q tests/test_production_hardening.py tests/test_error_boundary.py

# syntax gate used by CI
python -m compileall -q chartcleaner app_pages scripts tests integrations

# readiness of a running instance (token not required)
curl -s http://127.0.0.1:8765/api/v1/ready | python -m json.tool
```
