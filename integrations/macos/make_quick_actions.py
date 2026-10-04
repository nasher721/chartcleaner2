#!/usr/bin/env python3
"""Install macOS Quick Actions that clean, abbreviate or expand selected text.

Creates three services in ~/Library/Services (no admin needed):

* Chart Cleaner – Clean Selection
* Chart Cleaner – Abbreviate Selection
* Chart Cleaner – Expand Selection

Each runs this folder's ``clean-chart --stdin --stdout`` on the selected text
and replaces the selection with the result. Use them from the app menu →
Services, a right-click, or a keyboard shortcut (System Settings → Keyboard →
Keyboard Shortcuts → Services → Text).

    python3 integrations/macos/make_quick_actions.py            # install / update
    python3 integrations/macos/make_quick_actions.py --uninstall
    python3 integrations/macos/make_quick_actions.py --self-test   # check installed ones
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES = Path.home() / "Library" / "Services"
ACTIONS = {
    "Chart Cleaner – Clean Selection": "--mode clean --no-wrap",
    "Chart Cleaner – Abbreviate Selection": "--mode abbreviations",
    "Chart Cleaner – Expand Selection": "--mode expand",
}


def _document(command: str) -> dict:
    ids = [str(uuid.uuid4()).upper() for _ in range(3)]
    return {
        "AMApplicationBuild": "523", "AMApplicationVersion": "2.10", "AMDocumentVersion": "2",
        "actions": [{"action": {
            "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
            "AMActionVersion": "2.0.3", "AMApplication": ["Automator"],
            "AMParameterProperties": {k: {} for k in
                                      ("COMMAND_STRING", "CheckedForUserDefaultShell", "inputMethod",
                                       "shell", "source")},
            "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
            "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
            "ActionName": "Run Shell Script",
            "ActionParameters": {"COMMAND_STRING": command, "CheckedForUserDefaultShell": True,
                                 "inputMethod": 0, "shell": "/bin/bash", "source": ""},
            "BundleIdentifier": "com.apple.RunShellScript", "CFBundleVersion": "2.0.3",
            "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True,
            "Category": ["AMCategoryUtilities"], "Class Name": "RunShellScriptAction",
            "InputUUID": ids[0], "OutputUUID": ids[1], "UUID": ids[2],
            "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
            "UnlocalizedApplications": ["Automator"], "isViewVisible": 1,
        }, "isViewVisible": 1}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {}, "applicationPaths": [],
            "inputTypeIdentifier": "com.apple.Automator.text",
            "outputTypeIdentifier": "com.apple.Automator.text",
            "presentationMode": 11, "processesInput": 0,
            "serviceInputTypeIdentifier": "com.apple.Automator.text",
            "serviceOutputTypeIdentifier": "com.apple.Automator.text",
            "serviceProcessesInput": 0, "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": 0, "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def _info(name: str) -> dict:
    return {"NSServices": [{"NSMenuItem": {"default": name}, "NSMessage": "runWorkflowAsService",
                            "NSSendTypes": ["public.utf8-plain-text"],
                            "NSReturnTypes": ["public.utf8-plain-text"]}]}


def install(target: Path = SERVICES, root: Path = ROOT) -> list[Path]:
    cli = shlex.quote((root / "clean-chart").as_posix())  # a bash command, so POSIX separators
    made = []
    for name, flags in ACTIONS.items():
        bundle = target / f"{name}.workflow" / "Contents"
        bundle.mkdir(parents=True, exist_ok=True)
        command = f"{cli} --stdin --stdout {flags}"
        (bundle / "document.wflow").write_bytes(plistlib.dumps(_document(command)))
        (bundle / "Info.plist").write_bytes(plistlib.dumps(_info(name)))
        made.append(bundle.parent)
    return made


def uninstall(target: Path = SERVICES) -> int:
    removed = 0
    for name in ACTIONS:
        path = target / f"{name}.workflow"
        if path.exists():
            shutil.rmtree(path)
            removed += 1
    return removed


SAMPLE = "Patient with hypertension and atrial fibrillation.\nMRN: 1234567\n"


def self_test(target: Path = SERVICES, run=subprocess.run, timeout: float = 120) -> list[dict]:
    """Check every installed Quick Action the way macOS would run it.

    For each workflow: the bundle and its two plists parse, the service menu
    entry is present, the ``clean-chart`` it calls exists and is executable,
    and running its exact shell command on a sample selection exits 0 with
    text on stdout. Returns one ``{"name", "ok", "detail"}`` row per action.
    """
    rows = []
    for name in ACTIONS:
        bundle = target / f"{name}.workflow" / "Contents"
        row = {"name": name, "ok": False, "detail": ""}
        rows.append(row)
        try:
            doc = plistlib.loads((bundle / "document.wflow").read_bytes())
            info = plistlib.loads((bundle / "Info.plist").read_bytes())
        except FileNotFoundError:
            row["detail"] = "not installed — run without --self-test first"
            continue
        except Exception as exc:
            row["detail"] = f"workflow files don't parse: {exc}"
            continue
        services = info.get("NSServices") or []
        if not services or services[0].get("NSMenuItem", {}).get("default") != name:
            row["detail"] = "Info.plist has no matching Services menu item"
            continue
        try:
            command = doc["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
        except (KeyError, IndexError, TypeError):
            row["detail"] = "workflow has no shell command"
            continue
        cli = Path(shlex.split(command)[0])
        if not cli.is_file():
            row["detail"] = f"{cli} is missing — re-run the installer from the moved folder"
            continue
        if not os.access(cli, os.X_OK):
            row["detail"] = f"{cli} is not executable (chmod +x it)"
            continue
        try:
            done = run(["/bin/bash", "-c", command], input=SAMPLE, capture_output=True,
                       text=True, timeout=timeout)
        except Exception as exc:
            row["detail"] = f"could not run: {exc}"
            continue
        if done.returncode != 0:
            last = ((done.stderr or done.stdout or "").strip().splitlines() or ["failed"])[-1]
            row["detail"] = f"exit {done.returncode}: {last}"
        elif not (done.stdout or "").strip():
            row["detail"] = "ran but returned no text"
        else:
            row.update(ok=True, detail=f"ok — {len(done.stdout.strip())} characters back")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--self-test", action="store_true",
                        help="run each installed Quick Action's command on a sample and report")
    args = parser.parse_args()
    if args.uninstall:
        print(f"Removed {uninstall()} Quick Action(s).")
        return
    if args.self_test:
        rows = self_test()
        for row in rows:
            print(f"{'✓' if row['ok'] else '✕'} {row['name']}: {row['detail']}")
        raise SystemExit(0 if all(r["ok"] for r in rows) else 1)
    for path in install():
        print(f"Installed {path.name}")
    print("Assign shortcuts in System Settings → Keyboard → Keyboard Shortcuts → Services → Text.")


if __name__ == "__main__":
    main()
