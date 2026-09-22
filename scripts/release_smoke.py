"""Release-only native smoke checks in disposable per-user directories.

The frozen installer runs unmodified. Source updater transactions inject only
release discovery (draft assets are private) and a failed-launch argument;
native signatures, archive validation, replacement, health and rollback run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chartcleaner import __version__ as VERSION
from chartcleaner import release_identity, updater
from chartcleaner.update import ReleaseManifest, UpdateError, UpdateStatus, Version, safe_extract_archive
from scripts.build_release import manifest_for


def wait_ui(version: str, timeout: int = 120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/settings', timeout=2) as response:
                body = response.read().decode()
            if version in body and 'Chart Cleaner' in body:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError('Installed UI did not report expected version')


def stop_installed(install: Path):
    executable = str(updater._executable(install))
    if sys.platform == 'darwin':
        rows = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True).splitlines()
        pids = [int(row.strip().split(None, 1)[0]) for row in rows
                if len(row.strip().split(None, 1)) == 2 and
                row.strip().split(None, 1)[1].startswith(executable)]
    else:
        env = dict(os.environ, CC_SMOKE_EXECUTABLE=executable)
        command = 'Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $env:CC_SMOKE_EXECUTABLE} | ForEach-Object {$_.ProcessId}'
        pids = [int(v) for v in subprocess.check_output(
            ['powershell', '-NoProfile', '-Command', command], env=env, text=True).split()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 8765)) != 0:
                return
        time.sleep(0.2)
    raise RuntimeError('Installed application did not stop')


def snapshot(directory: Path):
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob('*') if p.is_file()}


def smoke(output: Path, previous: Path, previous_version: str, report: Path, minimum: str, provenance: str):
    prior = Version.parse(previous_version)
    if prior < Version.parse(minimum) or not prior < Version.parse(VERSION):
        raise RuntimeError('Previous release fixture must be older than the build')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 8765)) == 0:
            raise RuntimeError('Smoke port occupied; refusing to interact with another app')
    target = updater.current_platform()
    archive = output / f'ChartCleaner-{VERSION}-{target}.zip'
    installer = (output / 'Install Chart Cleaner.app/Contents/MacOS/Install Chart Cleaner'
                 if target == 'macos-arm64' else output / 'ChartCleanerSetup.exe')
    with tempfile.TemporaryDirectory(prefix='chart-cleaner-release-') as temp:
        home = Path(temp).resolve()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   LOCALAPPDATA=str(home / 'Local'), APPDATA=str(home / 'Roaming'), BROWSER='true')
        with patch.dict(os.environ, env):
            install = updater.default_install_path()
            try:
                # Real signed installer, compiled pins and embedded signed payload.
                subprocess.run([str(installer)], env=env, check=True, timeout=180)
                wait_ui(VERSION)
                stop_installed(install)
                shutil.rmtree(install)
                previous_root = home / 'previous'
                safe_extract_archive(previous, previous_root)
                old_payload = updater._verify_payload(previous_root, previous_version)
                shutil.copytree(old_payload, install, symlinks=True)
                data = (home / 'Library/Application Support/Chart Cleaner' if target == 'macos-arm64'
                        else home / 'Local/Chart Cleaner')
                data.mkdir(parents=True, exist_ok=True)
                # Synthetic sentinels only; the harness never reads real user data.
                config = json.loads((ROOT / 'chartcleaner/default_config.json').read_text())
                config['release_sentinel'] = 'settings-survive'
                (data / 'config.json').write_text(json.dumps(config))
                for name in ('data/history-sentinel.json', 'data/token-map-sentinel.json',
                             'custom_rules/release_sentinel.py', 'presets/release-sentinel.json'):
                    path = data / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('"synthetic-release-sentinel"')
                sentinels = {name: content for name, content in snapshot(data).items()
                             if 'sentinel' in name or name == 'config.json'}
                staged = updater._prepare_staging() / 'candidate.zip'
                shutil.copyfile(archive, staged)
                manifest = ReleaseManifest.parse(manifest_for({target: archive}, VERSION, minimum))

                class DraftClient:
                    def check(self):
                        return manifest, UpdateStatus('update_available', version=VERSION)

                original_launch = updater._launch
                children = []

                def launch(path, marker=None, nonce=None, *, broken=False):
                    if broken and marker is not None:
                        process = subprocess.Popen([str(updater._executable(path)), '--invalid-release-smoke-option'],
                                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        process = original_launch(path, marker, nonce)
                    children.append(process)
                    return process

                # No shipped runtime trust override: patches are confined to this CI harness.
                with patch.object(updater, 'release_client', return_value=DraftClient()), \
                     patch.object(release_identity, 'VERSION', previous_version), \
                     patch.object(updater, '_launch', side_effect=launch):
                    exited = subprocess.Popen([sys.executable, '-c', 'pass'])
                    exited.wait()
                    result = updater.update_install(exited.pid, install, staged, VERSION)
                    assert result.state == 'updated'
                    wait_ui(VERSION)
                    stop_installed(install)
                    assert all((data / name).read_bytes() == value for name, value in sentinels.items())
                    shutil.rmtree(install)
                    shutil.copytree(old_payload, install, symlinks=True)
                    with patch.object(updater, '_launch', side_effect=lambda *a, **k: launch(*a, **k, broken=True)):
                        try:
                            updater.update_install(exited.pid, install, staged, VERSION)
                        except UpdateError as exc:
                            assert exc.code == 'startup_failed', exc.code
                        else:
                            raise AssertionError('Broken startup was accepted')
                    wait_ui(previous_version)
                    stop_installed(install)
                    assert updater._payload_version(install) == previous_version
                    assert all((data / name).read_bytes() == value for name, value in sentinels.items())
                    assert not (install.parent / ('.' + install.name + '.rollback')).exists()
                report.write_text(json.dumps({'platform': target, 'version': VERSION,
                    'previous_version': previous_version, 'minimum_supported_version': minimum,
                    'previous_provenance': provenance, 'frozen_installer_ui': 'PASS',
                    'signed_upgrade_sentinels': 'PASS', 'broken_startup_rollback': 'PASS',
                    'production_network_upgrade': 'NOT_RUN', 'clean_machine_presentation': 'NOT_RUN'}, indent=2))
            finally:
                stop_installed(install)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous-archive', type=Path, required=True)
    parser.add_argument('--previous-version', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--minimum-version', required=True)
    parser.add_argument('--provenance', choices=('published_signed_release', 'synthetic_same_source_previous_version'), required=True)
    args = parser.parse_args()
    smoke(args.output.resolve(), args.previous_archive.resolve(), args.previous_version, args.report.resolve(), args.minimum_version, args.provenance)
