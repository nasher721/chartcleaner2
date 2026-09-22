"""Import ephemeral signing credentials on a release runner, without logging values."""
import base64
import os
import secrets
import subprocess
import sys
from pathlib import Path


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def prepare():
    temp = Path(os.environ['RUNNER_TEMP'])
    mac = sys.platform == 'darwin'
    prefix = 'MAC' if mac else 'WINDOWS'
    required = [prefix + '_CERT_BASE64', prefix + '_CERT_PASSWORD', 'MACOS_PUBLISHER', 'WINDOWS_PUBLISHER']
    if mac:
        required += ['APPLE_ID', 'APPLE_APP_PASSWORD', 'APPLE_TEAM_ID']
    if any(not os.environ.get(key) for key in required):
        raise RuntimeError('Required release signing credentials are missing')
    certificate = temp / (prefix.lower() + '-signing.p12')
    certificate.write_bytes(base64.b64decode(os.environ[prefix + '_CERT_BASE64'], validate=True))
    values = {}
    if mac:
        keychain = temp / 'chart-cleaner-signing.keychain-db'
        password = secrets.token_urlsafe(32)
        run('security', 'create-keychain', '-p', password, keychain)
        run('security', 'set-keychain-settings', '-lut', '21600', keychain)
        run('security', 'unlock-keychain', '-p', password, keychain)
        run('security', 'import', certificate, '-P', os.environ['MAC_CERT_PASSWORD'],
            '-A', '-t', 'cert', '-f', 'pkcs12', '-k', keychain)
        run('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:', '-k', password, keychain)
        values['MAC_KEYCHAIN'] = str(keychain)
    else:
        candidates = sorted(Path('C:/Program Files (x86)/Windows Kits/10/bin').glob('*/x64/signtool.exe'))
        if not candidates:
            raise RuntimeError('Windows SDK SignTool is required')
        values.update(SIGNTOOL=str(candidates[-1]), WINDOWS_CERTIFICATE=str(certificate))
    with Path(os.environ['GITHUB_ENV']).open('a') as stream:
        for key, value in values.items():
            if '\n' in value or '\r' in value:
                raise ValueError('Unsafe runner path')
            stream.write(f'{key}={value}\n')


if __name__ == '__main__':
    try:
        prepare()
    except Exception as exc:
        print(f'Signing setup failed ({type(exc).__name__}); publication is blocked.', file=sys.stderr)
        raise SystemExit(1) from None
