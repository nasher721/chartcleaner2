"""Refuse requests whose ``Host`` header isn't this computer (DNS-rebinding guard).

The app binds to 127.0.0.1, but a web page can still reach it through DNS
rebinding: ``evil.example`` first resolves to the attacker, then to
127.0.0.1, and the browser treats the app as same-origin with that page. The
UI has no login (it shows recent charts and the census), so the one thing that
tells such a request apart is its ``Host`` header, which still names the
attacker's domain. Starlette's ``TrustedHostMiddleware`` mis-parses IPv6
hosts (``[::1]:8765``), hence this small ASGI middleware.

Only installed when the server listens on loopback (``app.main``); with
``--host 0.0.0.0`` the user has opted into other hostnames.
"""

from __future__ import annotations

import ipaddress

__all__ = ["is_loopback_host", "LoopbackHostGuard"]


def _hostname(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:8765
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_loopback_host(host: str) -> bool:
    """True for ``localhost``, ``127.x.x.x`` and ``::1`` (with or without a port)."""
    name = _hostname(host).rstrip(".")
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


class LoopbackHostGuard:
    """ASGI middleware: 403 (HTTP) / close 1008 (websocket) for non-loopback Hosts."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            host = ""
            for key, value in scope.get("headers") or []:
                if key == b"host":
                    host = value.decode("latin-1")
                    break
            if not is_loopback_host(host):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = b"Chart Cleaner only answers on this computer (127.0.0.1 / localhost)."
                # This rejection is generated outside the SecurityHeaders middleware
                # (this guard is the outermost layer), so carry the essentials here.
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                        (b"content-length", str(len(body)).encode()),
                                        (b"x-content-type-options", b"nosniff"),
                                        (b"x-frame-options", b"DENY"),
                                        (b"referrer-policy", b"no-referrer")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
