"""Per-user installation transaction; no mutable application-data access.

The four-argument companion re-fetches trusted metadata and re-verifies bytes.
It must run from a cloned onedir runtime outside the replaceable installation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

from chartcleaner import release_identity
from chartcleaner.update import (
    ReleaseManifest, UpdateClient, UpdateError, UpdateStatus, Version, _archive_expanded_size,
    _signature_targets, current_platform, safe_extract_archive,
    verify_native_signature,
)

HEALTH_PATH_ENV = "CHART_CLEANER_UPDATE_HEALTH"
HEALTH_NONCE_ENV = "CHART_CLEANER_UPDATE_NONCE"
PROCESS_TIMEOUT = 60.0
HEALTH_TIMEOUT = 90.0


def default_install_path() -> Path:
    target = current_platform()
    if target == "macos-arm64":
        return Path.home() / "Applications" / "Chart Cleaner.app"
    local = os.environ.get("LOCALAPPDATA")
    if not local or not Path(local).is_absolute():
        raise UpdateError("invalid_install_path")
    return Path(local) / "Programs" / "Chart Cleaner"


def _no_symlinks(path: Path) -> None:
    for part in (path, *path.parents):
        try:
            attributes = getattr(part.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if part.is_symlink() or attributes & 0x400:  # Windows junction/reparse point.
            raise UpdateError("unsafe_path")


def _install_path(value: str | Path) -> Path:
    path = Path(value).absolute()
    expected = default_install_path().absolute()
    if path != expected:
        raise UpdateError("invalid_install_path")
    _no_symlinks(path)
    if path.exists() and not path.is_dir():
        raise UpdateError("invalid_install_path")
    return path


def staging_root() -> Path:
    return _install_path(default_install_path()).parent / ".chart-cleaner-staging"


def _prepare_staging() -> Path:
    root = staging_root()
    _no_symlinks(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _staged_archive(value: str | Path) -> Path:
    path = Path(value).absolute()
    root = staging_root()
    _no_symlinks(path)
    if root not in path.parents or not path.is_file():
        raise UpdateError("invalid_staged_archive")
    return path


def installation_size(path: Path) -> int:
    """Count executable-tree bytes only, including internal link metadata."""
    path = _install_path(path)
    return max(1, sum(p.lstat().st_size for p in path.rglob("*") if not p.is_dir()))


def _publisher() -> str:
    identity = (release_identity.MACOS_PUBLISHER if current_platform() == "macos-arm64"
                else release_identity.WINDOWS_PUBLISHER)
    if not identity:
        raise UpdateError("missing_trusted_publisher")
    return identity


def release_client() -> UpdateClient:
    return UpdateClient(release_identity.VERSION, release_identity.MANIFEST_URL,
                        trusted_publisher_identity=_publisher())


def _executable(install: Path) -> Path:
    if current_platform() == "macos-arm64":
        return install / "Contents" / "MacOS" / "Chart Cleaner"
    return install / "Chart Cleaner.exe"


def _runtime(install: Path) -> Path:
    return (install / "Contents" / "Helpers" / "updater" if current_platform() == "macos-arm64"
            else install / "updater")


def _companion(runtime: Path) -> Path:
    return runtime / ("chart-cleaner-updater" if current_platform() == "macos-arm64"
                      else "chart-cleaner-updater.exe")


def _payload_version(payload: Path) -> str:
    if current_platform() == "macos-arm64":
        with (payload / "Contents" / "Info.plist").open("rb") as stream:
            return str(Version.parse(plistlib.load(stream)["CFBundleShortVersionString"]))
    script = "[Console]::Out.Write((Get-Item -LiteralPath $env:CC_VERSION_PATH).VersionInfo.ProductVersion)"
    env = dict(os.environ, CC_VERSION_PATH=str(_executable(payload)))
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, check=True, capture_output=True, text=True, timeout=30)
    return str(Version.parse(result.stdout.strip()))


def _verify_payload(root: Path, expected_version: str) -> Path:
    name = "Chart Cleaner.app" if current_platform() == "macos-arm64" else "Chart Cleaner"
    payload = root / name
    if sorted(p.name for p in root.iterdir()) != [name] or not payload.is_dir() or payload.is_symlink():
        raise UpdateError("invalid_payload")
    # Archive links may resolve only inside the replacement app, not its siblings.
    for path in payload.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(payload.resolve()):
            raise UpdateError("unsafe_archive")
    if not _executable(payload).is_file() or not _companion(_runtime(payload)).is_file():
        raise UpdateError("invalid_payload")
    for signed in _signature_targets(root, current_platform()):
        verify_native_signature(signed, _publisher())
    if _payload_version(payload) != expected_version:
        raise UpdateError("payload_version_mismatch")
    return payload


@contextmanager
def _update_lock(install: Path):
    lock = install.parent / ".chart-cleaner-update.lock"
    _no_symlinks(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    stream = os.fdopen(fd, "r+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if not lock.stat().st_size:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise UpdateError("update_in_progress") from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpdateError("update_in_progress") from exc
        acquired = True
        yield
    finally:
        if acquired and os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()  # OS locks release on crash; the inode intentionally remains.


def _wait_for_exit(pid: int, timeout: float = PROCESS_TIMEOUT) -> None:
    if pid == 0:
        return  # Initial install only; checked before entering the transaction.
    if pid <= 0 or pid == os.getpid():
        raise UpdateError("invalid_pid")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, never terminate the old app.
        if not handle:
            if ctypes.get_last_error() == 87:  # PID no longer exists.
                return
            raise UpdateError("process_wait_failed")
        try:
            if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                raise UpdateError("process_exit_timeout")
        finally:
            kernel.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            raise UpdateError("process_wait_failed") from exc
        if time.monotonic() >= deadline:
            raise UpdateError("process_exit_timeout")
        time.sleep(0.1)


def _launch(install: Path, marker: Path | None = None, nonce: str | None = None):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    env.pop(HEALTH_PATH_ENV, None)
    env.pop(HEALTH_NONCE_ENV, None)
    if marker is not None:
        env[HEALTH_PATH_ENV], env[HEALTH_NONCE_ENV] = str(marker), str(nonce)
    return subprocess.Popen([str(_executable(install))], cwd=install.parent, env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True)


def _wait_healthy(process, marker: Path, version: str, nonce: str) -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if not marker.is_symlink() and marker.stat().st_size < 1024:
                if json.loads(marker.read_text()) == {"version": version, "nonce": nonce}:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return False


def write_health_marker(version: str) -> bool:
    """Call only after data initialization AND the local server is accepting requests."""
    raw, nonce = os.environ.get(HEALTH_PATH_ENV), os.environ.get(HEALTH_NONCE_ENV)
    if not raw or not nonce:
        return False
    marker = Path(raw).absolute()
    root = staging_root()
    if (marker.name != "health.json" or marker.parent.parent != root
            or not marker.parent.name.startswith("install-") or len(nonce) != 64
            or any(c not in "0123456789abcdef" for c in nonce)):
        raise UpdateError("invalid_health_context")
    _no_symlinks(marker)
    if not Path(sys.executable).resolve().is_relative_to(default_install_path().resolve()):
        raise UpdateError("invalid_health_context")
    payload = json.dumps({"version": str(Version.parse(version)), "nonce": nonce}).encode()
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return True


def _stop_failed_child(process) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _restore(install: Path, backup: Path, failed: Path) -> None:
    """Never delete the only usable tree, even if the second rename fails."""
    if install.exists():
        install.rename(failed)
    try:
        backup.rename(install)
    except BaseException:
        if failed.exists() and not install.exists():
            failed.rename(install)
        raise


def _copy_verified_archive(source: Path, destination: Path, asset) -> None:
    count, digest = 0, hashlib.sha256()
    with source.open("rb") as inp, destination.open("xb") as out:
        while chunk := inp.read(1024 * 1024):
            count += len(chunk)
            if count > asset.size:
                raise UpdateError("size_mismatch")
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if count != asset.size or digest.hexdigest() != asset.sha256:
        raise UpdateError("checksum_mismatch")


def update_install(pid: int, installed_path: str | Path, staged_archive: str | Path,
                   expected_version: str) -> UpdateStatus:
    """The companion always fetches release metadata independently from its pinned URL."""
    return _install_transaction(pid, installed_path, staged_archive, expected_version)


def _verified_payload(archive: Path, transaction: Path, manifest: ReleaseManifest,
                      version: str, install: Path) -> Path:
    if str(manifest.version) != version:
        raise UpdateError("release_version_changed")
    asset = manifest.platforms[current_platform()]
    if shutil.disk_usage(transaction).free < asset.size + installation_size(install):
        raise UpdateError("insufficient_disk_space")
    verified = transaction / "verified.zip"
    _copy_verified_archive(archive, verified, asset)
    expanded = _archive_expanded_size(verified)
    if shutil.disk_usage(transaction).free < asset.size + expanded + installation_size(install):
        raise UpdateError("insufficient_disk_space")
    extracted = transaction / "extracted"
    safe_extract_archive(verified, extracted)
    return _verify_payload(extracted, version)


def _install_transaction(pid: int, installed_path: str | Path, staged_archive: str | Path,
                         expected_version: str, *, installer_manifest: ReleaseManifest | None = None) -> UpdateStatus:
    """Shared transaction; embedded metadata is only supplied by the signed installer."""
    install = _install_path(installed_path)
    archive = None
    version = str(Version.parse(expected_version))
    if pid < 0 or pid == os.getpid() or (install.exists() and pid == 0):
        raise UpdateError("invalid_pid")
    backup = install.parent / ("." + install.name + ".rollback")
    _no_symlinks(backup)
    root = _prepare_staging()
    # A running Windows onedir updater would pin files inside the tree it replaces.
    if getattr(sys, "frozen", False) and Path(sys.executable).resolve().is_relative_to(install.resolve()):
        raise UpdateError("updater_inside_install")
    with _update_lock(install):
        transaction = Path(tempfile.mkdtemp(prefix="install-", dir=root))
        process = None
        replaced = False
        had_previous = install.exists()
        try:
            if backup.exists():
                # Recovery depends only on the signed local backup, never a new download.
                _wait_for_exit(pid)
                verify_native_signature(backup if current_platform() == "macos-arm64" else _executable(backup), _publisher())
                recovered_version = _payload_version(backup)
                _restore(install, backup, transaction / "interrupted")
                _launch(install)
                return UpdateStatus("rolled_back", "interrupted_update_recovered", version=recovered_version, platform=current_platform())
            installed_version = Version.parse(_payload_version(install)) if install.exists() else None
            if installed_version is not None and not installed_version < Version.parse(version):
                raise UpdateError("downgrade_rejected")
            archive = _staged_archive(staged_archive)
            if installer_manifest is None:
                manifest, status = release_client().check()
                if manifest is None:
                    raise UpdateError(status.code or "manifest_unavailable")
            else:
                if pid != 0 or install.exists() or version != release_identity.VERSION:
                    raise UpdateError("invalid_installer_context")
                manifest = installer_manifest
            if installed_version is not None and installed_version < manifest.minimum_supported_version:
                raise UpdateError("minimum_version_incompatible")
            payload = _verified_payload(archive, transaction, manifest, version, install)
            _wait_for_exit(pid)
            try:
                if had_previous:
                    install.rename(backup)
                payload.rename(install)  # Staging lives beside the install: same-volume rename.
                replaced = True
                nonce = secrets.token_hex(32)
                marker = transaction / "health.json"
                process = _launch(install, marker, nonce)
                if not _wait_healthy(process, marker, version, nonce):
                    raise UpdateError("startup_failed")
                if backup.exists():
                    # Retire recovery eligibility atomically before any destructive cleanup.
                    backup.rename(transaction / "retired-backup")
            except BaseException:
                _stop_failed_child(process)
                if backup.exists():
                    _restore(install, backup, transaction / "failed")
                    _launch(install)  # One relaunch of the prior version, no recursive update.
                    replaced = False
                elif replaced and install.exists():
                    install.rename(transaction / "failed")
                    replaced = False
                raise
            return UpdateStatus("installed" if not had_previous else "updated", version=version, platform=current_platform())
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError("installation_failed") from exc
        finally:
            # Backup is deliberately excluded; cleanup must never remove the recovery copy.
            shutil.rmtree(transaction, ignore_errors=True)
            if archive is not None and archive.parent.parent == root and archive.parent.name.startswith("update-"):
                shutil.rmtree(archive.parent, ignore_errors=True)


def handoff_update(pid: int, install_path: str | Path, staged_archive: str | Path,
                   expected_version: str):
    """Launch the companion from a freshly hash-verified release outside the install."""
    install, archive = _install_path(install_path), _staged_archive(staged_archive)
    version = str(Version.parse(expected_version))
    destination = Path(tempfile.mkdtemp(prefix="companion-", dir=_prepare_staging()))
    try:
        manifest, status = release_client().check()
        if manifest is None:
            raise UpdateError(status.code or "manifest_unavailable")
        payload = _verified_payload(archive, destination, manifest, version, install)
        # The archive hash seals supporting DLL/Python resources too; an EXE signature alone does not.
        runtime = _runtime(payload)
        command = [str(_companion(runtime)), "--pid", str(pid), "--installed-path", str(install),
                   "--staged-archive", str(archive), "--expected-version", expected_version]
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        return subprocess.Popen(command, cwd=destination, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except Exception as exc:
        shutil.rmtree(destination, ignore_errors=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("updater_launch_failed") from exc


def _start_menu_shortcut(install: Path) -> None:
    if current_platform() != "windows-x64":
        return
    roaming = os.environ.get("APPDATA")
    if not roaming or not Path(roaming).is_absolute():
        raise UpdateError("shortcut_failed")
    folder = Path(roaming) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    _no_symlinks(folder)
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / "Chart Cleaner.lnk"
    _no_symlinks(link)
    script = ("$w=New-Object -ComObject WScript.Shell; "
              "$s=$w.CreateShortcut($env:CC_SHORTCUT); "
              "$s.TargetPath=$env:CC_EXECUTABLE; $s.WorkingDirectory=$env:CC_DIRECTORY; $s.Save()")
    env = dict(os.environ, CC_SHORTCUT=str(link), CC_EXECUTABLE=str(_executable(install)), CC_DIRECTORY=str(install))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=env, check=True, capture_output=True, timeout=30)


def _embedded_installer() -> tuple[Path, ReleaseManifest]:
    """Only the signed installer may trust its own embedded manifest."""
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        raise UpdateError("signed_installer_required")
    signer = Path(sys.executable).resolve()
    if current_platform() == "macos-arm64":
        bundles = [parent for parent in signer.parents if parent.suffix == ".app"]
        if not bundles:
            raise UpdateError("signed_installer_required")
        signer = bundles[0]
    verify_native_signature(signer, _publisher())
    resources = Path(sys._MEIPASS).resolve()
    manifest_file = (resources / "installer-manifest.json").resolve()
    payload = (resources / "installer-payload.zip").resolve()
    if current_platform() == "macos-arm64":
        if not all(path.is_relative_to(signer) for path in (resources, manifest_file, payload)):
            raise UpdateError("unsealed_installer_resources")
    elif (not resources.name.startswith("_MEI")
          or not resources.is_relative_to(Path(tempfile.gettempdir()).resolve())
          or signer.is_relative_to(resources)
          or not all(path.is_relative_to(resources) for path in (manifest_file, payload))):
        # Windows must be a signed onefile EXE, never a signed EXE with mutable sidecars.
        raise UpdateError("unsealed_installer_resources")
    _no_symlinks(manifest_file)
    _no_symlinks(payload)
    if manifest_file.stat().st_size > 1024 * 1024:
        raise UpdateError("malformed_manifest")
    manifest = ReleaseManifest.parse(manifest_file.read_bytes())
    if str(manifest.version) != release_identity.VERSION or current_platform() not in manifest.platforms:
        raise UpdateError("invalid_installer_context")
    return payload, manifest


def install_latest() -> UpdateStatus:
    """Install this signed setup bundle's exact embedded release, without network access."""
    install = _install_path(default_install_path())
    if install.exists():
        raise UpdateError("already_installed_use_app_update")
    payload, manifest = _embedded_installer()
    stage = Path(tempfile.mkdtemp(prefix="update-installer-", dir=_prepare_staging()))
    try:
        archive = stage / "release.zip"
        asset = manifest.platforms[current_platform()]
        if shutil.disk_usage(stage).free < asset.size:
            raise UpdateError("insufficient_disk_space")
        _copy_verified_archive(payload, archive, asset)
        result = _install_transaction(0, install, archive, str(manifest.version), installer_manifest=manifest)
        _start_menu_shortcut(install)
        return result
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chart Cleaner verified update companion")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--installed-path", required=True)
    parser.add_argument("--staged-archive", required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args(argv)
    try:
        result = update_install(args.pid, args.installed_path, args.staged_archive, args.expected_version)
    except Exception as exc:
        result = UpdateStatus("error", exc.code if isinstance(exc, UpdateError) else "installation_failed")
    print(json.dumps(result.as_dict()))
    return 0 if result.state == "updated" else 1


def installer_main() -> int:
    try:
        result = install_latest()
    except Exception as exc:
        result = UpdateStatus("error", exc.code if isinstance(exc, UpdateError) else "installation_failed")
    print(json.dumps(result.as_dict()))
    return 0 if result.state == "installed" else 1
