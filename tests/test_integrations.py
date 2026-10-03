"""Hotkey integrations: CLI pipe mode and the macOS Quick Action generator."""

import importlib.util
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cli(*args: str, stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "medical_cleaner.py"), *args],
                          input=stdin, capture_output=True, text=True, cwd=ROOT, timeout=120)


def test_stdin_stdout_writes_only_the_result():
    out = _cli("--stdin", "--stdout", "--mode", "abbreviations", stdin="Hypertension noted.\n")
    assert out.returncode == 0
    assert out.stdout == "Hypertension noted.\n".replace("Hypertension", "HTN")
    assert "chars" in out.stderr


def test_stdin_stdout_expand_and_empty_input():
    assert _cli("--stdin", "--stdout", "--mode", "expand", stdin="Pt w/ HTN").stdout == "Pt with hypertension"
    assert _cli("--stdin", "--stdout", stdin="   ").returncode == 1


def test_unknown_preset_is_a_clear_error():
    out = _cli("--stdin", "--stdout", "--preset", "no-such-preset", stdin="x")
    assert out.returncode == 1 and "no preset" in out.stderr


def _quick_actions():
    spec = importlib.util.spec_from_file_location(
        "make_quick_actions", ROOT / "integrations" / "macos" / "make_quick_actions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quick_actions_are_valid_services_calling_clean_chart(tmp_path):
    qa = _quick_actions()
    made = qa.install(tmp_path, root=Path("/Users/me/Chart Cleaner"))
    assert len(made) == 3
    for bundle in made:
        doc = plistlib.loads((bundle / "Contents" / "document.wflow").read_bytes())
        info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
        command = doc["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
        assert command.startswith("'/Users/me/Chart Cleaner/clean-chart' --stdin --stdout --mode ")
        assert doc["workflowMetaData"]["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"
        assert info["NSServices"][0]["NSReturnTypes"] == ["public.utf8-plain-text"]
    assert qa.uninstall(tmp_path) == 3 and not list(tmp_path.iterdir())
