"""Specialty abbreviation packs and CSV import/export of user abbreviations.

A pack is a JSON file in ``chartcleaner/packs/abbreviations/``::

    {"_pack": {"name": "Neuro ICU", "description": "...", "version": "1.0"},
     "abbreviations": {"custom": [{"term": "...", "replacement": "..."}]}}

Installing a pack adds its entries to ``abbreviations.custom`` tagged with
``"pack": "<name>"`` so it can be removed cleanly later. Entries never
overwrite a term the user already has, and Do Not Use entries are skipped.

The CSV format uses the bundled dictionary's column names, so the same file
opens in a spreadsheet and round-trips through import.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .abbreviation_safety import is_blocked
from .abbreviations import normalize_settings

__all__ = ["PACKS_DIR", "list_packs", "load_pack", "install", "uninstall", "installed",
           "export_csv", "preview_import", "apply_import", "CSV_COLUMNS"]

PACKS_DIR = Path(__file__).with_name("packs") / "abbreviations"
CSV_COLUMNS = ["Abbreviation", "Expanded version", "Enabled", "Pack"]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_packs() -> list[dict]:
    """Metadata for every bundled abbreviation pack, sorted by name."""
    out = []
    for path in sorted(PACKS_DIR.glob("*.json")):
        try:
            data = _read(path)
        except (OSError, json.JSONDecodeError):
            continue
        meta = data.get("_pack") or {}
        entries = (data.get("abbreviations") or {}).get("custom") or []
        out.append({"name": str(meta.get("name") or path.stem),
                    "description": str(meta.get("description") or ""),
                    "version": str(meta.get("version") or ""),
                    "count": len(entries), "file": str(path)})
    return sorted(out, key=lambda p: p["name"].casefold())


def load_pack(name: str) -> list[dict]:
    for pack in list_packs():
        if pack["name"] == name:
            return list((_read(Path(pack["file"])).get("abbreviations") or {}).get("custom") or [])
    raise KeyError(f"Unknown abbreviation pack: {name}")


def installed(cfg: dict | None) -> dict[str, int]:
    """Pack name → number of its entries in the user's settings."""
    group = normalize_settings((cfg or {}).get("abbreviations") if isinstance(cfg, dict) else None)
    counts: dict[str, int] = {}
    for item in group["custom"]:
        if item.get("pack"):
            counts[item["pack"]] = counts.get(item["pack"], 0) + 1
    return counts


def install(cfg: dict, name: str) -> tuple[dict, int, list[str]]:
    """Add a pack. Returns (new config, entries added, terms skipped)."""
    cfg = dict(cfg)
    group = normalize_settings(cfg.get("abbreviations"))
    existing = {c["term"].casefold() for c in group["custom"]}
    added, skipped = 0, []
    for entry in load_pack(name):
        term, replacement = str(entry.get("term", "")).strip(), str(entry.get("replacement", "")).strip()
        if not term or not replacement:
            continue
        if term.casefold() in existing or is_blocked(replacement, term):
            skipped.append(term)
            continue
        group["custom"].append({"term": term, "replacement": replacement, "enabled": True, "pack": name})
        existing.add(term.casefold())
        added += 1
    cfg["abbreviations"] = normalize_settings(group)
    return cfg, added, skipped


def uninstall(cfg: dict, name: str) -> tuple[dict, int]:
    """Remove every entry a pack added. Returns (new config, entries removed)."""
    cfg = dict(cfg)
    group = normalize_settings(cfg.get("abbreviations"))
    before = len(group["custom"])
    group["custom"] = [c for c in group["custom"] if c.get("pack") != name]
    cfg["abbreviations"] = normalize_settings(group)
    return cfg, before - len(group["custom"])


def export_csv(cfg: dict | None) -> str:
    """The user's own abbreviations (custom + pack entries) as CSV."""
    group = normalize_settings((cfg or {}).get("abbreviations") if isinstance(cfg, dict) else None)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for item in group["custom"]:
        writer.writerow([item["replacement"], item["term"], "yes" if item["enabled"] else "no",
                         item.get("pack", "")])
    return buf.getvalue()


def _truthy(value: str) -> bool:
    return value.strip().casefold() not in {"no", "false", "0", "off", "disabled"}


def preview_import(text: str, cfg: dict | None) -> list[dict]:
    """Classify each CSV row against current settings without changing anything.

    Status is one of: new, changed, same, blocked (Do Not Use), invalid.
    Accepts the export format or any CSV with Abbreviation / Expanded version
    columns (for example a copy of the bundled dictionary).
    """
    group = normalize_settings((cfg or {}).get("abbreviations") if isinstance(cfg, dict) else None)
    current = {c["term"].casefold(): c for c in group["custom"]}
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or not {"Abbreviation", "Expanded version"} <= set(reader.fieldnames):
        raise ValueError("The CSV needs 'Abbreviation' and 'Expanded version' columns.")
    rows, seen = [], set()
    for row in reader:
        term = (row.get("Expanded version") or "").strip()
        replacement = (row.get("Abbreviation") or "").strip()
        enabled = _truthy(row.get("Enabled") or "yes")
        pack = (row.get("Pack") or "").strip()
        out = {"term": term, "replacement": replacement, "enabled": enabled, "pack": pack}
        if not term or not replacement or term.casefold() in seen:
            out["status"] = "invalid"
        elif is_blocked(replacement, term):
            out["status"] = "blocked"
        elif term.casefold() not in current:
            out["status"] = "new"
        else:
            old = current[term.casefold()]
            same = old["replacement"] == replacement and old["enabled"] == enabled
            out["status"] = "same" if same else "changed"
        seen.add(term.casefold())
        rows.append(out)
    return rows


def apply_import(cfg: dict, rows: list[dict]) -> tuple[dict, int]:
    """Apply new/changed rows from :func:`preview_import`. Returns (config, count)."""
    cfg = dict(cfg)
    group = normalize_settings(cfg.get("abbreviations"))
    by_term = {c["term"].casefold(): c for c in group["custom"]}
    applied = 0
    for row in rows:
        if row.get("status") not in ("new", "changed"):
            continue
        entry = {"term": row["term"], "replacement": row["replacement"], "enabled": row["enabled"]}
        if row.get("pack"):
            entry["pack"] = row["pack"]
        by_term[row["term"].casefold()] = entry
        applied += 1
    group["custom"] = list(by_term.values())
    cfg["abbreviations"] = normalize_settings(group)
    return cfg, applied
