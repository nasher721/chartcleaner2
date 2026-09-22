# One spec, three products; paths are checkout-relative on both release runners.
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from chartcleaner import __version__

product = os.environ.get('CC_BUILD_PRODUCT', 'app')
if product not in {'app', 'updater', 'installer'}:
    raise ValueError('Unknown build product')
mac = sys.platform == 'darwin'
name = {'app': 'Chart Cleaner', 'updater': 'chart-cleaner-updater',
        'installer': 'Install Chart Cleaner' if mac else 'ChartCleanerSetup'}[product]
entry = {'app': 'app.py', 'updater': 'updater_entry.py', 'installer': 'installer_entry.py'}[product]
datas, binaries, hiddenimports = [], [], []
if product == 'app':
    # Never bundle mutable configuration, token maps, exports, or custom scripts.
    datas = [(str(ROOT / 'chartcleaner/default_config.json'), 'chartcleaner'),
             (str(ROOT / 'chartcleaner/packs'), 'chartcleaner/packs')]
    for package in ('nicegui', 'spacy', 'en_core_web_sm', 'thinc', 'srsly', 'catalogue',
                    'wasabi', 'weasel', 'preshed', 'murmurhash', 'cymem', 'blis', 'regex',
                    'presidio_analyzer', 'presidio_anonymizer', 'phonenumbers', 'tldextract',
                    'thefuzz', 'rapidfuzz', 'pyperclip', 'pymupdf', 'watchdog', 'ex4nicegui'):
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hiddenimports += h
    if mac:
        d, b, h = collect_all('uvloop')
        datas += d
        binaries += b
        hiddenimports += h
elif product == 'installer':
    resources = Path(os.environ['CC_INSTALLER_RESOURCES'])
    datas = [(str(resources / filename), '.') for filename in
             ('installer-payload.zip', 'installer-manifest.json')]

a = Analysis([str(ROOT / entry)], pathex=[str(ROOT)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
onefile = product == 'installer' and not mac
exe = EXE(pyz, a.scripts, a.binaries if onefile else [], a.datas if onefile else [],
          exclude_binaries=not onefile, name=name, console=product == 'updater',
          upx=False, target_arch='arm64' if mac else None,
          version=os.environ.get('CC_WINDOWS_VERSION_FILE') if not mac else None)
if not onefile:
    coll = COLLECT(exe, a.binaries, a.datas, name=name, upx=False)
    if mac and product != 'updater':
        app = BUNDLE(coll, name=name + '.app',
                     bundle_identifier='com.nasher721.chart-cleaner' + ('.installer' if product == 'installer' else ''),
                     info_plist={'CFBundleShortVersionString': __version__,
                                 'CFBundleVersion': __version__,
                                 'NSHighResolutionCapable': True})
