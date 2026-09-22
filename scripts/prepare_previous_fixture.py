"""Resolve a real prior release or prepare an explicit signed bootstrap fixture."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chartcleaner import __version__ as VERSION
from chartcleaner.update import Version, current_platform


def previous_contract(target: str, previous_tag: str = '', fixture_version: str = '', minimum: str = ''):
    current = Version.parse(target)
    if previous_tag:
        if not re.fullmatch(r'v\d+\.\d+\.\d+', previous_tag):
            raise ValueError('PREVIOUS_RELEASE_TAG must be a stable vMAJOR.MINOR.PATCH tag')
        previous = previous_tag[1:]
        provenance = 'published_signed_release'
    else:
        if fixture_version:
            previous = fixture_version
        else:
            major, minor, patch = map(int, target.split('.'))
            if patch:
                previous = f'{major}.{minor}.{patch - 1}'
            elif minor:
                previous = f'{major}.{minor - 1}.0'
            elif major:
                previous = f'{major - 1}.0.0'
            else:
                raise ValueError('No stable previous version exists below 0.0.0')
        provenance = 'synthetic_same_source_previous_version'
    if not re.fullmatch(r'\d+\.\d+\.\d+', previous):
        raise ValueError('Previous fixture must use a stable version')
    floor = minimum or previous
    prior = Version.parse(previous)
    if prior < Version.parse(floor) or not prior < current:
        raise ValueError('Release requires minimum_supported_version <= previous_version < target_version')
    return previous, floor, provenance


def replace_version(source: str, previous: str):
    updated, count = re.subn(r'^__version__\s*=\s*[\'"][^\'\"]+[\'\"]\s*$',
                            f'__version__ = {previous!r}', source, flags=re.MULTILINE)
    if count != 1:
        raise ValueError('Expected one canonical version declaration')
    return updated


def prepare(output: Path):
    previous, minimum, provenance = previous_contract(
        VERSION, os.environ.get('PREVIOUS_RELEASE_TAG', ''),
        os.environ.get('PREVIOUS_FIXTURE_VERSION', ''), os.environ.get('MINIMUM_SUPPORTED_VERSION', ''))
    output.mkdir(parents=True, exist_ok=True)
    if provenance.startswith('synthetic'):
        # CI runs on a tagged clean checkout: do not copy local state or .venv.
        snapshot = output / 'source.tar'
        subprocess.run(['git', 'archive', '--format=tar', '--output', str(snapshot), 'HEAD'], cwd=ROOT, check=True)
        source = output / 'source'
        source.mkdir()
        with tarfile.open(snapshot) as archive:
            archive.extractall(source, filter='data')
        snapshot.unlink()
        canonical = source / 'chartcleaner/__init__.py'
        canonical.write_text(replace_version(canonical.read_text(), previous))
    values = {'PREVIOUS_VERSION': previous, 'MINIMUM_SUPPORTED_VERSION': minimum,
              'PREVIOUS_PROVENANCE': provenance,
              'PREVIOUS_ARCHIVE': str(output / f'ChartCleaner-{previous}-{current_platform()}.zip')}
    with Path(os.environ['GITHUB_ENV']).open('a') as stream:
        for key, value in values.items():
            if '\n' in value or '\r' in value:
                raise ValueError('Invalid runner path')
            stream.write(f'{key}={value}\n')
    print(f'Previous-version gate: {provenance}; {minimum} <= {previous} < {VERSION}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())
