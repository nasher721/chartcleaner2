"""DNS-rebinding guard: the UI answers only to loopback Host headers."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from chartcleaner.host_guard import LoopbackHostGuard, is_loopback_host


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8765", "localhost", "LOCALHOST:8765",
                                  "localhost.", "[::1]:8765", "::1", "127.1.2.3:80"])
def test_loopback_hosts_allowed(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["", "evil.example", "evil.example:8765", "127.0.0.1.evil.example",
                                  "localhost.evil.example", "10.0.0.5:8765", "[2001:db8::1]:80",
                                  "0.0.0.0:8765"])
def test_other_hosts_refused(host):
    assert not is_loopback_host(host)


def _client():
    async def ws(websocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    app = Starlette(routes=[Route("/", lambda r: PlainTextResponse("chart")),
                            WebSocketRoute("/ws", ws)])
    app.add_middleware(LoopbackHostGuard)
    return TestClient(app)


def test_http_requests_checked_by_host_header():
    client = _client()
    assert client.get("/", headers={"host": "127.0.0.1:8765"}).text == "chart"
    refused = client.get("/", headers={"host": "rebind.evil.example:8765"})
    assert refused.status_code == 403 and "chart" not in refused.text


def test_websockets_checked_by_host_header():
    client = _client()
    with client.websocket_connect("/ws", headers={"host": "localhost:8765"}) as ws:
        assert ws.receive_text() == "hi"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"host": "rebind.evil.example"}) as ws:
            ws.receive_text()
