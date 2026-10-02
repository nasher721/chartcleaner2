"""Portable sharing of user learned removal/replacement rules.

The format intentionally contains only user rules and abbreviation overrides;
it never serializes chart text, history, paths, scripts, or application prefs.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
KIND = "chart-cleaner-rules"
ABBREVIATION_KEY = "abbreviations"


def _pair(value: Any, context: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{context} must be a [pattern, replacement] pair")
    pattern, replacement = value
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"{context} pattern must be a non-empty string")
    if not isinstance(replacement, str):
        raise ValueError(f"{context} replacement must be a string")
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
        compiled.sub(replacement, "")
    except re.error as exc:
        raise ValueError(f"{context} has invalid regex: {exc}") from exc
    return [pattern, replacement]


def _merge_rules(current: list[list[str]], incoming: list[list[str]]) -> tuple[list[list[str]], int]:
    """Keep existing same-pattern rules; skip ambiguous imported replacements."""
    result = list(current)
    by_pattern = {pair[0]: pair[1] for pair in current}
    conflicts = 0
    for pair in incoming:
        if pair[0] in by_pattern:
            if by_pattern[pair[0]] != pair[1]:
                conflicts += 1
            continue
        by_pattern[pair[0]] = pair[1]
        result.append(pair)
    return result, conflicts


def _abbreviations(config: dict) -> Any:
    return config.get(ABBREVIATION_KEY)


def _validate_abbreviations(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("abbreviations must be an object")
    disabled = value.get("disabled", [])
    custom = value.get("custom", [])
    if not isinstance(disabled, list) or any(not isinstance(x, str) or not x.strip() for x in disabled):
        raise ValueError("abbreviations.disabled must be a list of non-empty strings")
    if not isinstance(custom, list):
        raise ValueError("abbreviations.custom must be a list")
    checked = []
    for i, item in enumerate(custom):
        if not isinstance(item, dict) or not isinstance(item.get("term"), str) or not item["term"].strip():
            raise ValueError(f"abbreviations.custom[{i}].term must be non-empty text")
        if not isinstance(item.get("replacement"), str) or not item["replacement"].strip():
            raise ValueError(f"abbreviations.custom[{i}].replacement must be non-empty text")
        if "enabled" in item and not isinstance(item["enabled"], bool):
            raise ValueError(f"abbreviations.custom[{i}].enabled must be true or false")
        checked.append({"term": item["term"].strip(), "replacement": item["replacement"],
                        "enabled": item.get("enabled", True)})
    terms = [item["term"].casefold() for item in checked]
    if len(terms) != len(set(terms)):
        raise ValueError("abbreviations.custom contains duplicate terms")
    return {"disabled": list(dict.fromkeys(disabled)), "custom": checked}


def payload_from_config(config: dict) -> dict:
    """Return the small, portable rules document for *config*."""
    learned = [_pair(x, f"learned_rules[{i}]") for i, x in enumerate(config.get("learned_rules", []))]
    abbreviations = _abbreviations(config)
    if abbreviations is not None:
        abbreviations = _validate_abbreviations(abbreviations)
    # Keep the canonical ordered stream: learned rules are sequential and
    # reordering a removal ahead of a replacement can change the output.
    rules = [{"mode": "remove" if p[1] == "" else "replace",
              "pattern": p[0], "replacement": p[1]} for p in learned]
    removals = [p[0] for p in learned if p[1] == ""]
    replacements = [{"pattern": p[0], "replacement": p[1]} for p in learned if p[1] != ""]
    return {
        "kind": KIND,
        "version": FORMAT_VERSION,
        "rules": rules,
        "removals": removals,
        "replacements": replacements,
        **({"abbreviations": abbreviations} if abbreviations is not None else {}),
    }


def validate_payload(payload: Any) -> dict:
    if not isinstance(payload, dict) or payload.get("kind") != KIND:
        raise ValueError("Not a Chart Cleaner rules file")
    version = payload.get("version", 0)
    if type(version) is not int or version != FORMAT_VERSION:
        raise ValueError(f"Unsupported rules file version: {version}")
    removals = payload.get("removals", [])
    replacements = payload.get("replacements", [])
    if not isinstance(removals, list) or not isinstance(replacements, list):
        raise ValueError("removals and replacements must be lists")
    pairs: list[list[str]] = []
    ordered = payload.get("rules")
    if ordered is not None:
        if not isinstance(ordered, list):
            raise ValueError("rules must be a list")
        for i, item in enumerate(ordered):
            if not isinstance(item, dict) or item.get("mode") not in {"remove", "replace"}:
                raise ValueError(f"rules[{i}] must specify mode remove or replace")
            replacement = "" if item["mode"] == "remove" else item.get("replacement")
            pair = _pair([item.get("pattern"), replacement], f"rules[{i}]")
            if item["mode"] == "replace" and pair[1] == "":
                raise ValueError(f"rules[{i}] replacement must not be empty; use remove")
            pairs.append(pair)
    else:
        for i, pattern in enumerate(removals):
            pairs.append(_pair([pattern, ""], f"removals[{i}]"))
        for i, item in enumerate(replacements):
            if isinstance(item, dict):
                item = [item.get("pattern"), item.get("replacement")]
            pairs.append(_pair(item, f"replacements[{i}]"))
            if pairs[-1][1] == "":
                raise ValueError(f"replacements[{i}] replacement must not be empty; use removals")
    abbreviations = (_validate_abbreviations(payload["abbreviations"])
                     if "abbreviations" in payload else None)
    result = {"kind": KIND, "version": FORMAT_VERSION,
            "rules": [{"mode": "remove" if p[1] == "" else "replace",
                        "pattern": p[0], "replacement": p[1]} for p in pairs],
            "removals": [p[0] for p in pairs if p[1] == ""],
            "replacements": [{"pattern": p[0], "replacement": p[1]} for p in pairs if p[1]],
            }
    if abbreviations is not None:
        result["abbreviations"] = abbreviations
    return result


def pairs_from_payload(payload: dict) -> list[list[str]]:
    checked = validate_payload(payload)
    return [[x["pattern"], x["replacement"]] for x in checked["rules"]]


def preview_import(config: dict, payload: dict, mode: str = "merge") -> dict:
    """Validate and summarize an import without changing config."""
    if mode not in {"merge", "replace"}:
        raise ValueError("mode must be 'merge' or 'replace'")
    checked = validate_payload(payload)
    incoming = pairs_from_payload(checked)
    current = [_pair(x, f"learned_rules[{i}]") for i, x in enumerate(config.get("learned_rules", []))]
    merged, rule_conflicts = _merge_rules(current if mode == "merge" else [], incoming)
    current_abbrev = config.get(ABBREVIATION_KEY)
    incoming_abbrev = checked.get(ABBREVIATION_KEY)
    conflicts = 0
    if mode == "merge" and isinstance(current_abbrev, dict) and isinstance(incoming_abbrev, dict):
        terms = {x["term"].strip().casefold(): x for x in current_abbrev.get("custom", [])}
        conflicts = sum(x["term"].casefold() in terms and
                        (terms[x["term"].casefold()]["replacement"] != x["replacement"] or
                         terms[x["term"].casefold()].get("enabled", True) != x.get("enabled", True))
                        for x in incoming_abbrev.get("custom", []))
    added = len(merged) - len(current) if mode == "merge" else len(merged)
    return {"mode": mode, "incoming": len(incoming), "existing": len(current),
            "added": added,
            "duplicates": len(incoming) - added - rule_conflicts,
            "rule_conflicts": rule_conflicts,
            "result": len(merged), "abbreviations": len((incoming_abbrev or {}).get("custom", [])) if incoming_abbrev else 0,
            "abbreviation_conflicts": conflicts,
            "disabled_abbreviations": len((incoming_abbrev or {}).get("disabled", [])),
            "enables_learned_rules": bool(incoming) and config.get("stage_options", {}).get("learned_rules", {}).get("enabled") is False}


def apply_import(config: dict, payload: dict, mode: str = "merge") -> dict:
    """Return a changed copy after full validation; input is never mutated."""
    preview_import(config, payload, mode)
    checked = validate_payload(payload)
    current = [_pair(x, f"learned_rules[{i}]") for i, x in enumerate(config.get("learned_rules", []))]
    incoming = pairs_from_payload(checked)
    out = dict(config)
    out["learned_rules"] = _merge_rules(current if mode == "merge" else [], incoming)[0]
    if incoming:
        options = dict(out.get("stage_options") or {})
        learned_options = dict(options.get("learned_rules") or {})
        learned_options["enabled"] = True
        options["learned_rules"] = learned_options
        out["stage_options"] = options
    incoming_abbrev = checked.get(ABBREVIATION_KEY)
    if incoming_abbrev is not None:
        if mode == "replace" or not isinstance(config.get(ABBREVIATION_KEY), dict):
            out[ABBREVIATION_KEY] = incoming_abbrev
        else:
            current_abbrev = config[ABBREVIATION_KEY]
            existing = {x["term"].strip().casefold() for x in current_abbrev.get("custom", [])}
            additions = [x for x in incoming_abbrev["custom"] if x["term"].casefold() not in existing]
            out[ABBREVIATION_KEY] = {
                "disabled": list(dict.fromkeys(current_abbrev.get("disabled", []) + incoming_abbrev["disabled"])),
                "custom": current_abbrev.get("custom", []) + additions,
            }
    return out


def export_json(config: dict, destination: str | Path) -> Path:
    path = Path(destination)
    path.write_text(json.dumps(payload_from_config(config), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def import_json(source: str | Path) -> dict:
    return validate_payload(json.loads(Path(source).read_text(encoding="utf-8")))


def export_csv(config: dict, destination: str | Path) -> Path:
    path = Path(destination)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("mode", "pattern", "replacement"))
    for rule in payload_from_config(config)["rules"]:
        writer.writerow((rule["mode"], rule["pattern"], rule["replacement"]))
    path.write_text(output.getvalue(), encoding="utf-8")
    return path


def import_csv(source: str | Path) -> dict:
    with Path(source).open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        if not rows.fieldnames or {"mode", "pattern", "replacement"} - set(rows.fieldnames):
            raise ValueError("CSV must have mode, pattern, and replacement columns")
        rules = []
        for i, row in enumerate(rows, 2):
            mode = (row.get("mode") or "").strip().lower()
            pattern = row.get("pattern") or ""
            replacement = row.get("replacement") or ""
            if mode == "remove" and not replacement:
                rules.append({"mode": "remove", "pattern": pattern, "replacement": ""})
            elif mode == "replace" and replacement:
                rules.append({"mode": "replace", "pattern": pattern, "replacement": replacement})
            else:
                raise ValueError(f"CSV row {i}: mode must be remove or replace with matching values")
    return validate_payload({"kind": KIND, "version": FORMAT_VERSION,
                             "rules": rules, "removals": [], "replacements": [],
                             })


class RuleSharingPanel:
    """Small UI adapter: host apps provide callbacks for rendering controls."""
    def __init__(self, load_config, save_config):
        self.load_config = load_config
        self.save_config = save_config

    def preview(self, payload: dict, mode: str = "merge") -> dict:
        return preview_import(self.load_config(), payload, mode)

    def apply(self, payload: dict, mode: str = "merge") -> dict:
        updated = apply_import(self.load_config(), payload, mode)
        from .engine import validate_config
        errors, _ = validate_config(updated)
        if errors:
            raise ValueError(errors[0])
        self.save_config(updated)
        return updated


def render_rule_sharing(load_current, save_current, open_exports=None):
    """Render self-contained NiceGUI controls for portable rule sharing.

    The host owns config loading/saving, so this can be mounted from any
    settings page without importing or modifying the main application.
    """
    from nicegui import ui

    from .engine import validate_config

    panel = RuleSharingPanel(load_current, save_current)
    with ui.card().classes("w-full gap-3"):
        ui.label("Share learned rules").classes("text-lg font-semibold")
        ui.label("JSON includes saved text rules and abbreviation settings. CSV includes text rules only. "
                 "Review saved rule text before sharing; charts and run history are excluded.").classes("text-sm opacity-70")
        with ui.row().classes("gap-2 flex-wrap"):
            def download(fmt: str) -> None:
                import time
                from . import store
                suffix = ".csv" if fmt == "csv" else ".json"
                try:
                    store.ensure_dirs()
                    path = store.EXPORTS_DIR / f"chart-cleaner-rules-{time.time_ns()}{suffix}"
                    (export_csv if fmt == "csv" else export_json)(load_current(), path)
                    ui.download(path, filename=f"chart-cleaner-rules{suffix}")
                    ui.notify("Rules saved in the exports folder; download started.", type="positive")
                except Exception as exc:
                    ui.notify(f"Could not export rules: {exc}", type="negative")

            async def copy_json() -> None:
                try:
                    payload = json.dumps(payload_from_config(load_current()), indent=2, ensure_ascii=False)
                    await ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(payload)})")
                    ui.notify("Rules JSON copied.", type="positive")
                except Exception:
                    ui.notify("Clipboard access failed. Export the rules file instead.", type="warning")

            ui.button("Export all rules JSON", on_click=lambda: download("json")).props("outline")
            ui.button("Export text rules CSV", on_click=lambda: download("csv")).props("outline")
            ui.button("Copy JSON", icon="content_copy", on_click=copy_json).props("flat")
            if open_exports:
                ui.button("Open exports folder", icon="folder_open", on_click=open_exports).props("flat")
            mode = ui.radio({"merge": "Merge (keep existing)", "replace": "Replace"}, value="merge")
            import_box = ui.column().classes("w-full gap-2")
        with import_box:
            status = ui.label("Choose a JSON or CSV file to preview it.").classes("text-sm opacity-70")
            preview_box = ui.column().classes("w-full gap-1")
            actions = ui.row().classes("justify-end gap-2")
            actions.set_visibility(False)

        pending: dict[str, Any] = {}

        def clear_preview() -> None:
            pending.clear()
            preview_box.clear()
            actions.set_visibility(False)
            status.set_text("Choose a JSON or CSV file to preview it.")

        def render_pending() -> None:
            if "payload" not in pending:
                return
            summary = panel.preview(pending["payload"], mode.value)
            preview_box.clear()
            with preview_box:
                ui.label(f"{pending['name']}: {summary['incoming']} rules; {summary['added']} new; {summary['duplicates']} duplicate(s); result {summary['result']}.").classes("text-sm")
                ui.label(f"Abbreviation custom rows: {summary['abbreviations']}; conflicts: {summary['abbreviation_conflicts']}; conflicting text rules skipped: {summary['rule_conflicts']}").classes("text-xs opacity-70")
                if summary["enables_learned_rules"]:
                    ui.label("Learned rules will be enabled for future full cleans.").classes("text-sm")
                if mode.value == "replace":
                    ui.label("Replaces all saved text rules. Abbreviation settings are replaced only if this file includes them.").classes("text-sm text-orange-700")
                for rule in pending["payload"].get("rules", [])[:20]:
                    ui.label(f"{rule['mode']}: {rule['pattern']}" + (f" → {rule['replacement']}" if rule["mode"] == "replace" else " → removed")).classes("text-xs font-mono break-all")
                abbreviation_settings = pending["payload"].get("abbreviations", {})
                for item in abbreviation_settings.get("custom", [])[:20]:
                    ui.label(f"{item['term']} → {item['replacement']}" +
                             (" (disabled)" if not item.get("enabled", True) else "")).classes("text-xs break-all")
                if summary["disabled_abbreviations"]:
                    ui.label(f"{summary['disabled_abbreviations']} disabled bundled terms: " +
                             ", ".join(abbreviation_settings["disabled"][:20])).classes("text-xs break-all")
                if any(len(items) > 20 for items in (pending["payload"].get("rules", []),
                       abbreviation_settings.get("custom", []), abbreviation_settings.get("disabled", []))):
                    ui.label("Preview shows the first 20 entries in each category.").classes("text-xs opacity-70")

        async def show_preview(event) -> None:
            try:
                if hasattr(event, "file"):
                    name, content = str(event.file.name), await event.file.read()
                else:
                    name, content = str(event.name), event.content.read()
                if name.lower().endswith(".csv"):
                    with tempfile.NamedTemporaryFile(suffix=".csv") as f:
                        f.write(content); f.flush()
                        payload = import_csv(f.name)
                else:
                    payload = validate_payload(json.loads(content.decode("utf-8-sig")))
                pending["payload"] = payload
                pending["name"] = name
                render_pending()
                status.set_text("Review the counts, then apply or cancel.")
                actions.set_visibility(True)
            except Exception as exc:
                clear_preview()
                status.set_text(f"Import rejected: {exc}")

        def apply_pending() -> None:
            try:
                panel.apply(pending["payload"], mode.value)
                ui.notify("Rules imported.", type="positive")
                clear_preview()
            except Exception as exc:
                ui.notify(f"Could not apply rules: {exc}", type="negative")

        import tempfile
        mode.on_value_change(lambda _: render_pending())
        ui.upload(on_upload=show_preview, auto_upload=True).props("accept=.json,.csv,application/json,text/csv label='Import rules'").classes("max-w-xs")
        with actions:
            ui.button("Apply", on_click=apply_pending).props("unelevated color=primary")
            ui.button("Cancel", on_click=clear_preview).props("flat")
    return panel
