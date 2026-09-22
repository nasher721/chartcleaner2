import base64
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile

import pytest
import chartcleaner.update as update

from chartcleaner.update import (
    ReleaseManifest,
    UpdateClient,
    UpdateError,
    Version,
    current_platform,
    safe_extract_archive,
    verify_native_signature as native_verify,
)


MANIFEST = {
    "version": "2.4.0",
    "minimum_supported_version": "2.3.0",
    "notes_url": "https://github.com/nasher721/chartcleaner2/releases/tag/v2.4.0",
    "platforms": {
        "macos-arm64": {"url": "https://github.com/nasher721/chartcleaner2/releases/download/v2.4.0/app.zip", "sha256": "a" * 64, "size": 3},
        "windows-x64": {"url": "https://github.com/nasher721/chartcleaner2/releases/download/v2.4.0/app.zip", "sha256": "a" * 64, "size": 3},
    },
}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class RedirectResponse(Response):
    def __init__(self, payload: bytes, final_url: str):
        super().__init__(payload)
        self.final_url = final_url

    def geturl(self):
        return self.final_url


@pytest.fixture(autouse=True)
def native_signature_fixture(monkeypatch):
    monkeypatch.setattr(update, "verify_native_signature", lambda *_: None)


def archive_payload():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        info = zipfile.ZipInfo("Chart Cleaner.exe")
        info.external_attr = 0o100755 << 16
        zf.writestr(info, b"signed executable")
    return output.getvalue()


def test_manifest_and_semver_validation():
    parsed = ReleaseManifest.parse(json.dumps(MANIFEST))
    assert str(parsed.version.major) == "2"
    assert Version.parse("2.3.0") < Version.parse("2.4.0")
    with pytest.raises(UpdateError):
        ReleaseManifest.parse({**MANIFEST, "platforms": {"linux-x64": MANIFEST["platforms"]["macos-arm64"]}})
    with pytest.raises(UpdateError):
        ReleaseManifest.parse({**MANIFEST, "notes_url": "http://github.com/x"})
    with pytest.raises(UpdateError, match="invalid_version"):
        Version.parse("2.4.0-01")
    for invalid in ("2.4.0-alpha.", "2.4.0-alpha..1", "2.4.0-.alpha", "2.4.0+build..1",
                    "12٢.3.4", "1.2٢.3", "1.2.3٢"):
        with pytest.raises(UpdateError, match="invalid_version"):
            Version.parse(invalid)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"version": "2.3.0"}, "downgrade_rejected"),
        ({"minimum_supported_version": "2.4.1"}, "minimum_version_incompatible"),
        ({"platforms": {"windows-x64": MANIFEST["platforms"]["windows-x64"]}}, "platform_asset_missing"),
    ],
)
def test_check_rejects_release_compatibility_failures(change, code):
    payload = {**MANIFEST, **change}
    if code == "minimum_version_incompatible":
        payload["version"] = "2.5.0"
    client = UpdateClient(
        "2.4.0",
        "https://github.com/example/repo/releases/latest/download/update-manifest.json",
        transport=lambda *_: Response(json.dumps(payload).encode()),
        platform_name="macos-arm64",
    )
    manifest, status = client.check()
    assert manifest is None and status.code == code


@pytest.mark.parametrize(
    "field_value",
    [
        {"url": "http://github.com/a", "sha256": "a" * 64, "size": 1},
        {"url": "https://github.com/a", "sha256": "short", "size": 1},
        {"url": "https://github.com/a", "sha256": "a" * 64, "size": 0},
    ],
)
def test_manifest_rejects_invalid_asset_fields(field_value):
    payload = {**MANIFEST, "platforms": {"macos-arm64": field_value}}
    with pytest.raises(UpdateError, match="invalid_asset"):
        ReleaseManifest.parse(payload)


def test_malformed_url_is_generic():
    for url in ("https://[bad", "https://github.com:bad/file", "https://github.com:65536/file",
                "https://github.com:80/file", "https://github.com:/file", "https://github.com/\nfile"):
        with pytest.raises(UpdateError, match="invalid_asset"):
            ReleaseManifest.parse({**MANIFEST, "platforms": {"macos-arm64": {**MANIFEST["platforms"]["macos-arm64"], "url": url}}})


