"""End-to-end source transactions with nested user data and real child health.

Native signatures are injected fixtures; signed OS acceptance remains a release
runner gate. No fixture can alter the shipped CLI's trust checks.
"""
import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from chartcleaner import updater
from chartcleaner.paths import user_data_dir
from chartcleaner.update import ReleaseAsset, ReleaseManifest, UpdateError, UpdateStatus, Version


@pytest.mark.parametrize('outcome', ['updated', 'bad_hash', 'bad_signature', 'broken_startup'])
def test_full_transaction_preserves_nested_user_data(tmp_path, monkeypatch, outcome):
    install = tmp_path / 'Applications/Chart Cleaner.app'
    (install / 'Contents/MacOS').mkdir(parents=True)
    (install / 'Contents/MacOS/Chart Cleaner').write_bytes(b'old application')
    (install / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '2.3.0'}))
    data = user_data_dir(platform='darwin', home=tmp_path)
    sentinel = {
        'config.json': b'{"settings":"SYNTHETIC SETTINGS"}\n',
        'data/history.jsonl': b'{"history":"SYNTHETIC HISTORY"}\n',
        'data/token_maps/map.json': b'{"token":"SYNTHETIC MAPPING"}\n',
        'custom_rules/sentinel.py': b'# SYNTHETIC CUSTOM RULE\n',
        'presets/rounds.json': b'{"preset":"SYNTHETIC PRESET"}\n',
    }
    for name, value in sentinel.items():
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    assert not data.is_relative_to(install)
    monkeypatch.setattr(updater, 'default_install_path', lambda: install)
    monkeypatch.setattr(updater, 'current_platform', lambda: 'macos-arm64')
    monkeypatch.setattr(updater.release_identity, 'MACOS_PUBLISHER', 'Fixture publisher')
    monkeypatch.setattr(updater.release_identity, 'VERSION', '2.3.0')
    monkeypatch.setattr(updater, 'HEALTH_TIMEOUT', 1.0)
    archive = updater._prepare_staging() / 'candidate.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('Chart Cleaner.app/Contents/Info.plist', plistlib.dumps({'CFBundleShortVersionString': '2.4.0'}))
        stream.writestr('Chart Cleaner.app/Contents/MacOS/Chart Cleaner', b'new application')
        stream.writestr('Chart Cleaner.app/Contents/Helpers/updater/chart-cleaner-updater', b'new companion')
    asset = ReleaseAsset('https://github.com/nasher721/chartcleaner2/releases/download/v2.4.0/app.zip',
                         hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size)
    manifest = ReleaseManifest(Version.parse('2.4.0'), Version.parse('2.3.0'),
        'https://github.com/nasher721/chartcleaner2/releases/tag/v2.4.0', {'macos-arm64': asset})
    monkeypatch.setattr(updater, 'release_client', lambda: SimpleNamespace(
        check=lambda: (manifest, UpdateStatus('update_available'))))

    def verify(*_args):
        if outcome == 'bad_signature':
            raise UpdateError('signature_invalid')

    monkeypatch.setattr(updater, 'verify_native_signature', verify)
    if outcome == 'bad_hash':
        archive.write_bytes(archive.read_bytes()[:-1])
    children, launches = [], []
    child_code = '''import os,sys,time
from pathlib import Path
from chartcleaner import updater
install = Path(sys.argv[1])
updater.default_install_path = lambda: install
updater.sys.executable = str(install / 'Contents/MacOS/Chart Cleaner')
if os.environ.get(updater.HEALTH_PATH_ENV):
    updater.write_health_marker('2.4.0')
time.sleep(30)
'''

    def launch(path, marker=None, nonce=None):
        launches.append(marker)
        if marker is None and children:
            assert children[0].poll() is not None, 'Failed child must stop before rollback launch'
        env = dict(os.environ)
        env.pop(updater.HEALTH_PATH_ENV, None)
        env.pop(updater.HEALTH_NONCE_ENV, None)
        if marker is not None:
            env[updater.HEALTH_PATH_ENV] = str(marker)
            env[updater.HEALTH_NONCE_ENV] = nonce
        code = 'raise SystemExit(3)' if outcome == 'broken_startup' and marker else child_code
        child = subprocess.Popen([sys.executable, '-c', code, str(path)], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        children.append(child)
        return child

    monkeypatch.setattr(updater, '_launch', launch)
    previous = subprocess.Popen([sys.executable, '-c', 'pass'])
    previous.wait()
    try:
        if outcome == 'updated':
            assert updater.update_install(previous.pid, install, archive, '2.4.0').state == 'updated'
            assert updater._payload_version(install) == '2.4.0'
            assert len(launches) == 1
        else:
            error = {'bad_hash': 'checksum_mismatch', 'bad_signature': 'signature_invalid',
                     'broken_startup': 'startup_failed'}[outcome]
            with pytest.raises(UpdateError, match=error):
                updater.update_install(previous.pid, install, archive, '2.4.0')
            assert updater._payload_version(install) == '2.3.0'
            assert (install / 'Contents/MacOS/Chart Cleaner').read_bytes() == b'old application'
            assert len(launches) == (2 if outcome == 'broken_startup' else 0)
        assert {str(p.relative_to(data)): p.read_bytes() for p in data.rglob('*') if p.is_file()} == sentinel
        assert not list(updater.staging_root().glob('install-*'))
        assert not (install.parent / '.Chart Cleaner.app.rollback').exists()
    finally:
        for child in children:
            updater._stop_failed_child(child)
            if child.stderr:
                child.stderr.close()


def test_first_signed_release_fixture_has_a_compatible_default_interval():
    from scripts.prepare_previous_fixture import previous_contract, replace_version
    previous, minimum, provenance = previous_contract('2.3.0')
    assert (previous, minimum, provenance) == ('2.2.0', '2.2.0', 'synthetic_same_source_previous_version')
    assert previous_contract('2.3.1', previous_tag='v2.3.0', minimum='2.3.0') == (
        '2.3.0', '2.3.0', 'published_signed_release')
    assert replace_version('"""package"""\n__version__ = "2.3.0"\n', previous).endswith("__version__ = '2.2.0'")
    for target, tag, fixture, floor in [
        ('0.0.0', '', '', ''),
        ('2.3.0', '', '2.2.0', '2.3.0'),
        ('2.3.0', 'v2.3.0', '', ''),
        ('2.3.0', 'v2.4.0', '', ''),
        ('2.3.0', '', '2.3.0-rc.1', ''),
    ]:
        with pytest.raises(ValueError):
            previous_contract(target, tag, fixture, floor)
