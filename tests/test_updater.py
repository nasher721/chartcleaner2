"""Disposable installs only; native trust checks are mocked, never disabled in CLI."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from chartcleaner import updater
from chartcleaner.update import ReleaseAsset, ReleaseManifest, UpdateError, UpdateStatus, Version


@pytest.fixture
def installation(tmp_path, monkeypatch):
    install = tmp_path / "Applications" / "Chart Cleaner.app"
    install.mkdir(parents=True)
    (install / "old-code").write_bytes(b"old executable")
    (install / "Contents").mkdir()
    (install / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "2.3.0"}))
    data = tmp_path / "Library" / "Application Support" / "Chart Cleaner"
    data.mkdir(parents=True)
    sentinels = {"config.json": b'{"private":"unchanged"}', "history.json": b"history",
                 "token-maps.json": b"private map", "custom_rules.py": b"private rules"}
    for name, value in sentinels.items():
        (data / name).write_bytes(value)
    monkeypatch.setattr(updater, "current_platform", lambda: "macos-arm64")
    monkeypatch.setattr(updater, "default_install_path", lambda: install)
    monkeypatch.setattr(updater.release_identity, "MACOS_PUBLISHER", "Fixture Publisher")
    monkeypatch.setattr(updater.release_identity, "VERSION", "2.3.0")
    monkeypatch.setattr(updater, "verify_native_signature", lambda *args: None)
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid: None)
    monkeypatch.setattr(updater, "HEALTH_TIMEOUT", 0.5)
    signatures = []
    monkeypatch.setattr(updater, "verify_native_signature", lambda p, identity: signatures.append((p, identity)))
    archive_dir = updater._prepare_staging() / "update-fixture"
    archive_dir.mkdir()
    archive = archive_dir / "release.zip"
    entries = {
        "Chart Cleaner.app/Contents/Info.plist": plistlib.dumps({"CFBundleShortVersionString": "2.4.0"}),
        "Chart Cleaner.app/Contents/MacOS/Chart Cleaner": b"new executable",
        "Chart Cleaner.app/Contents/Helpers/updater/chart-cleaner-updater": b"companion",
    }
    def package(values=entries, version="2.4.0"):
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as zf:
            for key, value in values.items():
                zf.writestr(key, value)
        asset = ReleaseAsset("https://github.com/nasher721/chartcleaner2/releases/download/v2.4.0/app.zip",
                             hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size)
        manifest = ReleaseManifest(Version.parse(version), Version.parse("2.0.0"),
                                   "https://github.com/nasher721/chartcleaner2/releases/tag/v2.4.0",
                                   {"macos-arm64": asset})
        monkeypatch.setattr(updater, "release_client", lambda: SimpleNamespace(check=lambda: (manifest, UpdateStatus("update_available"))))
        return manifest
    package()
    launched = []
    class FakeProcess:
        def __init__(self):
            self.stopped = False
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, **kwargs):
            return 0
    def launch(target, marker=None, nonce=None):
        launched.append((target, marker, nonce))
        if marker:
            marker.write_text(json.dumps({"version": "2.4.0", "nonce": nonce}))
        return FakeProcess()
    monkeypatch.setattr(updater, "_launch", launch)
    state = SimpleNamespace(install=install, archive=archive, data=data, sentinels=sentinels,
                            launched=launched, package=package, entries=entries, signatures=signatures,
                            process=FakeProcess)
    yield state
    assert {p.name: p.read_bytes() for p in data.iterdir()} == sentinels


def run(state):
    return updater.update_install(123456, state.install, state.archive, "2.4.0")


def test_success_requires_signature_and_fresh_version_nonce(installation):
    state = installation
    result = run(state)
    assert result.state == "updated"
    assert updater._executable(state.install).read_bytes() == b"new executable"
    assert len(state.signatures) == 1
    assert len(state.launched) == 1
    assert len(state.launched[0][2]) == 64
    assert not (state.install.parent / ".Chart Cleaner.app.rollback").exists()
    assert not list(updater.staging_root().glob("install-*"))


@pytest.mark.parametrize("change,code", [("truncate", "checksum_mismatch"), ("tamper", "checksum_mismatch")])
def test_bad_archive_leaves_previous_app(installation, change, code):
    archive = installation.archive
    data = archive.read_bytes()
    archive.write_bytes(data[:-1] if change == "truncate" else bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(UpdateError, match=code):
        run(installation)
    assert (installation.install / "old-code").read_bytes() == b"old executable"
    assert not installation.launched


def test_failed_native_verification_preserves_old(installation, monkeypatch):
    def reject(*args):
        raise UpdateError("signature_invalid")
    monkeypatch.setattr(updater, "verify_native_signature", reject)
    with pytest.raises(UpdateError, match="signature_invalid"):
        run(installation)
    assert (installation.install / "old-code").exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "Chart Cleaner.app/../../../escape"])
def test_archive_traversal_never_replaces(installation, name):
    installation.package(dict(installation.entries, **{name: b"bad"}))
    with pytest.raises(UpdateError):
        run(installation)
    assert (installation.install / "old-code").exists()


def test_not_enough_disk_preserves_install(installation, monkeypatch):
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda path: SimpleNamespace(free=1))
    with pytest.raises(UpdateError, match="insufficient_disk_space"):
        run(installation)
    assert (installation.install / "old-code").exists()


def test_expansion_space_checked_independently(installation, monkeypatch):
    calls = []
    def space(path):
        calls.append(path)
        return SimpleNamespace(free=10**10 if len(calls) == 1 else 1)
    monkeypatch.setattr(updater.shutil, "disk_usage", space)
    with pytest.raises(UpdateError, match="insufficient_disk_space"):
        run(installation)
    assert len(calls) == 2
    assert (installation.install / "old-code").exists()


def test_lock_excludes_second_update(installation):
    with updater._update_lock(installation.install):
        with pytest.raises(UpdateError, match="update_in_progress"):
            run(installation)
    assert (installation.install / "old-code").exists()


def test_process_timeout_does_not_move_old_install(installation, monkeypatch):
    def timeout(pid):
        raise UpdateError("process_exit_timeout")
    monkeypatch.setattr(updater, "_wait_for_exit", timeout)
    with pytest.raises(UpdateError, match="process_exit_timeout"):
        run(installation)
    assert (installation.install / "old-code").exists()


@pytest.mark.parametrize("reason", ["missing", "stale_version", "stale_nonce", "exited", "launch_failed"])
def test_startup_failure_rolls_back_once(installation, monkeypatch, reason):
    calls, processes = [], []
    def launch(target, marker=None, nonce=None):
        calls.append(marker)
        process = installation.process()
        processes.append(process)
        if marker and reason == "launch_failed":
            raise OSError("private executable path")
        if marker and reason == "stale_version":
            marker.write_text(json.dumps({"version": "2.3.0", "nonce": nonce}))
        if marker and reason == "stale_nonce":
            marker.write_text(json.dumps({"version": "2.4.0", "nonce": "0" * 64}))
        if marker and reason == "exited":
            process.stopped = True
        return process
    monkeypatch.setattr(updater, "_launch", launch)
    with pytest.raises(UpdateError):
        run(installation)
    assert (installation.install / "old-code").read_bytes() == b"old executable"
    assert len(calls) == 2 and calls[1] is None
    assert not (installation.install.parent / ".Chart Cleaner.app.rollback").exists()
    if reason != "launch_failed":
        assert processes[0].stopped


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_locked_or_interrupted_replacement_restores_old(installation, monkeypatch, failure):
    original = Path.rename
    def fail_new(self, destination):
        if self.name == "Chart Cleaner.app" and self.parent.name == "extracted":
            raise failure("simulated locked file or interruption")
        return original(self, destination)
    monkeypatch.setattr(Path, "rename", fail_new)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else UpdateError):
        run(installation)
    assert (installation.install / "old-code").exists()
    assert len(installation.launched) == 1


def test_locked_old_tree_does_not_modify_it(installation, monkeypatch):
    original = Path.rename
    def fail_old(self, destination):
        if self == installation.install:
            raise PermissionError("Windows lock")
        return original(self, destination)
    monkeypatch.setattr(Path, "rename", fail_old)
    with pytest.raises(UpdateError, match="installation_failed"):
        run(installation)
    assert (installation.install / "old-code").exists()
    assert not installation.launched


def test_recovers_backup_from_interrupted_companion(installation):
    backup = installation.install.parent / ".Chart Cleaner.app.rollback"
    installation.install.rename(backup)
    result = run(installation)
    assert result.state == "rolled_back" and result.code == "interrupted_update_recovered"
    assert (installation.install / "old-code").exists()
    assert len(installation.launched) == 1


def test_interrupted_new_install_does_not_block_recovery(installation, monkeypatch):
    state = installation
    state.install.rename(state.install.parent / ".Chart Cleaner.app.rollback")
    state.install.mkdir()  # New install is incomplete, so its version is unreadable.
    monkeypatch.setattr(updater.release_identity, "VERSION", "2.4.0")
    result = run(state)
    assert result.state == "rolled_back" and result.version == "2.3.0"
    assert (state.install / "old-code").exists()


@pytest.mark.parametrize("archive_state", ["missing", "corrupt"])
def test_interrupted_rename_recovers_without_network_or_new_archive(installation, monkeypatch, archive_state):
    state = installation
    state.install.rename(state.install.parent / ".Chart Cleaner.app.rollback")
    if archive_state == "missing":
        state.archive.unlink()
    else:
        state.archive.write_bytes(b"invalid new archive")
    def offline_check():
        pytest.fail("recovery must not fetch release metadata")
    monkeypatch.setattr(updater, "release_client", offline_check)
    events = []
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid: events.append(("wait", pid)))
    monkeypatch.setattr(updater, "verify_native_signature", lambda path, identity: events.append(("verify", path.name)))
    result = run(state)
    assert result.state == "rolled_back" and result.version == "2.3.0"
    assert result.code == "interrupted_update_recovered"
    assert [stage for stage, value in events] == ["wait", "verify"]
    assert len(state.launched) == 1 and state.launched[0][1] is None
    assert (state.install / "old-code").read_bytes() == b"old executable"
    if archive_state == "corrupt":
        assert state.archive.read_bytes() == b"invalid new archive"


def test_recovery_ignores_untrusted_archive_argument_without_touching_it(installation, monkeypatch):
    state = installation
    state.install.rename(state.install.parent / ".Chart Cleaner.app.rollback")
    original = (state.data / "config.json").read_bytes()
    monkeypatch.setattr(updater, "release_client", lambda: pytest.fail("offline recovery"))
    result = updater.update_install(123456, state.install, state.data / "config.json", "2.4.0")
    assert result.state == "rolled_back"
    assert (state.data / "config.json").read_bytes() == original
    assert len(state.launched) == 1


def test_offline_recovery_rejects_unverified_backup(installation, monkeypatch):
    state = installation
    backup = state.install.parent / ".Chart Cleaner.app.rollback"
    state.install.rename(backup)
    state.archive.write_bytes(b"invalid new archive")
    monkeypatch.setattr(updater, "release_client", lambda: pytest.fail("offline recovery"))
    def reject(*args):
        raise UpdateError("signature_invalid")
    monkeypatch.setattr(updater, "verify_native_signature", reject)
    with pytest.raises(UpdateError, match="signature_invalid"):
        run(state)
    assert (backup / "old-code").read_bytes() == b"old executable"
    assert not state.install.exists() and not state.launched
    assert state.archive.read_bytes() == b"invalid new archive"


def test_version_mismatch_rejected_before_shutdown(installation):
    entries = dict(installation.entries)
    entries["Chart Cleaner.app/Contents/Info.plist"] = plistlib.dumps({"CFBundleShortVersionString": "2.3.0"})
    installation.package(entries)
    with pytest.raises(UpdateError, match="payload_version_mismatch"):
        run(installation)
    assert (installation.install / "old-code").exists()


def test_data_path_and_external_archive_never_opened(installation):
    with pytest.raises(UpdateError, match="invalid_install_path"):
        updater.update_install(123, installation.data, installation.archive, "2.4.0")
    with pytest.raises(UpdateError, match="invalid_staged_archive"):
        updater.update_install(123, installation.install, installation.data / "config.json", "2.4.0")


def test_symlink_install_rejected(installation):
    shutil.rmtree(installation.install)
    installation.install.symlink_to(installation.data, target_is_directory=True)
    with pytest.raises(UpdateError, match="unsafe_path"):
        run(installation)


def test_missing_identity_fails_closed(monkeypatch):
    monkeypatch.setattr(updater, "current_platform", lambda: "windows-x64")
    monkeypatch.setattr(updater.release_identity, "WINDOWS_PUBLISHER", "")
    with pytest.raises(UpdateError, match="missing_trusted_publisher"):
        updater.release_client()


def test_health_writer_validates_install_and_staging_boundary(installation, monkeypatch):
    transaction = updater.staging_root() / "install-test"
    transaction.mkdir()
    marker = transaction / "health.json"
    monkeypatch.setattr(sys, "executable", str(updater._executable(installation.install)))
    monkeypatch.setenv(updater.HEALTH_PATH_ENV, str(marker))
    monkeypatch.setenv(updater.HEALTH_NONCE_ENV, "a" * 64)
    assert updater.write_health_marker("2.4.0")
    assert json.loads(marker.read_text()) == {"version": "2.4.0", "nonce": "a" * 64}
    monkeypatch.setenv(updater.HEALTH_PATH_ENV, str(installation.data / "config.json"))
    with pytest.raises(UpdateError, match="invalid_health_context"):
        updater.write_health_marker("2.4.0")


def test_health_writer_without_update_environment(monkeypatch):
    monkeypatch.delenv(updater.HEALTH_PATH_ENV, raising=False)
    monkeypatch.delenv(updater.HEALTH_NONCE_ENV, raising=False)
    assert updater.write_health_marker("2.4.0") is False


def test_companion_handoff_uses_verified_archive_not_installed_runtime(installation, monkeypatch):
    runtime = updater._runtime(installation.install)
    (runtime / "_internal").mkdir(parents=True)
    updater._companion(runtime).write_bytes(b"signed updater")
    (runtime / "_internal" / "python-runtime").write_bytes(b"tampered installed runtime")
    installation.package(dict(installation.entries, **{
        "Chart Cleaner.app/Contents/Helpers/updater/_internal/python-runtime": b"verified runtime"}))
    captured = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kwargs: captured.append((cmd, kwargs)) or installation.process())
    updater.handoff_update(123, installation.install, installation.archive, "2.4.0")
    command, kwargs = captured[0]
    copied = Path(command[0])
    assert not copied.is_relative_to(installation.install)
    assert copied.is_relative_to(updater.staging_root())
    assert (copied.parent / "_internal" / "python-runtime").read_bytes() == b"verified runtime"
    assert command[1::2] == ["--pid", "--installed-path", "--staged-archive", "--expected-version"]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_install_flow_reuses_transaction_without_old_app(installation):
    shutil.rmtree(installation.install)
    result = updater.update_install(0, installation.install, installation.archive, "2.4.0")
    assert result.state == "installed"
    assert updater._executable(installation.install).exists()


def test_installer_refuses_to_overwrite_existing_app(installation):
    with pytest.raises(UpdateError, match="already_installed_use_app_update"):
        updater.install_latest()


def test_companion_cli_has_no_trust_override(installation):
    with pytest.raises(SystemExit):
        updater.main(["--publisher", "attacker"])


def test_generic_failure_output_omits_paths(installation, monkeypatch, capsys):
    def fail(*args):
        raise OSError("PRIVATE chart config path")
    monkeypatch.setattr(updater, "update_install", fail)
    assert updater.main(["--pid", "123", "--installed-path", str(installation.install),
                         "--staged-archive", str(installation.archive), "--expected-version", "2.4.0"]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and str(installation.install) not in output
    assert json.loads(output)["code"] == "installation_failed"


def test_real_child_exit_wait_and_health_marker(tmp_path):
    marker = tmp_path / "health.json"
    process = subprocess.Popen([sys.executable, "-c", "import sys,json,time; open(sys.argv[1], 'w').write(json.dumps({'version':'2.4.0','nonce':'test'})); time.sleep(5)", str(marker)])
    try:
        assert updater._wait_healthy(process, marker, "2.4.0", "test")
    finally:
        updater._stop_failed_child(process)
    updater._wait_for_exit(process.pid, timeout=0.5)


def test_windows_payload_transaction_and_shortcut(installation, monkeypatch):
    state = installation
    monkeypatch.setattr(updater, "current_platform", lambda: "windows-x64")
    monkeypatch.setattr(updater.release_identity, "WINDOWS_PUBLISHER", "Fixture Windows Publisher")
    state.install.rename(state.install.with_suffix(""))
    state.install = state.install.with_suffix("")
    monkeypatch.setattr(updater, "default_install_path", lambda: state.install)
    with zipfile.ZipFile(state.archive, "w") as zf:
        zf.writestr("Chart Cleaner/Chart Cleaner.exe", b"new executable")
        zf.writestr("Chart Cleaner/updater/chart-cleaner-updater.exe", b"updater")
        zf.writestr("Chart Cleaner/updater/_internal/python.dll", b"python runtime")
    asset = ReleaseAsset("https://github.com/nasher721/chartcleaner2/releases/download/v2.4.0/win.zip",
                         hashlib.sha256(state.archive.read_bytes()).hexdigest(), state.archive.stat().st_size)
    manifest = ReleaseManifest(Version.parse("2.4.0"), Version.parse("2.0.0"),
                               "https://github.com/nasher721/chartcleaner2/releases/tag/v2.4.0", {"windows-x64": asset})
    monkeypatch.setattr(updater, "release_client", lambda: SimpleNamespace(check=lambda: (manifest, UpdateStatus("update_available"))))
    monkeypatch.setattr(updater, "_payload_version", lambda path: "2.3.0" if (path / "old-code").exists() else "2.4.0")
    result = run(state)
    assert result.state == "updated" and len(state.signatures) == 2
    assert updater._executable(state.install).read_bytes() == b"new executable"
    monkeypatch.setenv("APPDATA", str(state.install.parent.parent / "Roaming"))
    calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    updater._start_menu_shortcut(state.install)
    assert calls[0][1]["env"]["CC_EXECUTABLE"] == str(state.install / "Chart Cleaner.exe")
    assert calls[0][1]["env"]["CC_SHORTCUT"].endswith("Start Menu/Programs/Chart Cleaner.lnk")


def test_embedded_signed_installer_installs_without_network(installation, monkeypatch):
    state = installation
    manifest = state.package()
    shutil.rmtree(state.install)
    installer = state.install.parent / "Install Chart Cleaner.app"
    resources = installer / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "installer-payload.zip").write_bytes(state.archive.read_bytes())
    asset = manifest.platforms["macos-arm64"]
    raw = {"version": "2.4.0", "minimum_supported_version": "2.0.0", "notes_url": manifest.notes_url,
           "platforms": {"macos-arm64": {"url": asset.url, "size": asset.size, "sha256": asset.sha256}}}
    (resources / "installer-manifest.json").write_text(json.dumps(raw))
    monkeypatch.setattr(updater.release_identity, "VERSION", "2.4.0")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(resources), raising=False)
    monkeypatch.setattr(sys, "executable", str(installer / "Contents" / "MacOS" / "Install Chart Cleaner"))
    monkeypatch.setattr(updater, "release_client", lambda: pytest.fail("embedded install must not fetch GitHub"))
    result = updater.install_latest()
    assert result.state == "installed"
    assert state.signatures[0][0] == installer
    assert updater._executable(state.install).read_bytes() == b"new executable"


def test_windows_installer_rejects_unsigned_adjacent_sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "current_platform", lambda: "windows-x64")
    monkeypatch.setattr(updater.release_identity, "WINDOWS_PUBLISHER", "Fixture Publisher")
    monkeypatch.setattr(updater, "verify_native_signature", lambda *args: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "ChartCleanerSetup.exe"))
    with pytest.raises(UpdateError, match="unsealed_installer_resources"):
        updater._embedded_installer()


def test_source_installer_refuses_unsealed_payload(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    with pytest.raises(UpdateError, match="signed_installer_required"):
        updater._embedded_installer()


def test_partial_retired_backup_cleanup_never_recovers_over_healthy_app(installation, monkeypatch):
    state = installation
    (state.install / "python.dll").write_bytes(b"required old runtime")
    original = shutil.rmtree
    leftovers = []
    def partial_cleanup(path, *args, **kwargs):
        retired = Path(path) / "retired-backup"
        if retired.exists():
            # Windows-style partial recursive cleanup followed by a locked file.
            (retired / "python.dll").unlink(missing_ok=True)
            leftovers.append(retired)
            if not kwargs.get("ignore_errors"):
                raise PermissionError("locked retired file")
            return
        return original(path, *args, **kwargs)
    monkeypatch.setattr(updater.shutil, "rmtree", partial_cleanup)
    assert run(state).state == "updated"
    assert leftovers and leftovers[0].exists()
    assert not (state.install.parent / ".Chart Cleaner.app.rollback").exists()
    monkeypatch.setattr(updater.shutil, "rmtree", original)
    entries = dict(state.entries)
    entries["Chart Cleaner.app/Contents/Info.plist"] = plistlib.dumps({"CFBundleShortVersionString": "2.5.0"})
    state.package(entries, version="2.5.0")
    def launch(target, marker=None, nonce=None):
        assert marker is not None  # No recovery launch of the incomplete retired tree.
        marker.write_text(json.dumps({"version": "2.5.0", "nonce": nonce}))
        return state.process()
    monkeypatch.setattr(updater, "_launch", launch)
    result = updater.update_install(123456, state.install, state.archive, "2.5.0")
    assert result.state == "updated"
    assert updater._payload_version(state.install) == "2.5.0"


def test_target_version_companion_accepts_older_install(installation, monkeypatch):
    monkeypatch.setattr(updater.release_identity, "VERSION", "2.4.0")
    assert run(installation).state == "updated"


def test_backup_retirement_failure_rolls_back_before_cleanup(installation, monkeypatch):
    original = Path.rename
    def fail_retirement(path, target):
        if Path(target).name == "retired-backup":
            raise PermissionError("locked backup")
        return original(path, target)
    monkeypatch.setattr(Path, "rename", fail_retirement)
    with pytest.raises(UpdateError, match="installation_failed"):
        run(installation)
    assert (installation.install / "old-code").exists()
    assert len(installation.launched) == 2


def test_actual_installed_version_prevents_downgrade(installation, monkeypatch):
    (installation.install / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "2.5.0"}))
    monkeypatch.setattr(updater.release_identity, "VERSION", "2.3.0")
    with pytest.raises(UpdateError, match="downgrade_rejected"):
        run(installation)
    assert not installation.launched


def test_target_companion_enforces_minimum_against_installed_version(installation, monkeypatch):
    manifest = installation.package()
    manifest = ReleaseManifest(manifest.version, Version.parse("2.3.1"), manifest.notes_url, manifest.platforms)
    monkeypatch.setattr(updater.release_identity, "VERSION", "2.4.0")
    monkeypatch.setattr(updater, "release_client", lambda: SimpleNamespace(check=lambda: (manifest, UpdateStatus("current"))))
    with pytest.raises(UpdateError, match="minimum_version_incompatible"):
        run(installation)
    assert (installation.install / "old-code").exists()


@pytest.mark.parametrize("failure", ["hash", "signature", "manifest"])
def test_handoff_rejects_unverified_release_before_spawning(installation, monkeypatch, failure):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    if failure == "hash":
        installation.archive.write_bytes(b"tampered archive")
    elif failure == "signature":
        def reject(*args):
            raise UpdateError("signature_invalid")
        monkeypatch.setattr(updater, "verify_native_signature", reject)
    else:
        monkeypatch.setattr(updater, "release_client", lambda: SimpleNamespace(
            check=lambda: (None, UpdateStatus("error", "network_error"))))
    with pytest.raises(UpdateError):
        updater.handoff_update(123, installation.install, installation.archive, "2.4.0")
    assert not calls
    assert not list(updater.staging_root().glob("companion-*"))
    assert (installation.install / "old-code").exists()


def test_windows_handoff_seals_supporting_dll_by_release_hash(installation, monkeypatch):
    state = installation
    monkeypatch.setattr(updater, "current_platform", lambda: "windows-x64")
    monkeypatch.setattr(updater.release_identity, "WINDOWS_PUBLISHER", "Fixture Publisher")
    runtime = updater._runtime(state.install)
    (runtime / "_internal").mkdir(parents=True)
    updater._companion(runtime).write_bytes(b"signed old companion")
    (runtime / "_internal" / "python.dll").write_bytes(b"tampered DLL")
    with zipfile.ZipFile(state.archive, "w") as zf:
        zf.writestr("Chart Cleaner/Chart Cleaner.exe", b"new executable")
        zf.writestr("Chart Cleaner/updater/chart-cleaner-updater.exe", b"signed release companion")
        zf.writestr("Chart Cleaner/updater/_internal/python.dll", b"verified DLL")
    asset = ReleaseAsset("https://github.com/nasher721/chartcleaner2/releases/download/v2.4.0/win.zip",
                         hashlib.sha256(state.archive.read_bytes()).hexdigest(), state.archive.stat().st_size)
    manifest = ReleaseManifest(Version.parse("2.4.0"), Version.parse("2.0.0"),
                               "https://github.com/nasher721/chartcleaner2/releases/tag/v2.4.0", {"windows-x64": asset})
    monkeypatch.setattr(updater, "release_client", lambda: SimpleNamespace(check=lambda: (manifest, UpdateStatus("update_available"))))
    monkeypatch.setattr(updater, "_payload_version", lambda path: "2.4.0")
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kwargs: calls.append(cmd) or state.process())
    updater.handoff_update(123, state.install, state.archive, "2.4.0")
    companion = Path(calls[0][0])
    assert companion.read_bytes() == b"signed release companion"
    assert (companion.parent / "_internal" / "python.dll").read_bytes() == b"verified DLL"
    assert len(state.signatures) == 2


def test_missing_marker_real_process_terminated_before_rollback(installation, monkeypatch):
    processes = []
    def launch(install, marker=None, nonce=None):
        if marker is None:
            assert processes[0].poll() is not None
            return installation.process()
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        processes.append(p)
        return p
    monkeypatch.setattr(updater, "_launch", launch)
    try:
        with pytest.raises(UpdateError, match="startup_failed"):
            run(installation)
        assert (installation.install / "old-code").exists()
        assert processes[0].poll() is not None
    finally:
        for process in processes:
            updater._stop_failed_child(process)
