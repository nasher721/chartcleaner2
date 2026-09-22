# Cross-platform deployment updater: official references

Research date: 2026-09-22. Scope: the existing Python/NiceGUI/PyInstaller stack and the design in `../plans/cross-platform-deployment-updater.design.md`.

## Direct recommendations

- Build Apple Silicon artifacts on an explicit GitHub-hosted arm64 label such as `macos-14` or `macos-15`; avoid `macos-latest` for release reproducibility because GitHub can move that alias. The current runner reference lists standard M1 arm64 labels `macos-latest`, `macos-14`, and `macos-15`; arm64 larger-runner labels include `macos-14-xlarge` and `macos-15-xlarge` but require the repository's larger-runner availability.
- Keep the main app and `chart-cleaner-updater` as separate PyInstaller targets. The existing build is already an onedir macOS `.app`/Windows directory build. PyInstaller documents one-folder bundles, multiple applications sharing a bundle, and the fact that one-file data is extracted to a temporary directory; it does not define a companion self-updater protocol, so process handoff, replacement, health markers, rollback, and locking remain application-level behavior.
- For macOS distribution, sign nested executables and the app with a Developer ID certificate, enable Hardened Runtime, include a secure timestamp, submit the final distributable with `xcrun notarytool`, inspect the notary log even on success, staple with `xcrun stapler`, then verify with `codesign` and Gatekeeper assessment (`spctl`). Final Gatekeeper testing needs a clean Apple Silicon Mac; CI proves the scripted checks but not every first-launch UI outcome.
- For Windows, sign the installer and every shipped PE executable with a CA-trusted Authenticode identity. Verification should require a valid Authenticode chain and compare the signer identity with the configured expected publisher. `signtool verify /pa /v` and PowerShell `Get-AuthenticodeSignature` provide the platform checks; a valid signature alone does not prove that the signer is the intended publisher.
- Install into `%LOCALAPPDATA%\\Programs\\Chart Cleaner` (`FOLDERID_UserProgramFiles`) and place the Start-menu shortcut under `%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs` (`FOLDERID_Programs`). These are per-user locations; do not use common/all-users folders for the no-elevation design. Microsoft documents the locations and scope, but does not prescribe how a Python app creates a `.lnk`; test shortcut creation and launch in a clean standard-user account.

## Official docs evidence

### GitHub Actions and release credentials

- [Choosing the runner for a job](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job) - documents `runs-on`, current standard macOS arm64 labels, and the warning that `-latest` labels can advance.
- [Larger runners reference](https://docs.github.com/en/actions/reference/runners/larger-runners) - documents arm64 M2 larger-runner labels, availability caveats, and the limitation that arm64 runners do not have a static Apple UDID.
- [Secrets](https://docs.github.com/en/actions/concepts/security/secrets) - documents repository, organization, and environment secrets. Release signing credentials should be injected only into the signing jobs; environment secrets can require approval.
- [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use) - recommends least-privilege credentials and minimum `GITHUB_TOKEN` permissions.

### PyInstaller packaging

- [What PyInstaller Does and How It Does It](https://pyinstaller.org/en/stable/operating-mode.html) - documents onedir as a folder containing the executable and dependencies, and onefile as a self-extracting executable. It explicitly recommends validating onedir before onefile.
- [Using Spec Files](https://pyinstaller.org/en/stable/spec-files.html) - documents `EXE`, `COLLECT`, `BUNDLE`, `datas`, and multipackage bundles for multiple applications. This supports building the app and updater as distinct frozen executables while reusing analysis where appropriate.
- [Run-time Information](https://pyinstaller.org/en/stable/runtime-information.html) - documents onefile extraction to a temporary directory and runtime resource paths.

The current repository's [`Chart Cleaner.spec`](../../Chart%20Cleaner.spec) and [`build_app.sh`](../../build_app.sh) already use `EXE` + `COLLECT` + `BUNDLE` for an onedir macOS app; [`build_app.bat`](../../build_app.bat) uses an onedir Windows build. The docs support retaining that packaging model. They do not guarantee that an updater can replace a running bundle or executable, so that behavior needs platform-specific integration tests.

### Apple signing and notarization

- [Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) - requires Developer ID signing for direct distribution and describes Apple notarization, Gatekeeper tickets, Hardened Runtime, secure timestamps, and the `notarytool`/`stapler` workflow.
- [Customizing the notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow) - documents `xcrun notarytool submit`, log retrieval, the deprecation of `altool`, and stapling to apps, bundles, disk images, and packages. ZIP files cannot be stapled directly; staple the app/bundle before creating the ZIP.
- [Code Signing Tasks](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Procedures/Procedures.html) - documents `codesign --verify --deep --strict --verbose=2` and `spctl --assess --type execute` as pre-release signature and Gatekeeper checks.
- [Packaging Mac software for distribution](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution) - recommends stapling the ticket to the exact distributed product and testing the packaged product on another Mac where possible.

### Windows signing and per-user placement

- [Use SignTool to Verify a File Signature](https://learn.microsoft.com/en-us/windows/win32/seccrypto/using-signtool-to-verify-a-file-signature) - documents `signtool verify /pa`, verbose signer output, and exit codes.
- [Get-AuthenticodeSignature](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature) - documents Windows-side retrieval of signature status and `SignerCertificate` details for a file.
- [Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options) - states that trusted signing/OV certificates are appropriate for public distribution, self-signed certificates are for development or managed enterprise trust, and publisher reputation accumulates over releases.
- [Installation Context](https://learn.microsoft.com/en-us/windows/win32/msi/installation-context) - documents per-user versus per-machine installation and confirms that per-user shortcuts go to the current user's profile. It identifies `FOLDERID_UserProgramFiles` as `%LOCALAPPDATA%\\Programs`.
- [KNOWNFOLDERID](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid) - documents `FOLDERID_UserProgramFiles` as `%LOCALAPPDATA%\\Programs` and `FOLDERID_Programs` as `%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs`.

## Version and environment notes

- GitHub runner labels and images change. Pin an explicit supported arm64 label in release workflows and review the runner reference when upgrading the workflow; do not treat the label list above as permanent.
- PyInstaller stable documentation currently identifies the 6.x series and describes the existing onedir shape. The exact PyInstaller version should be pinned by the build environment before release reproducibility is claimed.
- Apple signing/notarization requires a paid Apple Developer program team, Developer ID signing identity, and notarization credentials. `codesign`, `notarytool`, `stapler`, and `spctl` require macOS tooling; Apple also documents a Notary API for non-macOS submission, but final Gatekeeper validation still needs a real Mac.
- Windows public distribution requires a trusted code-signing identity. Certificate acquisition, private-key custody, and CI integration are release prerequisites. SmartScreen reputation is independent of a local signature-valid result and may warn on early releases.

## Evidence boundary and caveats

The PyInstaller, Apple, GitHub, and Microsoft statements above are documentation evidence. The recommendation to compare the Windows signer against a configured expected publisher, and the updater's replacement/rollback protocol, are implementation guidance derived from the design; the cited vendor docs do not provide that complete updater protocol. Likewise, a CI signature check cannot replace clean-machine installation, update, rollback, Gatekeeper, and Windows reputation tests.

## Reusable takeaway

Retain onedir PyInstaller builds and add a separately frozen updater target; pin explicit arm64 GitHub runner labels; use Developer ID + Hardened Runtime + `notarytool`/stapler on macOS; use a trusted Authenticode publisher check on Windows; and keep both the install directory and Start-menu shortcut in documented per-user locations.
