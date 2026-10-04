"""Local REST API for scripts, launchers and the browser extension.

Routes (JSON in, JSON out), all backed by :mod:`chartcleaner.service`:

* ``GET  /api/v1/health`` — ``{"ok": true, "version": ...}`` (no chart data, no token)
* ``POST /api/v1/clean`` — ``{"text", "preset"?, "format"?, "delta"?, "trends"?, "wrap"?}``
* ``POST /api/v1/abbreviate`` / ``/api/v1/expand`` — ``{"text", "preset"?}``
* ``POST /api/v1/prompt`` — ``{"text", "template", "preset"?}``
* ``POST /api/v1/insights`` — ``{"text", "which"?: ["problems", "devices", "micro",
  "overnight", "trends"]}`` (reads the text as given; clean it first)
* ``POST /api/v1/note`` — ``{"text", "template", "preset"?}`` (fill a note template)
* ``POST /api/v1/patients`` — ``{"text", "preset"?}`` (split a patient list, clean each)
* ``POST /api/v1/daily-note`` — ``{"text", "previous"?, "tag"?, "preset"?}``

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
from starlette.concurrency import run_in_threadpool

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
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        return None, _deny(400, "Bad Content-Length header.")
    if declared > MAX_BODY_BYTES:
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


def _str(data: dict, key: str) -> str | None:
    """``data[key]`` if it is a non-empty string, else None (wrong types are ignored)."""
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _bool(data: dict, key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


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
            # Cleans can take seconds (NLP redaction); keep the app's event loop free.
            return await run_in_threadpool(call, data)
        except KeyError as ex:
            return _deny(404, str(ex).strip("'\""))
        except (ValueError, FileNotFoundError) as ex:
            return _deny(400, str(ex))

    @app.post("/api/v1/clean")
    async def api_clean(request: Request):
        return await run(request, ("text",), lambda d: service.clean(
            d["text"], preset=_str(d, "preset"), fmt=_str(d, "format") or "text",
            delta=bool(d.get("delta")), trends=bool(d.get("trends")), wrap=_bool(d, "wrap"),
            source="api:rest"))

    @app.post("/api/v1/abbreviate")
    async def api_abbreviate(request: Request):
        return await run(request, ("text",), lambda d: service.abbreviate(
            d["text"], preset=_str(d, "preset"), source="api:rest"))

    @app.post("/api/v1/expand")
    async def api_expand(request: Request):
        return await run(request, ("text",), lambda d: service.expand(
            d["text"], preset=_str(d, "preset"), source="api:rest"))

    @app.post("/api/v1/restore")
    async def api_restore(request: Request):
        return await run(request, ("text",), lambda d: service.restore(d["text"]))

    @app.post("/api/v1/insights")
    async def api_insights(request: Request):
        def call(d):
            which = d.get("which")
            if which is not None and not (isinstance(which, list) and all(isinstance(w, str) for w in which)):
                raise ValueError("which must be a list of insight names")
            return service.insights(d["text"], which or None)
        return await run(request, ("text",), call)

    @app.post("/api/v1/note")
    async def api_note(request: Request):
        return await run(request, ("text", "template"), lambda d: service.note(
            d["text"], d["template"], preset=_str(d, "preset"), source="api:rest"))

    @app.post("/api/v1/patients")
    async def api_patients(request: Request):
        return await run(request, ("text",), lambda d: {"patients": service.clean_patients(
            d["text"], preset=_str(d, "preset"))})

    @app.post("/api/v1/daily-note")
    async def api_daily_note(request: Request):
        return await run(request, ("text",), lambda d: service.daily_note(
            d["text"], _str(d, "previous"), tag=_str(d, "tag"),
            preset=_str(d, "preset"), source="api:rest"))

    @app.post("/api/v1/prompt")
    async def api_prompt(request: Request):
        return await run(request, ("text", "template"), lambda d: service.prompt(
            d["text"], d["template"], preset=_str(d, "preset"), source="api:rest"))
