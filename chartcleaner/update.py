"""Small, privacy-safe release client for the companion updater.

The client only handles public release metadata and staged bytes.  It never
receives chart text or user-data paths.  Native signature checks are mandatory
for the default production verifier; tests may inject a verifier/transport.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping

MANIFEST_FIELDS = {"version", "minimum_supported_version", "notes_url", "platforms"}
ASSET_FIELDS = {"url", "sha256", "size"}
SUPPORTED_PLATFORMS = {"macos-arm64", "windows-x64"}
CHECK_INTERVAL = 24 * 60 * 60
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
_METADATA_HOSTS = {"github.com"}
_ASSET_HOSTS = _METADATA_HOSTS | {"objects.githubusercontent.com", "release-assets.githubusercontent.com", "githubusercontent.com"}


class UpdateError(ValueError):
    """A safe, generic update failure suitable for local diagnostics."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...] = ()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if not self.prerelease else base + "-" + ".".join(map(str, self.prerelease))

    @classmethod
    def parse(cls, value: str) -> "Version":
        if not isinstance(value, str):
            raise UpdateError("invalid_version")
        match = _SEMVER.fullmatch(value)
        if not match:
            raise UpdateError("invalid_version")
        if match.group(4) and any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in match.group(4).split(".")):
            raise UpdateError("invalid_version")
        pre = tuple(int(x) if x.isdigit() else x for x in match.group(4).split(".")) if match.group(4) else ()
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), pre)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        base = (self.major, self.minor, self.patch)
        other_base = (other.major, other.minor, other.patch)
        if base != other_base:
            return base < other_base
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not bool(other.prerelease)
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if isinstance(left, int) and isinstance(right, str):
                return True
            if isinstance(left, str) and isinstance(right, int):
                return False
            return left < right
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class ReleaseAsset:
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ReleaseManifest:
    version: Version
    minimum_supported_version: Version
    notes_url: str
    platforms: Mapping[str, ReleaseAsset]

    @classmethod
    def parse(cls, payload: str | bytes | Mapping[str, object]) -> "ReleaseManifest":
        try:
            obj = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateError("malformed_manifest") from exc
        if not isinstance(obj, dict) or set(obj) != MANIFEST_FIELDS or not isinstance(obj["platforms"], dict):
            raise UpdateError("malformed_manifest")
        version = Version.parse(obj["version"])
        minimum = Version.parse(obj["minimum_supported_version"])
        if version < minimum or not _is_github_https(obj["notes_url"], asset=False):
            raise UpdateError("invalid_manifest")
        assets: dict[str, ReleaseAsset] = {}
        for name, raw in obj["platforms"].items():
            if name not in SUPPORTED_PLATFORMS or not isinstance(raw, dict) or set(raw) != ASSET_FIELDS:
                raise UpdateError("invalid_asset")
            url, digest, size = raw["url"], raw["sha256"], raw["size"]
            if not _is_github_https(url) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise UpdateError("invalid_asset")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise UpdateError("invalid_asset")
            assets[name] = ReleaseAsset(url, digest.lower(), size)
        return cls(version, minimum, str(obj["notes_url"]), assets)


def _is_github_https(value: object, *, asset: bool = True) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urllib.parse.urlparse(value)
        hosts = _ASSET_HOSTS if asset else _METADATA_HOSTS
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.hostname in hosts and parsed.port in (None, 443)
                and not parsed.netloc.endswith(":") and bool(parsed.path)
                and not any(char.isspace() or ord(char) < 32 for char in value))
    except ValueError:
        return False


def _validate_response_url(response: BinaryIO, requested_url: str, *, asset: bool = True) -> None:
    """Reject redirects that leave the pinned HTTPS/GitHub trust boundary."""
    geturl = getattr(response, "geturl", None)
    final_url = geturl() if callable(geturl) else requested_url
    if not _is_github_https(final_url, asset=asset):
        raise UpdateError("untrusted_redirect")


def current_platform(system: str | None = None, machine: str | None = None) -> str:
    system, machine = (system or sys.platform).lower(), (machine or platform.machine()).lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system.startswith("win") and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    raise UpdateError("unsupported_platform")


