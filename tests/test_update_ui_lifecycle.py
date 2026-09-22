"""Focused update UI lifecycle tests; all transports and handoff calls are local fakes."""

from types import SimpleNamespace

import app
from chartcleaner.update import UpdateStatus, Version


async def test_automatic_check_uses_previous_timestamp_and_records_result(monkeypatch):
    class Client:
        def check(self, **kwargs):
            assert kwargs["automatic"] is True
            assert kwargs["last_checked"] == 123.0
            return None, UpdateStatus("error", "network_error", 456.0, platform="macos-arm64")

    before = dict(app.PREFS)
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app, "_update_client", lambda: Client())
    app.PREFS["update_last_checked"] = 123.0
    try:
        _manifest, status = await app._check_for_updates(automatic=True)
        assert status.code == "network_error"
        assert app.PREFS["update_last_checked"] == 456.0
        assert "retry manually" in app.PREFS["update_status"]
    finally:
        app.PREFS.clear()
        app.PREFS.update(before)


async def test_manual_check_surfaces_update_available_without_network(monkeypatch):
    class Client:
        def check(self, **kwargs):
            return SimpleNamespace(version=Version.parse("2.4.0")), UpdateStatus(
                "update_available", checked_at=10.0, version="2.4.0", platform="windows-x64")

    before = dict(app.PREFS)
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app, "_update_client", lambda: Client())
    try:
        manifest, status = await app._check_for_updates(automatic=False, force=True)
        assert str(manifest.version) == "2.4.0"
        assert status.state == "update_available"
        assert app.PREFS["update_status"] == "Update available: v2.4.0"
    finally:
        app.PREFS.clear()
        app.PREFS.update(before)


async def test_startup_health_marker_is_called_before_auto_check(monkeypatch):
    events = []
    monkeypatch.setattr("chartcleaner.updater.write_health_marker",
                        lambda version: events.append(("health", version)) or True)
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app, "SERVER_PORT", 8765)
    monkeypatch.setattr(app, "_is_chart_cleaner", lambda port: port == 8765)
    async def auto():
        return None
    monkeypatch.setattr(app, "_automatic_update_check", auto)
    monkeypatch.setattr(app.asyncio, "create_task", lambda coro: (events.append(("schedule",)), coro.close())[1])
    await app._after_server_ready()
    assert events == [("health", str(app.__version__)), ("schedule",)]


async def test_health_probe_serves_same_event_loop_without_blocking(monkeypatch):
    import asyncio

    ready = asyncio.Event()
    markers = []

    async def serve(reader, writer):
        await reader.read(4096)
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\nChart Cleaner')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    monkeypatch.setattr(app, 'SERVER_PORT', server.sockets[0].getsockname()[1])
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr('chartcleaner.updater.write_health_marker', lambda version: markers.append(version))

    async def auto():
        ready.set()

    monkeypatch.setattr(app, '_automatic_update_check', auto)
    try:
        await asyncio.wait_for(app._after_server_ready(), timeout=2)
        await asyncio.wait_for(ready.wait(), timeout=1)
        assert markers == [str(app.__version__)]
    finally:
        server.close()
        await server.wait_closed()


async def test_throttle_preserves_actual_check_across_restarts(monkeypatch, tmp_path):
    import io
    import json
    from chartcleaner.update import UpdateClient

    clock = [100.0]
    requests = []
    payload = {'version': '2.3.0', 'minimum_supported_version': '2.3.0',
               'notes_url': 'https://github.com/example/repo/releases/tag/v2.3.0',
               'platforms': {'macos-arm64': {'url': 'https://github.com/example/repo/app.zip',
                                            'sha256': 'a' * 64, 'size': 1}}}

    def transport(*args):
        requests.append(args)
        return io.BytesIO(json.dumps(payload).encode())

    client = UpdateClient('2.3.0', 'https://github.com/example/repo/releases/latest/download/update-manifest.json',
                          platform_name='macos-arm64', now=lambda: clock[0], transport=transport)
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app, '_update_client', lambda: client)
    monkeypatch.setattr(app.store, 'PREFS_FILE', tmp_path / 'prefs.json')
    monkeypatch.setattr(app, 'PREFS', dict(app.store.DEFAULT_PREFS))
    await app._check_for_updates(automatic=True)
    assert len(requests) == 1
    clock[0] += 23 * 3600
    monkeypatch.setattr(app, 'PREFS', app.store.load_prefs())
    await app._check_for_updates(automatic=True)
    assert app.store.load_prefs()['update_last_checked'] == 100.0
    assert app.PREFS['update_status'] == 'Up to date'
    clock[0] += 23 * 3600
    monkeypatch.setattr(app, 'PREFS', app.store.load_prefs())
    await app._check_for_updates(automatic=True)
    assert len(requests) == 2
    assert app.store.load_prefs()['update_last_checked'] == clock[0]


