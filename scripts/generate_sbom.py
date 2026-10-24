"""Generate a CycloneDX SBOM for the installed release environment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import uuid

from chartcleaner import __version__


def _timestamp() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is None:
        epoch = subprocess.check_output(["git", "log", "-1", "--format=%ct"], text=True).strip()
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat().replace("+00:00", "Z")


def sbom() -> dict:
    packages = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"]
        if not name:
            continue
        packages.append({
            "type": "library",
            "name": name,
            "version": dist.version,
            "purl": f"pkg:pypi/{name.lower().replace('_', '-')}@{dist.version}",
        })
    identity = f"nasher721/chartcleaner2@{__version__}:{_timestamp()}"
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
        "version": 1,
        "metadata": {
            "timestamp": _timestamp(),
            "component": {"type": "application", "name": "Chart Cleaner", "version": __version__},
        },
        "components": packages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sbom(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