def safe_extract_archive(archive: Path, destination: Path) -> None:
    """Extract an archive, preserving only relative in-bundle symlinks."""
    destination = destination.resolve()
    existed = destination.exists()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            links: list[tuple[Path, str, int]] = []
            for info in zf.infolist():
                raw_name = info.filename.replace("\\", "/")
                target = (destination / raw_name).resolve()
                if target != destination and destination not in target.parents:
                    raise UpdateError("unsafe_archive")
                mode = (info.external_attr >> 16) & 0o177777
                file_type = mode & 0o170000
                if file_type == 0o120000:
                    link_text = zf.read(info).decode("utf-8")
                    win_target = link_text.replace("\\", "/")
                    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(raw_name), win_target))
                    if ("\x00" in link_text or win_target.startswith("/") or re.match(r"^[A-Za-z]:", win_target) or normalized == ".." or normalized.startswith("../")):
                        raise UpdateError("unsafe_archive")
                    links.append((destination / raw_name, link_text, mode & 0o7777))
                    continue
                if info.is_dir() or file_type == 0o040000:
                    target.mkdir(parents=True, exist_ok=True)
                    if mode:
                        os.chmod(target, mode & 0o7777)
                    continue
                if file_type not in (0, 0o100000):
                    raise UpdateError("unsafe_archive")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
                os.chmod(target, mode & 0o7777 or 0o600)
            for target, link_text, _mode in links:
                # Never create an entry through a previously created link. All
                # links are deferred, so ordinary files already have real parents.
                if any(parent.is_symlink() for parent in target.parents
                       if parent != destination and destination in parent.parents):
                    raise UpdateError("unsafe_archive")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link_text)
            for target, _link_text, _mode in links:
                try:
                    resolved = target.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise UpdateError("unsafe_archive") from exc
                if resolved != destination and destination not in resolved.parents:
                    raise UpdateError("unsafe_archive")
    except Exception:
        if not existed:
            shutil.rmtree(destination, ignore_errors=True)
        raise


def _archive_expanded_size(archive: Path) -> int:
    try:
        with zipfile.ZipFile(archive) as zf:
            total = 0
            for info in zf.infolist():
                if info.file_size < 0 or total > (1 << 63) - 1 - info.file_size:
                    raise UpdateError("archive_too_large")
                total += info.file_size
            return total
    except UpdateError:
        raise
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise UpdateError("invalid_archive") from exc


def _signature_targets(root: Path, platform_name: str) -> tuple[Path, ...]:
    if platform_name == "macos-arm64":
        targets = tuple(sorted(p for p in root.rglob("*.app") if p.is_dir()))
    else:
        targets = tuple(sorted(p for p in root.rglob("*.exe") if p.is_file()))
    if not targets:
        raise UpdateError("signed_target_missing")
    return targets


def verify_native_signature(path: Path, trusted_publisher_identity: str) -> None:
    if not trusted_publisher_identity:
        raise UpdateError("missing_trusted_publisher")
    try:
        if sys.platform == "darwin":
            # Hex-encoded UTF-8 is a literal requirement value, so certificate
            # names never become syntax (including quotes and backslashes).
            requirement = (
                '=anchor apple generic '
                'and certificate 1[field.1.2.840.113635.100.6.2.6] exists '
                'and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
                'and certificate leaf[subject.CN] = 0x'
                + trusted_publisher_identity.encode("utf-8").hex()
            )
            commands = [
                ["codesign", "--verify", "--deep", "--strict", "--test-requirement", requirement, str(path)],
                ["spctl", "--assess", "--type", "execute", str(path)],
            ]
            for command in commands:
                subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        elif sys.platform.startswith("win"):
            # Encode data separately: -Command's trailing arguments are parsed as
            # more PowerShell code, and paths/publishers are never code.
            payload = base64.b64encode(json.dumps({
                "path": str(path), "publisher": trusted_publisher_identity,
            }).encode("utf-8")).decode("ascii")
            script = (
                "$ErrorActionPreference='Stop'; "
                "$inputData=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
                + payload + "')) | ConvertFrom-Json; "
                "$s=Get-AuthenticodeSignature -LiteralPath $inputData.path; "
                "if ($s.Status -ne 'Valid' -or $null -eq $s.SignerCertificate "
                "-or $s.SignerCertificate.Subject -cne $inputData.publisher) { exit 1 }"
            )
            encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            raise UpdateError("unsupported_platform")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise UpdateError("signature_invalid") from exc


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_github_https(newurl):
            raise UpdateError("untrusted_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_transport(url: str, timeout: float) -> BinaryIO:
    if not _is_github_https(url):
        raise UpdateError("invalid_url")
    opener = urllib.request.build_opener(_PinnedRedirectHandler())
    response = opener.open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout)
    try:
        _validate_response_url(response, url)
    except Exception:
        response.close()
        raise
    return response


@dataclass(frozen=True)
class UpdateStatus:
    state: str
    code: str | None = None
    checked_at: float | None = None
    version: str | None = None
    platform: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"state": self.state, "code": self.code, "checked_at": self.checked_at, "version": self.version, "platform": self.platform}


