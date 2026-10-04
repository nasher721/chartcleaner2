"""Doctor: check everything Chart Cleaner depends on, with a fix for each problem.

One list of checks for the /doctor page and ``clean-chart --doctor``:
Python and the virtual environment, required packages, the spaCy model (and
optionally that the NLP engine actually loads), the local AI (Ollama), the
encryption key store, config.json, the local API token, the command-line and
MCP launchers, the data folder, and update signing.

Each :class:`Check` is ``ok`` / ``warn`` / ``fail`` / ``info`` with a plain
sentence and, when something can be repaired from here, a ``fix`` id that
:func:`apply_fix` knows. Nothing here sends chart text anywhere.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from . import store

__all__ = ["Check", "run_checks", "apply_fix", "FIXES", "summary", "ROOT"]

ROOT = Path(__file__).resolve().parent.parent
SPACY_MODEL_URL = ("https://github.com/explosion/spacy-models/releases/download/"
                   "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl")
REQUIRED = {"nicegui": "the app window", "presidio_analyzer": "name/phone redaction",
            "presidio_anonymizer": "name/phone redaction", "cryptography": "encrypted storage",
            "pyperclip": "clipboard", "fitz": "PDF files (pymupdf)", "watchdog": "folder watcher",
            "mcp": "Claude MCP server", "thefuzz": "duplicate-note folding"}


@dataclass
class Check:
    id: str
    label: str
    status: str          # "ok" | "warn" | "fail" | "info"
    detail: str
    fix: str = ""        # FIXES key, when the app can repair it
    fix_label: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _python() -> Check:
    v = sys.version_info
    ok = v >= (3, 10)
    return Check("python", "Python", "ok" if ok else "fail",
                 f"Python {v.major}.{v.minor}.{v.micro}" + ("" if ok else " — Chart Cleaner needs 3.10 or newer"))


def _venv() -> Check:
    inside = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    frozen = bool(getattr(sys, "frozen", False))
    if inside or frozen:
        return Check("venv", "Isolated environment", "ok",
                     "Running from the installed app" if frozen else f"Virtual environment at {sys.prefix}")
    return Check("venv", "Isolated environment", "warn",
                 "Not running inside the app's virtual environment — run install.sh / install.bat "
                 "and start the app with its launcher")


def _packages() -> list[Check]:
    out = []
    for module, purpose in REQUIRED.items():
        found = find_spec(module) is not None
        out.append(Check(f"pkg:{module}", f"Package {module}", "ok" if found else "fail",
                         f"Installed ({purpose})" if found else
                         f"Missing — needed for {purpose}. Run the installer again.",
                         "" if found else "reinstall", "" if found else "Reinstall packages"))
    return out


def _spacy_model(deep: bool) -> list[Check]:
    if find_spec("en_core_web_sm") is None:
        return [Check("spacy_model", "spaCy model en_core_web_sm", "fail",
                      "Not installed — name redaction (NLP) is skipped until it is",
                      "install_spacy_model", "Install the model")]
    checks = [Check("spacy_model", "spaCy model en_core_web_sm", "ok", "Installed")]
    if deep:
        try:
            from .stages import _PRESIDIO_CACHE, DEFAULT_NLP_ENTITIES
            _PRESIDIO_CACHE.get_engines(tuple(sorted(DEFAULT_NLP_ENTITIES.items())), frozenset(), None)
            checks.append(Check("nlp_engine", "NLP engine loads", "ok", "Presidio + spaCy started"))
        except Exception as exc:
            checks.append(Check("nlp_engine", "NLP engine loads", "fail", str(exc)))
    return checks


def _local_ai(cfg: dict) -> Check:
    from .local_llm import health
    from .summarizer import merge_llm_config
    h = health(str(merge_llm_config(cfg)["base_url"]))
    if not h["loopback"]:
        return Check("ollama", "Local AI (Ollama)", "fail", h["error"])
    if not h["reachable"]:
        return Check("ollama", "Local AI (Ollama)", "info",
                     "Not running — optional; install from ollama.com for summaries and Q&A")
    if not h["models"]:
        return Check("ollama", "Local AI (Ollama)", "warn", h["error"], "pull_model",
                     f"Pull {h['recommended']}")
    names = ", ".join(m.get("name", "") for m in h["models"][:4])
    return Check("ollama", "Local AI (Ollama)", "ok", f"Ollama {h['version']} with {names}")


def _encryption() -> Check:
    from . import secure_store
    try:
        token = secure_store.encrypt(b"chart-cleaner-doctor")
        assert secure_store.decrypt(token) == b"chart-cleaner-doctor"
        return Check("encryption", "Encrypted storage", "ok", secure_store.describe())
    except Exception as exc:
        return Check("encryption", "Encrypted storage", "fail",
                     f"Could not encrypt/decrypt ({type(exc).__name__}: {exc}) — token maps and "
                     "recent charts can't be saved")


def _config() -> tuple[Check, dict]:
    from .engine import ConfigError, load_config, validate_config
    try:
        cfg = load_config(store.CONFIG_PATH)
    except ConfigError as exc:
        return Check("config", "Rules (config.json)", "fail", str(exc), "restore_config",
                     "Restore the last backup"), {}
    errors, warnings = validate_config(cfg)
    if errors:
        return Check("config", "Rules (config.json)", "fail",
                     f"{len(errors)} error(s): {errors[0]}", "restore_config", "Restore the last backup"), cfg
    if warnings:
        return Check("config", "Rules (config.json)", "warn",
                     f"{len(warnings)} warning(s): {warnings[0]}"), cfg
    return Check("config", "Rules (config.json)", "ok", "Valid"), cfg


def _api_token() -> Check:
    from .api import token_path
    path = token_path()
    if not path.exists():
        return Check("api_token", "Local API token", "info",
                     "Not created yet (made on first use of the local API)", "create_api_token",
                     "Create it now")
    if os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return Check("api_token", "Local API token", "warn",
                         f"Readable by other users (mode {oct(mode)})", "fix_token_permissions",
                         "Make it private")
    return Check("api_token", "Local API token", "ok", "Present and private")


def _launchers() -> list[Check]:
    out = []
    names = (("clean-chart.cmd", "clean-chart-mcp.cmd") if os.name == "nt"
             else ("clean-chart", "clean-chart-mcp"))
    for name in names:
        path = ROOT / name
        cid = f"launcher:{name}"
        if not path.exists():
            out.append(Check(cid, f"Launcher {name}", "warn", f"{path} is missing"))
        elif os.name != "nt" and not os.access(path, os.X_OK):
            out.append(Check(cid, f"Launcher {name}", "fail", "Not executable",
                             "chmod_launchers", "Make launchers executable"))
        else:
            out.append(Check(cid, f"Launcher {name}", "ok", str(path)))
    return out


def _data_dir() -> Check:
    try:
        store.ensure_dirs()
        probe = store.DATA_DIR / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("data_dir", "Data folder", "fail", f"{store.DATA_DIR} is not writable ({exc})",
                     "create_data_dirs", "Create the folders")
    free = shutil.disk_usage(store.DATA_DIR).free
    gb = free / 1e9
    if gb < 0.5:
        return Check("data_dir", "Data folder", "warn", f"{store.DATA_DIR} — only {gb:.1f} GB free")
    return Check("data_dir", "Data folder", "ok", f"{store.DATA_DIR} ({gb:.0f} GB free)")


def _update_signing() -> Check:
    from . import release_identity
    from .update import current_platform
    try:
        plat = current_platform()
    except Exception:
        return Check("update_signing", "Update signing", "info",
                     "Self-update is only offered on macOS and Windows builds")
    publisher = (release_identity.MACOS_PUBLISHER if plat == "macos-arm64"
                 else release_identity.WINDOWS_PUBLISHER)
    if publisher:
        return Check("update_signing", "Update signing", "ok",
                     f"Updates must be signed by {publisher} (checked before install)")
    return Check("update_signing", "Update signing", "info",
                 "Source install: no publisher identity, so self-update is refused — update with git "
                 "or a signed release")


def run_checks(*, deep: bool = False, ai: bool = True) -> list[Check]:
    """Every check, in the order the page shows them. ``deep`` also loads the NLP engine."""
    config_check, cfg = _config()
    checks = [_python(), _venv(), *_packages(), *_spacy_model(deep), config_check, _encryption(),
              _data_dir(), _api_token(), *_launchers(), _update_signing()]
    if ai:
        checks.insert(len(checks) - 1, _local_ai(cfg))
    return checks


def summary(checks: list[Check]) -> str:
    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    if not fails and not warns:
        return f"All {len(checks)} checks passed"
    return f"{fails} problem(s), {warns} warning(s) in {len(checks)} checks"


# --- fixes --------------------------------------------------------------------

def _fix_token() -> str:
    from .api import get_token
    get_token()
    return "Local API token created."


def _fix_token_permissions() -> str:
    from .api import token_path
    os.chmod(token_path(), 0o600)
    return "Token file is now readable only by you."


def _fix_launchers() -> str:
    for name in ("clean-chart", "clean-chart-mcp", "run-app.command"):
        path = ROOT / name
        if path.exists():
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return "Launchers are executable."


def _fix_dirs() -> str:
    store.ensure_dirs()
    return "Data folders created."


def _pip(*args: str) -> str:
    done = subprocess.run([sys.executable, "-m", "pip", *args], capture_output=True, text=True,
                          timeout=1800)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["pip failed"]
        raise RuntimeError(tail[0])
    return "Done."


def _fix_spacy() -> str:
    _pip("install", SPACY_MODEL_URL)
    return "spaCy model installed — restart the app to use name redaction."


def _fix_reinstall() -> str:
    _pip("install", "-r", str(ROOT / "requirements.txt"))
    return "Packages installed — restart the app."


def _fix_restore_config() -> str:
    backups = store.list_config_backups()
    if not backups:
        raise RuntimeError("No config backup to restore")
    ok, msg = store.restore_config_backup(backups[0]["file"])
    if not ok:
        raise RuntimeError(msg)
    return msg


def _fix_pull() -> str:
    from .engine import load_config
    from .local_llm import RECOMMENDED_MODEL, LocalLlmClient
    from .summarizer import merge_llm_config
    url = str(merge_llm_config(load_config(store.CONFIG_PATH))["base_url"])
    if not LocalLlmClient(url, timeout=30.0).pull(RECOMMENDED_MODEL):
        raise RuntimeError("Ollama did not report success")
    return f"{RECOMMENDED_MODEL} pulled."


FIXES = {
    "create_api_token": _fix_token,
    "fix_token_permissions": _fix_token_permissions,
    "chmod_launchers": _fix_launchers,
    "create_data_dirs": _fix_dirs,
    "install_spacy_model": _fix_spacy,
    "reinstall": _fix_reinstall,
    "restore_config": _fix_restore_config,
    "pull_model": _fix_pull,
}


def apply_fix(fix_id: str) -> tuple[bool, str]:
    """Run one repair; ``(ok, message)`` and never raises."""
    fn = FIXES.get(fix_id)
    if fn is None:
        return False, f"No automatic fix for {fix_id!r}"
    try:
        return True, fn()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
