"""Platform paths for mutable Chart Cleaner data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Chart Cleaner"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_dir(*, platform: str | None = None, env: dict[str, str] | None = None,
                  home: Path | None = None) -> Path:
    """Return the mutable per-user directory used by a frozen build."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if platform.startswith("win"):
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / APP_NAME
    # Development and unsupported frozen platforms get a deterministic local path.
    return home / ".local" / "share" / APP_NAME


def portable_root(*, executable: str | Path | None = None,
                  meipass: str | Path | None = None) -> Path:
    """Locate the sibling directory that may contain portable user data."""
    if executable is None:
        executable = sys.executable
    exe = Path(executable).resolve()
    if sys.platform == "darwin" or exe.suffix == ".app":
        d = exe.parent
        while d.name in {"MacOS", "Contents"} or d.suffix == ".app":
            d = d.parent
        return d
    return exe.parent


def frozen_resource_root(meipass: str | Path | None = None) -> Path:
    return Path(meipass if meipass is not None else getattr(sys, "_MEIPASS", "."))