class UpdateClient:
    def __init__(self, current_version: str, manifest_url: str, *, transport: Callable[[str, float], BinaryIO] | None = None, trusted_publisher_identity: str = "", platform_name: str | None = None, now: Callable[[], float] = time.time):
        self.current = Version.parse(current_version)
        if not _is_github_https(manifest_url, asset=False):
            raise UpdateError("invalid_url")
        self.manifest_url = manifest_url
        self.transport = transport or _default_transport
        self.trusted_publisher_identity = trusted_publisher_identity
        self.platform = platform_name or current_platform()
        if self.platform not in SUPPORTED_PLATFORMS:
            raise UpdateError("unsupported_platform")
        self.now = now

    def check(self, *, automatic: bool = False, last_checked: float | None = None, force: bool = False, timeout: float = 15.0) -> tuple[ReleaseManifest | None, UpdateStatus]:
        checked = self.now()
        if automatic and not force and last_checked is not None and checked - last_checked < CHECK_INTERVAL:
            return None, UpdateStatus("throttled", "check_throttled", checked, None, self.platform)
        try:
            with self.transport(self.manifest_url, timeout) as response:
                _validate_response_url(response, self.manifest_url)
                manifest = ReleaseManifest.parse(response.read())
            if manifest.version < self.current:
                raise UpdateError("downgrade_rejected")
            if self.current < manifest.minimum_supported_version:
                raise UpdateError("minimum_version_incompatible")
            if self.platform not in manifest.platforms:
                raise UpdateError("platform_asset_missing")
            state = "update_available" if self.current < manifest.version else "current"
            return manifest, UpdateStatus(state, None, checked, str(manifest.version), self.platform)
        except UpdateError as exc:
            return None, UpdateStatus("error", exc.code, checked, None, self.platform)
        except Exception:
            return None, UpdateStatus("error", "network_error", checked, None, self.platform)

    def stage(self, asset: ReleaseAsset, staging_dir: Path, *, rollback_size: int | None = None, free_space: Callable[[Path], int] | None = None, timeout: float = 60.0) -> tuple[Path, Path]:
        """Return ``(archive_path, extracted_root)`` for companion-updater handoff.

        The companion updater must treat the path as untrusted input and
        independently recheck its expected byte count, SHA-256, archive paths,
        and native signature immediately before replacement. It receives only
        this path, the expected release version, installed app path, and PID;
        it never opens the user-data directory.
        """
        target: Path | None = None
        transaction: Path | None = None
        digest = hashlib.sha256()
        count = 0
        try:
            if not _is_github_https(asset.url):
                raise UpdateError("invalid_url")
            if isinstance(asset.size, bool) or not isinstance(asset.size, int) or asset.size <= 0:
                raise UpdateError("invalid_asset")
            if not isinstance(asset.sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", asset.sha256):
                raise UpdateError("invalid_asset")
            if rollback_size is None or isinstance(rollback_size, bool) or not isinstance(rollback_size, int) or rollback_size <= 0:
                raise UpdateError("disk_context_required")
            staging_dir.mkdir(parents=True, exist_ok=True)
            usage = (free_space or (lambda p: shutil.disk_usage(p).free))(staging_dir)
            required = asset.size + rollback_size
            if usage < required:
                raise UpdateError("insufficient_disk_space")
            transaction = Path(tempfile.mkdtemp(prefix="update-", dir=staging_dir))
            fd, name = tempfile.mkstemp(prefix="archive-", suffix=".part", dir=transaction)
            os.close(fd)
            target = Path(name)
            with self.transport(asset.url, timeout) as response, target.open("wb") as output:
                _validate_response_url(response, asset.url)
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > asset.size:
                        raise UpdateError("size_mismatch")
                    digest.update(chunk)
                    output.write(chunk)
            if count != asset.size or digest.hexdigest() != asset.sha256:
                raise UpdateError("checksum_mismatch")
            verified = target.with_suffix("")
            target.replace(verified)
            expanded_size = _archive_expanded_size(verified)
            usage = (free_space or (lambda p: shutil.disk_usage(p).free))(staging_dir)
            if usage < asset.size + expanded_size + rollback_size:
                raise UpdateError("insufficient_disk_space")
            extracted = transaction / "extracted"
            safe_extract_archive(verified, extracted)
            for signed_target in _signature_targets(extracted, self.platform):
                if not self.trusted_publisher_identity:
                    raise UpdateError("missing_trusted_publisher")
                verify_native_signature(signed_target, self.trusted_publisher_identity)
            return verified, extracted
        except UpdateError:
            if transaction is not None:
                shutil.rmtree(transaction, ignore_errors=True)
            elif target is not None:
                target.unlink(missing_ok=True)
            raise
        except Exception as exc:
            if transaction is not None:
                shutil.rmtree(transaction, ignore_errors=True)
            elif target is not None:
                target.unlink(missing_ok=True)
            raise UpdateError("staging_failed") from exc
