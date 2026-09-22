"""Safe first-run migration and configuration migration helpers."""

from __future__ import annotations

import json
import os
import stat
import shutil
import tempfile
from pathlib import Path
from typing import Callable


PORTABLE_ITEMS = ("config.json", "data", "presets", "custom_rules")
_SAFE_NAME = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ")


def _safe_relative(path: Path) -> bool:
    return not path.is_absolute() and ".." not in path.parts and all(
        part and set(part) <= _SAFE_NAME for part in path.parts
    )


def _is_reparse(path: Path, info: os.stat_result) -> bool:
    del path
    return bool(getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_entry(path: Path, *, directory: bool | None = None) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if path.is_symlink() or _is_reparse(path, info):
        return False
    mode = info.st_mode
    if directory is True:
        return stat.S_ISDIR(mode)
    if directory is False:
        return stat.S_ISREG(mode)
    return stat.S_ISREG(mode) or stat.S_ISDIR(mode)


def _tree_is_safe(root: Path) -> bool:
    try:
        if not _safe_entry(root, directory=True):
            return False
        for path in root.rglob("*"):
            rel = path.relative_to(root)
            if not _safe_relative(rel) or not _safe_entry(path):
                return False
    except OSError:
        return False
    return True


def _effectively_empty(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        return not any(path.iterdir())
    except OSError:
        return False


def _valid_config(path: Path, validate: Callable[[dict], tuple[list[str], list[str]]] | None) -> bool:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(cfg, dict):
        return False
    if validate is None:
        return True
    try:
        errors, _ = validate(cfg)
    except Exception:
        return False
    return not errors


def _copy_file_nofollow(source: Path, target: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(source, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"not a regular file: {source}")
        with os.fdopen(fd, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        shutil.copystat(source, target, follow_symlinks=False)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _copy_tree_nofollow(source: Path, target: Path) -> None:
    if not _safe_entry(source, directory=True):
        raise OSError(f"not a safe directory: {source}")
    target.mkdir()
    for entry in os.scandir(source):
        child = Path(entry.path)
        child_info = entry.stat(follow_symlinks=False)
        if not _safe_entry(child):
            raise OSError(f"unsafe link: {child}")
        child_target = target / entry.name
        if stat.S_ISDIR(child_info.st_mode):
            _copy_tree_nofollow(child, child_target)
        elif stat.S_ISREG(child_info.st_mode):
            _copy_file_nofollow(child, child_target)
        else:
            raise OSError(f"unsupported file: {child}")


def migrate_portable_data(source: str | Path, destination: str | Path,
                          validate: Callable[[dict], tuple[list[str], list[str]]] | None = None
                          ) -> bool:
    """Atomically copy a portable install into an empty frozen data directory.

    Returns False when no migration was needed or validation failed. Source files
    are never changed; only a validated temporary tree can become the destination.
    """
    source, destination = Path(source), Path(destination)
    try:
        same_location = source.resolve() == destination.resolve()
    except (OSError, RuntimeError):
        return False
    if same_location or not _effectively_empty(destination):
        return False
    if os.path.lexists(destination) and not _safe_entry(destination, directory=True):
        return False
    if not _safe_entry(source, directory=True) or not _tree_is_safe(source):
        return False
    config = source / "config.json"
    if not config.is_file() or not _valid_config(config, validate):
        return False
    present = [source / name for name in PORTABLE_ITEMS if (source / name).exists()]
    for item in present:
        if not _safe_entry(item):
            return False
        if item.name != "config.json" and not _safe_entry(item, directory=True):
            return False
        if item.is_dir() and not _tree_is_safe(item):
            return False
    parent = destination.parent
    displaced: Path | None = None
    try:
        parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
    except OSError:
        return False
    try:
        for item in present:
            target = temp / item.name
            if item.is_dir():
                _copy_tree_nofollow(item, target)
            else:
                _copy_file_nofollow(item, target)
        # Re-check the source after copying: on platforms without O_NOFOLLOW,
        # a concurrent replacement must never make the staged tree installable.
        if not _tree_is_safe(source):
            return False
        if not _valid_config(temp / "config.json", validate):
            return False
        if destination.exists():
            if (not _safe_entry(destination, directory=True)
                    or not _effectively_empty(destination)):
                return False
            # A pre-existing empty directory cannot be replaced directly by
            # os.replace on POSIX. Give it a private sibling name first, then
            # restore it if installation fails.
            displaced = Path(tempfile.mkdtemp(prefix=f".{destination.name}.old-",
                                               dir=parent))
            displaced.rmdir()
            os.replace(destination, displaced)
        os.replace(temp, destination)
        temp = None
        if displaced is not None:
            displaced.rmdir()
            displaced = None
        return True
    except (OSError, shutil.Error):
        if displaced is not None and not destination.exists():
            try:
                os.replace(displaced, destination)
            except OSError:
                pass
        return False
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)
        if displaced is not None:
            shutil.rmtree(displaced, ignore_errors=True)


def migrate_config(path: str | Path, validate: Callable[[dict], tuple[list[str], list[str]]],
                   migrate: Callable[[dict], dict] | None = None) -> bool:
    """Back up and apply a known config migration, retaining the old file on failure."""
    path = Path(path)
    try:
        if not _safe_entry(path, directory=False):
            return False
        original = path.read_bytes()
        cfg = json.loads(original.decode("utf-8"))
        if not isinstance(cfg, dict):
            return False
        try:
            updated = migrate(cfg) if migrate else cfg
        except Exception:
            return False
        if not isinstance(updated, dict):
            return False
        try:
            errors, _ = validate(updated)
        except Exception:
            return False
        if errors:
            return False
        # Compare against a fresh parse because a migration may mutate cfg.
        if updated == json.loads(original):
            return True
        backup = path.with_suffix(path.suffix + ".bak")
        if os.path.lexists(backup) and not _safe_entry(backup, directory=False):
            return False
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, indent=4, ensure_ascii=False)
                handle.write("\n")
            shutil.copy2(path, backup)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return False