def test_platform_selection_is_exact():
    assert current_platform("darwin", "arm64") == "macos-arm64"
    assert current_platform("win32", "AMD64") == "windows-x64"
    with pytest.raises(UpdateError):
        current_platform("darwin", "x86_64")


def test_check_throttles_automatic_and_keeps_diagnostics_private():
    now = lambda: 100000.0
    client = UpdateClient("2.3.0", "https://github.com/example/repo/releases/latest/download/update-manifest.json", transport=lambda *_: Response(json.dumps(MANIFEST).encode()), platform_name="macos-arm64", now=now)
    manifest, status = client.check(automatic=True, last_checked=99999.0)
    assert manifest is None and status.code == "check_throttled"
    manifest, status = client.check(automatic=True, last_checked=0.0)
    assert manifest and status.state == "update_available"
    assert "chart" not in json.dumps(status.as_dict()).lower()


def test_manifest_release_cdn_redirect_is_allowed():
    class ManifestResponse(Response):
        def geturl(self):
            return "https://release-assets.githubusercontent.com/release/update-manifest.json?token=fixture"

    client = UpdateClient(
        "2.3.0",
        "https://github.com/example/repo/releases/latest/download/update-manifest.json",
        transport=lambda *_: ManifestResponse(json.dumps(MANIFEST).encode()),
        platform_name="macos-arm64",
    )
    manifest, status = client.check()
    assert manifest is not None and status.state == "update_available"


