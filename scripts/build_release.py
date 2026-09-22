"""Native release tooling. Unsigned local builds are never publication inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chartcleaner import __version__ as VERSION
from chartcleaner.update import ReleaseManifest, current_platform, verify_native_signature

REPO = 'nasher721/chartcleaner2'
PLATFORMS = ('macos-arm64', 'windows-x64')


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def require_env(*names):
    missing = [name for name in names if not os.environ.get(name, '').strip()]
    if missing:
        raise RuntimeError('Missing release credentials: ' + ', '.join(missing))


def identity_source(mac: str, windows: str) -> str:
    if not mac.startswith('Developer ID Application: ') or not windows.startswith('CN='):
        raise ValueError('Expected Developer ID common name and Windows certificate subject')
    return ('"""Compiled release trust pins; generated before freezing."""\n'
            'from chartcleaner import __version__ as VERSION\n'
            f'MANIFEST_URL = {f"https://github.com/{REPO}/releases/latest/download/update-manifest.json"!r}\n'
            f'MACOS_PUBLISHER = {mac!r}\nWINDOWS_PUBLISHER = {windows!r}\n')


def prepare_identity():
    require_env('MACOS_PUBLISHER', 'WINDOWS_PUBLISHER')
    (ROOT / 'chartcleaner/release_identity.py').write_text(
        identity_source(os.environ['MACOS_PUBLISHER'], os.environ['WINDOWS_PUBLISHER']))


def zip_tree(source: Path, destination: Path):
    """Keep executable modes and relative framework symlinks in update archives."""
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted([source, *source.rglob('*')]):
            arcname = path.relative_to(source.parent).as_posix()
            if path.is_symlink():
                link = os.readlink(path)
                if not path.resolve().is_relative_to(source.resolve()):
                    raise ValueError('External bundle symlink')
                info = zipfile.ZipInfo(arcname)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, link)
            else:
                archive.write(path, arcname)


def asset_metadata(path: Path, version: str):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'url': f'https://github.com/{REPO}/releases/download/v{version}/{path.name}',
            'size': path.stat().st_size, 'sha256': digest}


def manifest_for(files: dict[str, Path], version: str, minimum: str):
    manifest = {'version': version, 'minimum_supported_version': minimum,
                'notes_url': f'https://github.com/{REPO}/releases/tag/v{version}',
                'platforms': {key: asset_metadata(path, version) for key, path in files.items()}}
    ReleaseManifest.parse(manifest)
    return manifest


def build(output: Path, unsigned: bool, product: str):
    target = current_platform()
    if target == 'macos-arm64' and platform.machine() != 'arm64':
        raise RuntimeError('Build on native Apple Silicon')
    if not unsigned:
        prepare_identity()
    output.mkdir(parents=True, exist_ok=True)
    version_file = output / 'windows-version.txt'
    numbers = tuple(int(n) for n in VERSION.split('.')) + (0,)
    version_file.write_text(
        'VSVersionInfo(ffi=FixedFileInfo(filevers=' + repr(numbers) + ', prodvers=' + repr(numbers) +
        ", mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)), "
        "kids=[StringFileInfo([StringTable('040904B0', [StringStruct('FileVersion', '" + VERSION +
        "'), StringStruct('ProductVersion', '" + VERSION +
        "'), StringStruct('ProductName', 'Chart Cleaner')])]), VarFileInfo([VarStruct('Translation', [1033,1200])])])")
    env = dict(os.environ, CC_WINDOWS_VERSION_FILE=str(version_file), CC_BUILD_PRODUCT=product)
    if product == 'installer':
        env['CC_INSTALLER_RESOURCES'] = str(output / 'installer-resources')
    products = ('updater', 'app') if product == 'app' else ('installer',)
    for item in products:
        env['CC_BUILD_PRODUCT'] = item
        run(sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
            '--distpath', output, '--workpath', output / 'work' / item,
            ROOT / 'Chart Cleaner.spec', env=env, cwd=ROOT)
    if product == 'app':
        dest = (output / 'Chart Cleaner.app/Contents/Helpers/updater' if target == 'macos-arm64'
                else output / 'Chart Cleaner/updater')
        shutil.copytree(output / 'chart-cleaner-updater', dest, dirs_exist_ok=True, symlinks=True)
    if unsigned:
        (output / 'NOT-DISTRIBUTABLE.txt').write_text('Unsigned local build. No release signing or notarization performed.\n')
        print('Unsigned local build only; not distributable.')


def sign_mac(bundle: Path, output: Path):
    require_env('MACOS_PUBLISHER', 'MAC_KEYCHAIN', 'APPLE_ID', 'APPLE_APP_PASSWORD', 'APPLE_TEAM_ID')
    # Sign every Mach-O first, then nested bundles/frameworks, outer app last.
    paths = sorted((p for p in bundle.rglob('*') if not p.is_symlink()), key=lambda p: len(p.parts), reverse=True)
    for path in paths + [bundle]:
        native = path.is_file() and 'Mach-O' in subprocess.check_output(['file', '-b', str(path)], text=True)
        nested = path.is_dir() and path.suffix in {'.app', '.framework', '.bundle'}
        if native or nested:
            run('codesign', '--force', '--options', 'runtime', '--timestamp', '--keychain',
                os.environ['MAC_KEYCHAIN'], '--sign', os.environ['MACOS_PUBLISHER'], path,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    submission = output / (bundle.stem + '-notary.zip')
    run('ditto', '-c', '-k', '--keepParent', bundle, submission)
    response = subprocess.check_output(['xcrun', 'notarytool', 'submit', str(submission),
        '--apple-id', os.environ['APPLE_ID'], '--password', os.environ['APPLE_APP_PASSWORD'],
        '--team-id', os.environ['APPLE_TEAM_ID'], '--wait', '--output-format', 'json'], text=True, stderr=subprocess.PIPE)
    result = json.loads(response)
    if result.get('status') != 'Accepted':
        raise RuntimeError('Apple notarization did not accept artifact')
    run('xcrun', 'notarytool', 'log', result['id'], '--apple-id', os.environ['APPLE_ID'],
        '--password', os.environ['APPLE_APP_PASSWORD'], '--team-id', os.environ['APPLE_TEAM_ID'],
        output / (bundle.stem + '-notary-log.json'), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    run('xcrun', 'stapler', 'staple', bundle)
    run('xcrun', 'stapler', 'validate', bundle)
    verify_native_signature(bundle, os.environ['MACOS_PUBLISHER'])
    submission.unlink()


def sign_windows(root: Path):
    require_env('WINDOWS_PUBLISHER', 'WINDOWS_CERTIFICATE', 'WINDOWS_CERT_PASSWORD', 'SIGNTOOL')
    paths = [root] if root.is_file() else sorted(root.rglob('*.exe'))
    if not paths:
        raise RuntimeError('No Windows executables to sign')
    for path in paths:
        run(os.environ['SIGNTOOL'], 'sign', '/fd', 'SHA256', '/td', 'SHA256',
            '/tr', 'http://timestamp.digicert.com', '/f', os.environ['WINDOWS_CERTIFICATE'],
            '/p', os.environ['WINDOWS_CERT_PASSWORD'], path,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        run(os.environ['SIGNTOOL'], 'verify', '/pa', path,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        verify_native_signature(path, os.environ['WINDOWS_PUBLISHER'])


def signed_package(output: Path, minimum: str, product: str):
    if (output / 'NOT-DISTRIBUTABLE.txt').exists():
        raise RuntimeError('Refusing local unsigned build as release input; use a fresh output directory')
    target = current_platform()
    mac = target == 'macos-arm64'
    name = ('Chart Cleaner.app' if mac else 'Chart Cleaner') if product == 'app' else (
        'Install Chart Cleaner.app' if mac else 'ChartCleanerSetup.exe')
    root = output / name
    if mac:
        sign_mac(root, output)
    else:
        sign_windows(root)
    if product == 'app':
        archive = output / f'ChartCleaner-{VERSION}-{target}.zip'
        zip_tree(root, archive)
        resources = output / 'installer-resources'
        resources.mkdir(exist_ok=True)
        shutil.copyfile(archive, resources / 'installer-payload.zip')
        (resources / 'installer-manifest.json').write_text(json.dumps(manifest_for({target: archive}, VERSION, minimum)))
    elif mac:
        zip_tree(root, output / f'Install-ChartCleaner-{VERSION}-{target}.zip')
    print(f'Signed and verified {product} for {target}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'package', 'manifest', 'check-tag'))
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--product', choices=('app', 'installer'), default='app')
    parser.add_argument('--unsigned-local', action='store_true')
    parser.add_argument('--minimum-version', default='2.3.0')
    args = parser.parse_args()
    output = args.output.resolve()
    if args.action == 'check-tag':
        if os.environ.get('GITHUB_REF_NAME') != 'v' + VERSION:
            raise RuntimeError('Tag must exactly match chartcleaner.__version__')
    elif args.action == 'build':
        build(output, args.unsigned_local, args.product)
    elif args.action == 'package':
        if args.unsigned_local:
            raise RuntimeError('Unsigned publication is prohibited')
        signed_package(output, args.minimum_version, args.product)
    else:
        files = {key: output / f'ChartCleaner-{VERSION}-{key}.zip' for key in PLATFORMS}
        (output / 'update-manifest.json').write_text(json.dumps(manifest_for(files, VERSION, args.minimum_version), indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Subprocess exceptions include argv, which can contain signing passwords.
        print(f'Release step failed ({type(exc).__name__}); publication is blocked.', file=sys.stderr)
        raise SystemExit(1) from None
