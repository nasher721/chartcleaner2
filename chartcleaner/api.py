"""Local REST API for scripts, launchers and the browser extension.

Routes (JSON in, JSON out), all backed by :mod:`chartcleaner.service`:

* ``GET  /api/v1/health`` — ``{"ok": true, "version": ...}`` (no chart data, no token)
* ``POST /api/v1/clean`` — ``{"text", "preset"?, "format"?, "delta"?}``
* ``POST /api/v1/abbreviate`` / ``/api/v1/expand`` — ``{"text", "preset"?}``
* ``POST /api/v1/prompt`` — ``{"text", "template", "preset"?}``

Guards, in order: the caller must be on this computer (loopback address),
an ``Origin`` header — if any — must be this app or a browser extension,
the body must be under 5 MB, and ``Authorization: Bearer <token>`` must
match the per-install token in ``data/api_token`` (created on first use,
readable only by you; shown and rotated in Settings). Chart text is never
logged.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from . import __version__, service, store

__all__ = ["MAX_BODY_BYTES", "token_path", "get_token", "rotate_token", "register"]

MAX_BODY_BYTES = 5 * 1024 * 1024
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_ALLOWED_ORIGIN = re.compile(r"^(?:https?://(?:127\.0\.0\.1|localhost)(?::\d+)?|"
                             r"chrome-extension://[a-p]{32}|moz-extension://[0-9a-f-]{36})$")


def token_path() -> Path:
    return store.DATA_DIR / "api_token"


def get_token() -> str:
    path = token_path()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    return rotate_token()


def rotate_token() -> str:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def _deny(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _payload(request, required: tuple[str, ...]) -> tuple[dict | None, Any]:
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK:
        return None, _deny(403, "The API only answers requests from this computer.")
    origin = request.headers.get("origin")
    if origin and not _ALLOWED_ORIGIN.match(origin):
        return None, _deny(403, "Requests from web pages are not allowed.")
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return None, _deny(413, "Body over 5 MB; split the chart.")
    auth = request.headers.get("authorization", "")
    if not hmac.compare_digest(auth.encode(), f"Bearer {get_token()}".encode()):
        return None, _deny(401, "Missing or wrong token (Settings → Local API).")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return None, _deny(413, "Body over 5 MB; split the chart.")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return None, _deny(400, "Body must be JSON.")
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) for k in required):
        return None, _deny(400, f"JSON needs text field(s): {', '.join(required)}.")
    return data, None


def register(app) -> None:
    """Add the API routes to a FastAPI (or NiceGUI) app."""
    @app.get("/api/v1/health")
    async def api_health() -> dict:
        return {"ok": True, "version": __version__}

    async def run(request: Request, required: tuple[str, ...], call):
        data, denied = await _payload(request, required)
        if denied is not None:
            return denied
        try:
            return call(data)
        except KeyError as ex:
            return _deny(404, str(ex).strip("'\""))
        except (ValueError, FileNotFoundError) as ex:
            return _deny(400, str(ex))

    @app.post("/api/v1/clean")
    async def api_clean(request: Request):
        return await run(request, ("text",), lambda d: service.clean(
            d["text"], preset=d.get("preset") or None, fmt=d.get("format") or "text",
            delta=bool(d.get("delta")), source="api:rest"))

    @app.post("/api/v1/abbreviate")
    async def api_abbreviate(request: Request):
        return await run(request, ("text",), lambda d: service.abbreviate(
            d["text"], preset=d.get("preset") or None, source="api:rest"))

    @app.post("/api/v1/expand")
    async def api_expand(request: Request):
        return await run(request, ("text",), lambda d: service.expand(
            d["text"], preset=d.get("preset") or None, source="api:rest"))

    @app.post("/api/v1/prompt")
    async def api_prompt(request: Request):
        return await run(request, ("text", "template"), lambda d: service.prompt(
            d["text"], d["template"], preset=d.get("preset") or None, source="api:rest"))
