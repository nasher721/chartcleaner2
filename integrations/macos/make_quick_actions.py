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
"""

from __future__ import annotations

import argparse
import plistlib
import shlex
import shutil
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
    cli = shlex.quote(str(root / "clean-chart"))
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.uninstall:
        print(f"Removed {uninstall()} Quick Action(s).")
        return
    for path in install():
        print(f"Installed {path.name}")
    print("Assign shortcuts in System Settings → Keyboard → Keyboard Shortcuts → Services → Text.")


if __name__ == "__main__":
    main()
