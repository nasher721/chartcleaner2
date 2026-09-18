"""Rule packs: curated, read-only rule sets shipped with the app.

A pack is a JSON file in ``chartcleaner/packs/`` shaped like::

    {
      "_pack": {"name": "...", "description": "...",
                 "source": "...", "version": "1.0"},
      ...regular config keys (epic_phi_patterns, emr_line_metadata, ...)
    }

``install_pack`` copies the config part into the user's ``presets/`` folder
(stripping ``_pack``), so packs compose with the existing preset system:
install once, then apply/edit like any preset. ``apply_pack`` validates and
writes it straight to config.json.
"""

from __future__ import annotations

import json
from importlib.resources import files as _pkg_files
from pathlib import Path

from .engine import validate_config
from . import store

__all__ = ["list_packs", "load_pack", "install_pack", "apply_pack"]


def _packs_root() -> Path:
    try:
        return Path(str(_pkg_files("chartcleaner").joinpath("packs")))
    except Exception:
        return Path(__file__).resolve().parent / "packs"


def list_packs() -> list[dict]:
    """Pack metadata summaries, sorted by name."""
    root = _packs_root()
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(root.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        meta = data.get("_pack") or {}
        if not meta.get("name"):
            meta["name"] = p.stem
        out.append({
            "file": str(p),
            "name": str(meta.get("name")),
            "description": str(meta.get("description") or ""),
            "source": str(meta.get("source") or ""),
            "version": str(meta.get("version") or ""),
            "rules": sum(len(v) for k, v in data.items()
                         if k != "_pack" and isinstance(v, list)),
        })
    return out


def _resolve_pack(name: str) -> Path | None:
    """Find a pack file by file stem or by its _pack.name metadata."""
    root = _packs_root()
    if not root.is_dir():
        return None
    direct = root / f"{name}.json"
    if direct.exists():
        return direct
    if name.endswith(".json"):
        cand = Path(name)
        if cand.exists():
            return cand
    for p in list_packs():
        if p["name"] == name and Path(p["file"]).exists():
            return Path(p["file"])
    return None


def load_pack(name: str) -> dict:
    """Config part of a pack by pack name (or file path), `_pack` stripped."""
    path = _resolve_pack(name)
    if path is None:
        raise FileNotFoundError(f"Unknown pack '{name}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_pack", None)
    return data


def install_pack(name: str) -> tuple[str, str]:
    """Copy a pack's config into presets/. Returns (preset_name, message)."""
    packs = {p["name"]: p for p in list_packs()}
    meta = packs.get(name)
    if meta is None:
        return "", f"Unknown pack '{name}'."
    cfg = load_pack(name)
    errors, _ = validate_config(cfg)
    if errors:
        return meta["name"], f"Pack is invalid: {errors[0]}"
    preset_name = f"[pack] {meta['name']}"
    store.save_preset(preset_name, cfg)
    return preset_name, (f"Installed pack '{meta['name']}' as preset "
                         f"'{preset_name}' ({meta['rules']} rule entries).")


def apply_pack(name: str) -> tuple[bool, str]:
    """Validate a pack and make it the live config.json."""
    try:
        cfg = load_pack(name)
    except FileNotFoundError:
        return False, f"Unknown pack '{name}'."
    errors, _ = validate_config(cfg)
    if errors:
        return False, "Pack has invalid rules: " + errors[0]
    store.rotate_config_backup()
    from .engine import save_config
    save_config(cfg, store.CONFIG_PATH)
    return True, "Pack applied."
