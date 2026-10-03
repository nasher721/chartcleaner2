"""Update checks and hand-off to the updater (Settings page + startup)."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403

# Set by app.main() once the port is known.
SERVER_PORT: int | None = None


def _update_client():
    if not MANIFEST_URL:
        return None
    publisher = MACOS_PUBLISHER if sys.platform == "darwin" else WINDOWS_PUBLISHER
    return UpdateClient(str(__version__), MANIFEST_URL,
                       trusted_publisher_identity=publisher)


async def _check_for_updates(*, automatic: bool, force: bool = False, status_label=None):
    """Run metadata-only update checking off the NiceGUI event loop."""
    if not getattr(sys, "frozen", False):
        status = {"state": "source", "code": "source_mode"}
        common.PREFS.update(update_status="Source checkout — updates are available in installed builds",
                     update_status_code=status["code"])
        save_prefs()
        if status_label:
            status_label.set_text(common.PREFS["update_status"])
        return None, status
    client = _update_client()
    if client is None:
        status = {"state": "unavailable", "code": "update_unavailable"}
        common.PREFS.update(update_status="Update checks unavailable", update_status_code=status["code"])
        save_prefs()
        if status_label:
            status_label.set_text(common.PREFS["update_status"])
        return None, status
    previous_check = common.PREFS.get("update_last_checked")
    manifest, status = await run.io_bound(
        lambda: client.check(automatic=automatic,
                             last_checked=previous_check if automatic else None,
                             force=force))
    if status.state == "throttled":
        return manifest, status
    common.PREFS["update_last_checked"] = status.checked_at or time.time()
    text = {
        "current": "Up to date",
        "update_available": f"Update available: v{status.version}",
        "error": f"Check failed ({status.code}); retry manually",
    }.get(status.state, "Update status unavailable")
    common.PREFS.update(update_status=text, update_status_code=status.code)
    save_prefs()
    if status_label:
        status_label.set_text(text)
    return manifest, status


async def _automatic_update_check() -> None:
    if common.PREFS.get("update_auto_check", True):
        await _check_for_updates(automatic=True)


async def _after_server_ready() -> None:
    """Wait for the loopback route before acknowledging a companion handoff."""
    for _ in range(300):
        if SERVER_PORT and await run.io_bound(_is_chart_cleaner, SERVER_PORT):
            if getattr(sys, "frozen", False):
                from chartcleaner.updater import write_health_marker
                write_health_marker(str(__version__))
            asyncio.create_task(_automatic_update_check())
            return
        await asyncio.sleep(0.1)


async def _update_startup() -> None:
    asyncio.create_task(_after_server_ready())


async def _stage_and_handoff(manifest, status, notify) -> None:
    if manifest is None or status.state != "update_available":
        notify("No verified update is ready.", type="warning")
        return
    if not getattr(sys, "frozen", False):
        notify("Updates are unavailable in source mode.", type="warning")
        return
    archive = None
    try:
        from chartcleaner import updater
        install = updater.default_install_path()
        staging = updater.staging_root()
        client = _update_client()
        archive, _extracted = await run.io_bound(
            lambda: client.stage(manifest.platforms[status.platform], staging,
                                 rollback_size=updater.installation_size(install)))
        await run.io_bound(updater.handoff_update, os.getpid(), install, archive,
                           str(manifest.version))
    except Exception:
        if archive is not None and archive.parent.parent == staging and archive.parent.name.startswith("update-"):
            await run.io_bound(shutil.rmtree, archive.parent, True)
        notify("Update could not be staged; retry from Settings.", type="negative")
        return
    notify("Update verified. Restarting Chart Cleaner…", type="positive")
    await asyncio.sleep(0.2)
    os._exit(0)


def _is_chart_cleaner(port: int) -> bool:
    """True when something that answers like Chart Cleaner listens on this port."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5) as r:
            return "Chart Cleaner" in r.read(8192).decode("utf-8", "ignore")
    except Exception:
        return False
