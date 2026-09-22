# Signed desktop releases

The tag must equal `v` + `chartcleaner.__version__`. GitHub Actions creates a draft first. Both native jobs must pass tests, signature verification, frozen installer/UI checks, previous-version sentinel preservation, and broken-startup rollback before the final job uploads and publishes. A failed or credential-less build leaves the release draft; there is no unsigned publication route.

## Prepare once

Configure the `desktop-signing` GitHub environment. Environment protection may require a release maintainer's approval. Never put certificates or passwords in the repository.

| Secret | Exact meaning |
| --- | --- |
| `MACOS_PUBLISHER` | Full leaf certificate common name, `Developer ID Application: … (TEAMID)`; compiled into all products. |
| `WINDOWS_PUBLISHER` | Exact full Authenticode certificate Subject beginning `CN=`; compiled into all products. |
| `MAC_CERT_BASE64`, `MAC_CERT_PASSWORD` | Base64 PKCS#12 Developer ID certificate/private key and its password. |
| `APPLE_ID`, `APPLE_APP_PASSWORD`, `APPLE_TEAM_ID` | Apple notarization account, app-specific password, and team. |
| `WINDOWS_CERT_BASE64`, `WINDOWS_CERT_PASSWORD` | Exportable PKCS#12 public-trust signing certificate/private key and its password. Hardware-bound signing services require a signing-tool integration before this workflow is used. |

For later releases, set repository variable `PREVIOUS_RELEASE_TAG` to an older **signed** release containing both `ChartCleaner-VERSION-PLATFORM.zip` assets. When unset (first signed release), CI creates an isolated `git archive HEAD` source copy, lowers only its canonical version, and builds/signs a real previous-version app fixture with the same publisher. The default is the immediately lower patch, or the preceding minor/major at `.0` when necessary: target `2.3.0` produces fixture `2.2.0`. Optional `PREVIOUS_FIXTURE_VERSION` overrides that synthetic version.

`MINIMUM_SUPPORTED_VERSION` defaults to the chosen prior/fixture version, and that same value is embedded in both installers and the final published manifest. CI requires `minimum <= previous < target`; an incompatible explicit minimum, same/newer prior version, or target `0.0.0` fails clearly. Nothing waives the updater's minimum-version guard. Smoke reports label synthetic fixtures `synthetic_same_source_previous_version`; they demonstrate signed replacement/rollback and version-handshake behavior, **not historical application/schema compatibility**. Prefer a real prior signed release as soon as one exists.

Confirm private-key export is permitted by your certificate provider. Publisher identity changes require a planned trust transition; older installed updaters retain their compiled pins.

Only the native signing jobs receive secrets. Temporary certificates/keychain are removed in an `always()` step. The workflow uses standard GitHub-hosted `macos-15` arm64 and `windows-2022` x64 runners; native architecture is checked by the build script. Adjust runner availability deliberately, never cross-build a Windows payload on macOS.

## Build and gate sequence

1. Install `requirements.txt` and the already-used build tool `pyinstaller` under Python 3.12. Run the complete pytest suite and compilation checks.
2. Generate `chartcleaner/release_identity.py` with literal expected publishers and the fixed public GitHub manifest URL **before freezing**. Runtime environment variables never set trust pins.
3. Build the separate onedir updater and main app with `Chart Cleaner.spec`. Bundle the companion runtime at `Contents/Helpers/updater` on Mac and `updater/` on Windows. Bundle factory defaults and rule packs only, never local `config.json`, history, token maps, or custom scripts.
4. Mac: sign Mach-O files, nested bundles/frameworks, then the outer app with Hardened Runtime and timestamp. Notarize, retain Apple's log, staple, validate the ticket, and assess the expected Developer ID identity. Windows: sign every shipped EXE, including main and companion, with SHA-256 and timestamp; verify Authenticode and exact publisher. ProductVersion and Mac CFBundleShortVersionString use the canonical version.
5. ZIP the final signed/stapled app, preserving executable modes and relative framework symlinks. Compute its SHA-256/size and embed it with `installer-manifest.json` in the installer. Build `Install Chart Cleaner.app` or onefile `ChartCleanerSetup.exe`; sign/notarize/staple the installer as appropriate. Installer resources are sealed by the outer native signature.
6. Run `scripts/release_smoke.py` against the candidate and older signed archive (published or explicitly synthetic). It gives the unmodified frozen installer temporary HOME/LOCALAPPDATA/APPDATA, launches the app, and checks `/settings` for the expected version. It then uses the **same source updater transaction** with synthetic settings/history/token-map/custom-rule sentinels; native signature validation remains active. A deliberately invalid startup argument must trigger one rollback and relaunch of the older app. It checks all sentinels byte-for-byte.
7. The final job requires both proof reports, hashes the exact final platform archives into `update-manifest.json`, uploads all assets, and only then switches the draft to published/latest.

The source smoke harness substitutes draft release discovery because private draft assets are unavailable from the public updater URL. It does **not** establish a production frozen-companion/GitHub-network upgrade; that remains a post-publication smoke check. Windows onedir DLL/PYD and resource integrity is enforced by the exact SHA-256 of the pinned HTTPS manifest archive; EXE publisher verification is an additional gate, not a sealed-directory signature. The companion runtime is copied from the freshly verified archive. No runtime test bypass ships. The rollback fixture changes the launch arguments in the harness, never the signed executable.

## Local validation

`./build_app.sh` or `build_app.bat` builds a clearly marked **unsigned, not distributable** app and updater. Install PyInstaller in the existing venv first if absent. `--output /path/on/large/disk` keeps build scratch space off a small system disk. A directory containing `NOT-DISTRIBUTABLE.txt` is rejected by release packaging; use a fresh output directory for signed builds. The scripts do not install new packages implicitly or delete unrelated build directories.

Run `python -m pytest tests/test_release_tools.py -q` for offline packaging/manifest regression checks. Native signing and frozen release smoke checks require real target runners and signing credentials; source tests do not satisfy those gates.

## Remaining manual release evidence

Record clean Apple Silicon Gatekeeper behavior and clean Windows standard-user install/signature/SmartScreen presentation separately. Native CI signatures do not prove reputation or hospital IT policy acceptance. Exercise production update discovery, update confirmation, restart, and rollback from a previously installed signed release; record old/new versions and sentinel preservation. Uninstall removes the app/shortcut, while the per-user Chart Cleaner data directory stays intact unless the user explicitly removes it.

References: [PyInstaller specs](https://pyinstaller.org/en/stable/spec-files.html), [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), [Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool), [GitHub runner architectures](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