async def test_disabled_auto_keeps_timestamp_and_manual_check_still_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(app, 'PREFS', dict(app.PREFS, update_auto_check=False, update_last_checked=123.0))
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app, 'save_prefs', lambda: None)

    class Client:
        def check(self, **kwargs):
            calls.append(kwargs)
            return None, UpdateStatus('current', checked_at=456.0)

    monkeypatch.setattr(app, '_update_client', lambda: Client())
    await app._automatic_update_check()
    assert calls == [] and app.PREFS['update_last_checked'] == 123.0
    await app._check_for_updates(automatic=False, force=True)
    assert calls == [{'automatic': False, 'last_checked': None, 'force': True}]
    assert app.PREFS['update_last_checked'] == 456.0


async def test_handoff_runs_off_loop_and_exits_only_after_success(monkeypatch, tmp_path):
    import threading
    from chartcleaner import updater

    loop_thread = threading.get_ident()
    events = []
    archive = tmp_path / 'update-fixture' / 'archive'
    archive.parent.mkdir()
    archive.write_bytes(b'fixture')
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updater, 'default_install_path', lambda: tmp_path / 'installed')
    monkeypatch.setattr(updater, 'staging_root', lambda: tmp_path)
    monkeypatch.setattr(updater, 'installation_size', lambda _: 100)
    monkeypatch.setattr(app.os, '_exit', lambda status: events.append(('exit', status)))

    class Client:
        def stage(self, *args, **kwargs):
            assert threading.get_ident() != loop_thread
            events.append(('stage',))
            return archive, archive.parent / 'extracted'

    def handoff(*args):
        assert threading.get_ident() != loop_thread
        events.append(('handoff',))
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(app, '_update_client', lambda: Client())
    monkeypatch.setattr(updater, 'handoff_update', handoff)
    manifest = SimpleNamespace(version=Version.parse('2.4.0'), platforms={'macos-arm64': object()})
    status = UpdateStatus('update_available', platform='macos-arm64')
    await app._stage_and_handoff(manifest, status, lambda *a, **kw: None)
    assert events == [('stage',), ('handoff',), ('exit', 0)]
    assert archive.exists()


async def test_failed_handoff_cleans_staging_and_does_not_exit(monkeypatch, tmp_path):
    from chartcleaner import updater

    archive = tmp_path / 'update-fixture' / 'archive'
    archive.parent.mkdir()
    archive.write_bytes(b'fixture')
    notices = []
    exits = []
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updater, 'default_install_path', lambda: tmp_path / 'installed')
    monkeypatch.setattr(updater, 'staging_root', lambda: tmp_path)
    monkeypatch.setattr(updater, 'installation_size', lambda _: 100)
    monkeypatch.setattr(app.os, '_exit', lambda *a: exits.append(a))
    monkeypatch.setattr(app, '_update_client', lambda: SimpleNamespace(stage=lambda *a, **kw: (archive, archive.parent / 'extracted')))

    def handoff(*args):
        raise OSError('/private/chart-data/token-map.json')

    monkeypatch.setattr(updater, 'handoff_update', handoff)
    manifest = SimpleNamespace(version=Version.parse('2.4.0'), platforms={'macos-arm64': object()})
    await app._stage_and_handoff(manifest, UpdateStatus('update_available', platform='macos-arm64'),
                                 lambda message, **kw: notices.append(message))
    assert not exits and not archive.parent.exists()
    assert notices == ['Update could not be staged; retry from Settings.']


async def test_failed_stage_never_hands_off_or_exits(monkeypatch, tmp_path):
    from chartcleaner import updater
    from chartcleaner.update import UpdateError

    calls = []
    notices = []
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updater, 'default_install_path', lambda: tmp_path / 'installed')
    monkeypatch.setattr(updater, 'staging_root', lambda: tmp_path)
    monkeypatch.setattr(updater, 'installation_size', lambda _: 100)
    monkeypatch.setattr(app.os, '_exit', lambda *a: calls.append('exit'))
    monkeypatch.setattr(updater, 'handoff_update', lambda *a: calls.append('handoff'))

    def stage(*args, **kwargs):
        raise UpdateError('signature_invalid')

    monkeypatch.setattr(app, '_update_client', lambda: SimpleNamespace(stage=stage))
    manifest = SimpleNamespace(version=Version.parse('2.4.0'), platforms={'macos-arm64': object()})
    await app._stage_and_handoff(manifest, UpdateStatus('update_available', platform='macos-arm64'),
                                 lambda message, **kw: notices.append(message))
    assert calls == []
    assert notices == ['Update could not be staged; retry from Settings.']


async def test_unready_server_never_acknowledges_health_or_checks_updates(monkeypatch):
    calls = []
    monkeypatch.setattr(app, 'SERVER_PORT', None)
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr('chartcleaner.updater.write_health_marker', lambda *a: calls.append('health'))

    async def no_sleep(*args):
        return None

    async def auto():
        calls.append('check')

    monkeypatch.setattr(app.asyncio, 'sleep', no_sleep)
    monkeypatch.setattr(app, '_automatic_update_check', auto)
    await app._after_server_ready()
    assert calls == []
