"""Offline regression checks for release metadata, archives, and fail-closed builds."""
import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

from chartcleaner.update import ReleaseManifest, safe_extract_archive
from scripts import build_release as release


def test_archive_roundtrip_preserves_companion_modes_and_framework_links(tmp_path):
    app = tmp_path / 'source' / 'Chart Cleaner.app'
    executable = app / 'Contents/Helpers/updater/chart-cleaner-updater'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'synthetic-executable')
    executable.chmod(0o755)
    (app / 'Contents/Resources').mkdir()
    if os.name != 'nt':
        (app / 'Contents/Resources/helper').symlink_to('../Helpers/updater/chart-cleaner-updater')
    archive = tmp_path / 'payload.zip'
    release.zip_tree(app, archive)
    destination = tmp_path / 'extracted'
    safe_extract_archive(archive, destination)
    restored = destination / app.name / executable.relative_to(app)
    assert restored.read_bytes() == executable.read_bytes()
    if os.name != 'nt':
        assert restored.stat().st_mode & 0o777 == 0o755
        link = destination / app.name / 'Contents/Resources/helper'
        assert link.is_symlink() and link.read_bytes() == b'synthetic-executable'


def test_external_bundle_link_rejected(tmp_path):
    if os.name == 'nt':
        pytest.skip('Windows standard accounts cannot create unprivileged symlinks')
    app = tmp_path / 'Chart Cleaner.app'
    app.mkdir()
    outside = tmp_path / 'private-config'
    outside.write_text('private')
    (app / 'external').symlink_to(outside)
    with pytest.raises(ValueError, match='External bundle symlink'):
        release.zip_tree(app, tmp_path / 'payload.zip')


def test_final_manifest_hashes_exact_signed_bytes(tmp_path):
    files = {}
    for target in release.PLATFORMS:
        path = tmp_path / f'ChartCleaner-2.4.0-{target}.zip'
        path.write_bytes(('signed-and-stapled-' + target).encode())
        files[target] = path
    manifest = release.manifest_for(files, '2.4.0', '2.3.0')
    parsed = ReleaseManifest.parse(json.dumps(manifest))
    assert str(parsed.version) == '2.4.0'
    assert parsed.notes_url == 'https://github.com/nasher721/chartcleaner2/releases/tag/v2.4.0'
    for target, path in files.items():
        assert parsed.platforms[target].url == f'https://github.com/nasher721/chartcleaner2/releases/download/v2.4.0/{path.name}'
        assert parsed.platforms[target].sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert parsed.platforms[target].size == path.stat().st_size
    files['macos-arm64'].unlink()
    with pytest.raises(FileNotFoundError):
        release.manifest_for(files, '2.4.0', '2.3.0')


def test_publisher_pins_are_literals_not_executable_code():
    mac = 'Developer ID Application: A "quoted" Name (TEAM)'
    windows = "CN=Name';raise RuntimeError('injection')#"
    tree = ast.parse(release.identity_source(mac, windows))
    assignments = {node.targets[0].id: ast.literal_eval(node.value)
                   for node in tree.body if isinstance(node, ast.Assign)}
    from chartcleaner.release_identity import MANIFEST_URL
    assert assignments['MANIFEST_URL'] == MANIFEST_URL == 'https://github.com/nasher721/chartcleaner2/releases/latest/download/update-manifest.json'
    assert assignments['MACOS_PUBLISHER'] == mac
    assert assignments['WINDOWS_PUBLISHER'] == windows
    with pytest.raises(ValueError):
        release.identity_source('', windows)
    with pytest.raises(ValueError):
        release.identity_source(mac, '')


def test_missing_credentials_and_unsigned_outputs_fail_before_signing(tmp_path, monkeypatch):
    monkeypatch.delenv('MACOS_PUBLISHER', raising=False)
    monkeypatch.delenv('WINDOWS_PUBLISHER', raising=False)
    with pytest.raises(RuntimeError, match='Missing release credentials'):
        release.prepare_identity()
    (tmp_path / 'NOT-DISTRIBUTABLE.txt').write_text('unsigned')
    with pytest.raises(RuntimeError, match='Refusing local unsigned build'):
        release.signed_package(tmp_path, '2.3.0', 'app')
