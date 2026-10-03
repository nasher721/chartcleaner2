"""Encryption at rest for files that hold PHI (token maps).

Files are encrypted with Fernet (AES-128-CBC + HMAC, from ``cryptography``,
already installed with presidio-anonymizer). The key never sits next to the
data in plain form where the OS offers a keystore:

* **macOS** — the login Keychain (``security`` CLI, no admin needed).
* **Windows** — a key file protected with DPAPI for the current user.
* **elsewhere / fallback** — ``data/.datakey`` with owner-only permissions.

``CHARTCLEANER_KEY_BACKEND`` (``keychain`` | ``dpapi`` | ``file``) forces a
backend; the tests use ``file``. Copying the data folder to another machine
does not carry the key, so encrypted maps there read as "unreadable".

Encrypted files start with :data:`MAGIC`; anything else is legacy plain text
and is returned as is, so older files keep working until they are migrated.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

__all__ = ["MAGIC", "DecryptError", "backend", "describe", "encrypt", "decrypt",
           "is_encrypted", "write_text", "read_text", "key_dir"]

MAGIC = b"CCENC1:"
KEYCHAIN_SERVICE = "Chart Cleaner"
KEYCHAIN_ACCOUNT = "data-encryption-key"
BACKEND_ENV = "CHARTCLEANER_KEY_BACKEND"

_cache: dict[tuple[str, str], bytes] = {}
_used_backend: dict[str, str] = {}


class DecryptError(Exception):
    """The file is encrypted with a key this machine doesn't have (or is damaged)."""


def key_dir() -> Path:
    from . import store
    return store.DATA_DIR


def backend() -> str:
    forced = os.environ.get(BACKEND_ENV, "").strip().lower()
    if forced in ("keychain", "dpapi", "file"):
        return forced
    if sys.platform == "darwin":
        return "keychain"
    if sys.platform.startswith("win"):
        return "dpapi"
    return "file"


def describe() -> str:
    """Where the key lives, for the Settings page."""
    used = _used_backend.get(str(key_dir())) or backend()
    return {"keychain": "macOS Keychain",
            "dpapi": "a key file protected by Windows (DPAPI) for your user account",
            "file": "a private key file in the data folder (owner-only permissions)"}[used]


# --- key backends ------------------------------------------------------------

def _new_key() -> bytes:
    from cryptography.fernet import Fernet
    return Fernet.generate_key()


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _file_key(directory: Path) -> bytes:
    path = directory / ".datakey"
    if path.is_file():
        return path.read_bytes().strip()
    key = _new_key()
    _write_private(path, key)
    return key


def _keychain_key() -> bytes:
    found = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
        capture_output=True, text=True, timeout=15)
    if found.returncode == 0 and found.stdout.strip():
        return found.stdout.strip().encode("ascii")
    key = _new_key()
    added = subprocess.run(
        ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
         "-U", "-w", key.decode("ascii")],
        capture_output=True, text=True, timeout=15)
    if added.returncode != 0:
        raise OSError(added.stderr.strip() or "security add-generic-password failed")
    return key


def _dpapi(data: bytes, protect: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_key(directory: Path) -> bytes:  # pragma: no cover - Windows only
    path = directory / ".datakey.dpapi"
    if path.is_file():
        return _dpapi(path.read_bytes(), protect=False).strip()
    key = _new_key()
    _write_private(path, _dpapi(key, protect=True))
    return key


def _key() -> bytes:
    directory = key_dir()
    wanted = backend()
    cache_key = (wanted, str(directory))
    if cache_key in _cache:
        return _cache[cache_key]
    used = wanted
    try:
        if wanted == "keychain":
            key = _keychain_key()
        elif wanted == "dpapi":
            key = _dpapi_key(directory)
        else:
            key = _file_key(directory)
    except Exception:
        used = "file"  # keystore unavailable (locked, headless): stay usable
        key = _file_key(directory)
    base64.urlsafe_b64decode(key)  # fail early on a damaged key
    _cache[cache_key] = key
    _used_backend[str(directory)] = used
    return key


# --- data ----------------------------------------------------------------------

def is_encrypted(data: bytes) -> bool:
    return data.startswith(MAGIC)


def encrypt(data: bytes) -> bytes:
    from cryptography.fernet import Fernet
    return MAGIC + Fernet(_key()).encrypt(data)


def decrypt(data: bytes) -> bytes:
    if not is_encrypted(data):
        return data
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(_key()).decrypt(data[len(MAGIC):])
    except (InvalidToken, ValueError) as e:
        raise DecryptError("encrypted with a key this machine doesn't have") from e


def write_text(path: str | Path, text: str) -> None:
    """Encrypt ``text`` into ``path`` (owner-only permissions, atomic replace)."""
    _write_private(Path(path), encrypt(text.encode("utf-8")))


def read_text(path: str | Path) -> str:
    """Read an encrypted (or legacy plain-text) file."""
    return decrypt(Path(path).read_bytes()).decode("utf-8")