def test_stage_verifies_size_hash_and_cleans_failures(tmp_path):
    payload = archive_payload()
    asset = {**MANIFEST["platforms"]["macos-arm64"], "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
    client = UpdateClient("2.3.0", "https://github.com/example/repo/releases/latest/download/update-manifest.json", transport=lambda *_: Response(payload), trusted_publisher_identity="Developer ID", platform_name="windows-x64")
    staged, extracted = client.stage(type("Asset", (), asset)(), tmp_path, rollback_size=10, free_space=lambda _: 10000)
    assert staged.read_bytes() == payload and (extracted / "Chart Cleaner.exe").stat().st_mode & 0o100
    bad = type("Asset", (), {**asset, "sha256": "b" * 64})()
    with pytest.raises(UpdateError, match="checksum_mismatch"):
        client.stage(bad, tmp_path, rollback_size=10, free_space=lambda _: 10000)
    assert list(tmp_path.glob("*.part")) == []


def test_stage_rejects_insufficient_space(tmp_path):
    asset = type("Asset", (), MANIFEST["platforms"]["macos-arm64"])()
    client = UpdateClient("2.3.0", "https://github.com/example/repo/releases/latest/download/update-manifest.json", transport=lambda *_: Response(b"abc"), trusted_publisher_identity="Developer ID", platform_name="macos-arm64")
    with pytest.raises(UpdateError, match="insufficient_disk_space"):
        client.stage(asset, tmp_path, rollback_size=10, free_space=lambda _: 2)


def test_redirect_outside_github_boundary_is_rejected_and_cleaned(tmp_path):
    client = UpdateClient(
        "2.3.0",
        "https://github.com/example/repo/releases/latest/download/update-manifest.json",
        transport=lambda *_: RedirectResponse(b"abc", "https://example.invalid/payload"),
        trusted_publisher_identity="Developer ID",
        platform_name="macos-arm64",
    )
    asset = type("Asset", (), MANIFEST["platforms"]["macos-arm64"])()
    with pytest.raises(UpdateError, match="untrusted_redirect"):
        client.stage(asset, tmp_path, rollback_size=10, free_space=lambda _: 100)
    assert not list(tmp_path.iterdir())


def test_real_http_redirect_rejected_before_second_request():
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/evil")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = __import__("urllib.request", fromlist=["build_opener"]).build_opener(update._PinnedRedirectHandler())
        with pytest.raises(UpdateError, match="untrusted_redirect"):
            opener.open(f"http://127.0.0.1:{server.server_port}/start", timeout=2)
        assert hits == ["/start"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_release_asset_redirect_host_is_allowed(tmp_path):
    payload = archive_payload()
    asset = type("Asset", (), {**MANIFEST["platforms"]["windows-x64"], "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})()
    client = UpdateClient(
        "2.3.0",
        "https://github.com/example/repo/releases/latest/download/update-manifest.json",
        transport=lambda *_: RedirectResponse(payload, "https://release-assets.githubusercontent.com/release/app.zip?token=fixture"),
        trusted_publisher_identity="Publisher",
        platform_name="windows-x64",
    )
    archive, extracted = client.stage(asset, tmp_path, rollback_size=10, free_space=lambda _: 10000)
    assert archive.exists() and (extracted / "Chart Cleaner.exe").exists()


def test_unexpected_staging_errors_are_generic(tmp_path):
    client = UpdateClient(
        "2.3.0",
        "https://github.com/example/repo/releases/latest/download/update-manifest.json",
        transport=lambda *_: (_ for _ in ()).throw(OSError("/private/chart-data/config.json")),
        trusted_publisher_identity="Developer ID",
        platform_name="macos-arm64",
    )
    asset = type("Asset", (), MANIFEST["platforms"]["macos-arm64"])()
    with pytest.raises(UpdateError, match="staging_failed") as error:
        client.stage(asset, tmp_path, rollback_size=10, free_space=lambda _: 100)
    assert "chart-data" not in str(error.value)


def test_stage_requires_rollback_context(tmp_path):
    payload = archive_payload()
    asset = type("Asset", (), {**MANIFEST["platforms"]["windows-x64"], "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})()
    client = UpdateClient("2.3.0", "https://github.com/example/repo/releases/latest/download/update-manifest.json", transport=lambda *_: Response(payload), trusted_publisher_identity="Publisher", platform_name="windows-x64")
    with pytest.raises(UpdateError, match="disk_context_required"):
        client.stage(asset, tmp_path, free_space=lambda _: 10000)


def test_signature_failure_removes_transaction(tmp_path, monkeypatch):
    payload = archive_payload()
    asset = type("Asset", (), {**MANIFEST["platforms"]["windows-x64"], "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})()
    monkeypatch.setattr(update, "verify_native_signature", lambda *_: (_ for _ in ()).throw(UpdateError("signature_invalid")))
    client = UpdateClient("2.3.0", "https://github.com/example/repo/releases/latest/download/update-manifest.json", transport=lambda *_: Response(payload), trusted_publisher_identity="Publisher", platform_name="windows-x64")
    with pytest.raises(UpdateError, match="signature_invalid"):
        client.stage(asset, tmp_path, rollback_size=10, free_space=lambda _: 10000)
    assert not list(tmp_path.iterdir())


def test_safe_archive_rejects_traversal_and_preserves_safe_symlinks(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(UpdateError, match="unsafe_archive"):
        safe_extract_archive(archive, tmp_path / "out")

    linked = tmp_path / "linked.zip"
    with zipfile.ZipFile(linked, "w") as zf:
        zf.writestr("Versions/Current/Python", b"binary")
        info = zipfile.ZipInfo("Python")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "Versions/Current/Python")
    out = tmp_path / "linked-out"
    safe_extract_archive(linked, out)
    assert (out / "Python").is_symlink()
    assert (out / "Python").read_bytes() == b"binary"

    escaped = tmp_path / "escaped-link.zip"
    with zipfile.ZipFile(escaped, "w") as zf:
        info = zipfile.ZipInfo("bad-link")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "../../outside")
    with pytest.raises(UpdateError, match="unsafe_archive"):
        safe_extract_archive(escaped, tmp_path / "escaped-out")

    windows_escaped = tmp_path / "windows-escaped.zip"
    for name, link in (("backslash", r"..\outside"), ("drive", r"C:\outside"), ("unc", r"\\server\share")):
        with zipfile.ZipFile(windows_escaped, "w") as zf:
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16
            zf.writestr(info, link)
        with pytest.raises(UpdateError, match="unsafe_archive"):
            safe_extract_archive(windows_escaped, tmp_path / f"windows-out-{name}")


@pytest.mark.parametrize("links", [
    [("a", "d"), ("d/up", ".."), ("escape", "a/up/..")],
    [("a", "b"), ("b", "a")],
    [("a", "d"), ("d/up", ".."), ("escape", "a/up/.."), ("escape/created", "d")],
])
def test_archive_rejects_complete_symlink_escape_graph_and_loops(tmp_path, links):
    archive = tmp_path / "links.zip"
    destination = tmp_path / "out"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("d/", b"")
        for name, target in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16
            zf.writestr(info, target)
    with pytest.raises(UpdateError, match="unsafe_archive"):
        safe_extract_archive(archive, destination)
    assert not destination.exists()
    assert not (tmp_path / "created").exists()


def test_archive_retains_framework_link_graph(tmp_path):
    archive = tmp_path / "framework.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Python.framework/Versions/3.12/Python", b"framework")
        for name, target in (("Python.framework/Versions/Current", "3.12"),
                             ("Python.framework/Python", "Versions/Current/Python")):
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16
            zf.writestr(info, target)
    safe_extract_archive(archive, tmp_path / "out")
    assert (tmp_path / "out/Python.framework/Python").read_bytes() == b"framework"


def test_macos_verification_requires_codesign_and_gatekeeper(tmp_path, monkeypatch):
    for failure_index in (0, 1):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if len(calls) - 1 == failure_index:
                raise subprocess.CalledProcessError(1, command)

        with monkeypatch.context() as context:
            context.setattr(update.sys, "platform", "darwin")
            context.setattr(update.subprocess, "run", run)
            with pytest.raises(UpdateError, match="signature_invalid"):
                native_verify(tmp_path / "Chart Cleaner.app", 'Developer ID Application: Fixture')
        assert len(calls) == failure_index + 1
        assert "--test-requirement" in calls[0]
        if failure_index == 1:
            assert calls[1] == ["spctl", "--assess", "--type", "execute", str(tmp_path / "Chart Cleaner.app")]


@pytest.mark.skipif(sys.platform != "darwin", reason="Apple requirement compiler is macOS-only")
def test_macos_native_tools_parse_publisher_requirement_and_reject_mismatch(tmp_path, monkeypatch):
    calls = []
    real_run = subprocess.run
    with monkeypatch.context() as context:
        context.setattr(update.subprocess, "run", lambda command, **kwargs: calls.append(command))
        native_verify(tmp_path / "Chart Cleaner.app", 'Developer ID Application: O\'Brien "Lab" \\ Group (FIXTURE)')
    requirement = calls[0][calls[0].index("--test-requirement") + 1]
    real_run(["/usr/bin/csreq", "-r", requirement, "-b", str(tmp_path / "requirement.bin")],
             check=True, capture_output=True)
    result = real_run([*calls[0][:-1], "/usr/bin/true"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "invalid requirement" not in result.stderr.lower()
    assert "failed to satisfy" in result.stderr.lower()
    assessment = real_run([*calls[1][:-1], "/usr/bin/true"], capture_output=True, text=True, timeout=20)
    assert "invalid api object reference" not in assessment.stderr.lower()
    assert "unrecognized option" not in assessment.stderr.lower()


@pytest.mark.parametrize("status,matching_publisher", [("Valid", True), ("Valid", False), ("NotSigned", True)])
def test_powershell_executes_signature_data_as_literals(tmp_path, monkeypatch, status, matching_publisher):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell runtime is unavailable")
    calls = []
    real_run = subprocess.run
    path = tmp_path / "Chart O'Brien $([IO.File]::WriteAllText('oops','bad')); [app].exe"
    publisher = 'CN=O\'Brien "Lab"; $(throw "injected"), O=Example'
    with monkeypatch.context() as context:
        context.setattr(update.sys, "platform", "win32")
        context.setattr(update.subprocess, "run", lambda command, **kwargs: calls.append(command))
        native_verify(path, publisher)
    command = calls[0]
    assert command[1:4] == ["-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(command[4]).decode("utf-16le")
    # Exercise the generated script in the real interpreter; only the OS
    # certificate provider is a fixture, since CI may have no signing identity.
    provider = """
function Get-AuthenticodeSignature {
    param([string]$LiteralPath)
    if ($LiteralPath -cne $env:CC_SIGNATURE_PATH) { throw 'incorrect path binding' }
    [pscustomobject]@{ Status=$env:CC_SIGNATURE_STATUS;
        SignerCertificate=[pscustomobject]@{ Subject=$env:CC_SIGNATURE_PUBLISHER } }
}
"""
    encoded = base64.b64encode((provider + script).encode("utf-16le")).decode("ascii")
    env = {**os.environ, "CC_SIGNATURE_PATH": str(path), "CC_SIGNATURE_STATUS": status,
           "CC_SIGNATURE_PUBLISHER": publisher if matching_publisher else "CN=Other"}
    result = real_run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                      env=env, cwd=tmp_path, capture_output=True, timeout=20)
    assert (result.returncode == 0) is (status == "Valid" and matching_publisher)
    assert not (tmp_path / "oops").exists()
