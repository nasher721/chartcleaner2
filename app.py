#!/usr/bin/env python3
"""Chart Cleaner — local desktop app (runs in your browser, stays on your machine).

Pages:
  /          Clean      — paste or drop charts, live diff, per-run stats, batch folder
  /pipeline  Pipeline   — edit/reorder/enable every cleaning rule, presets, import/export
  /stats     Statistics — history dashboard: how much text was cleaned, by what
  /scripts   Scripts    — create and edit custom Python cleaning rules
  /settings  Settings   — output options, NLP status, data management
  /doctor    Doctor     — checks every dependency and offers a fix for each problem

Run with:  python app.py            (or the platform launcher / double-click helper)
           python app.py --help     for options
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import threading
import webbrowser

from nicegui import app, ui

from app_pages import common, updates
from app_pages.common import *  # noqa: F401,F403 — re-exported for scripts and tests
from app_pages.batch import batch_page
from app_pages.clean import clean_page
from app_pages.doctor import doctor_page
from app_pages.pipeline import pipeline_page
from app_pages.rules import text_rules_page
from app_pages.scripts import scripts_page
from app_pages.settings import settings_page
from app_pages.stats import stats_page
from app_pages.updates import _check_for_updates, _is_chart_cleaner, _update_startup  # noqa: F401


# ---------------------------------------------------------------------------
# offline/privacy: strip any external font CDN links from served HTML
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _strip_external_fonts(request, call_next):
    response = await call_next(request)
    try:
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            body = getattr(response, "body", None)
            if body is not None:
                cleaned = re.sub(rb"<link[^>]*fonts\.g(?:oogleapis|static)\.com[^>]*>\s*", b"", body)
                if cleaned != body:
                    response.body = cleaned
                    response.headers["content-length"] = str(len(cleaned))
    except Exception:
        pass
    return response


# Routes are registered here, not with decorators in app_pages: the test harness
# re-runs app.py against an already-imported app_pages, and every run needs routes.
ROUTES = {
    "/": clean_page,
    "/batch": batch_page,
    "/rules": text_rules_page,
    "/pipeline": pipeline_page,
    "/stats": stats_page,
    "/scripts": scripts_page,
    "/settings": settings_page,
    "/doctor": doctor_page,
}
for _path, _page in ROUTES.items():
    ui.page(_path)(_page)

store.ensure_dirs()  # app.py may re-run (test harness) with relocated data dirs
app.add_static_files("/exports", str(store.EXPORTS_DIR))
local_api.register(app)  # /api/v1/* — loopback + token only (see chartcleaner/api.py)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _free_port(start: int) -> int:
    """First port uvicorn can actually bind. A connect probe is not enough:
    a socket may be bound without listening (e.g. an outbound connection's
    local port), which passes connect_ex but fails uvicorn's bind."""
    for p in range(start, start + 50):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _warm_nlp_engines() -> None:
    """Kick off a best-effort background warmup of the Presidio/spaCy engines.

    The engines are cached module-level in chartcleaner.stages; pre-building
    them at startup means the first Clean click skips the model-load tax.
    The signature must match what run_nlp() passes so the cache is reused.
    """
    def _work() -> None:
        try:
            if os.environ.get("NICEGUI_USER_SIMULATION"):
                return  # test harness: never pay model-load time
            try:
                from chartcleaner.abbreviations import _matcher
                _matcher()  # the abbreviation matcher is built lazily; build it now
            except Exception:
                pass
            cfg = load_config(common.CONFIG_PATH)
            if not bool((cfg.get("nlp_redaction") or {}).get("enabled", True)):
                return
            from chartcleaner.stages import _PRESIDIO_CACHE, DEFAULT_NLP_ENTITIES

            ncfg = cfg.get("nlp_redaction") or {}
            entities = dict(ncfg.get("entities") or DEFAULT_NLP_ENTITIES)
            allow = frozenset(t.lower() for t in (cfg.get("nlp_allow_list") or []))
            _PRESIDIO_CACHE.get_engines(tuple(sorted(entities.items())), allow, ncfg.get("score_threshold"))
        except Exception:
            pass  # warmup is best-effort; the Clean page surfaces real NLP errors

    threading.Thread(target=_work, daemon=True, name="nlp-warmup").start()


def _clipboard_startup() -> None:
    if os.environ.get("NICEGUI_USER_SIMULATION"):
        return  # tests never watch the real clipboard
    if (common.PREFS.get("clipboard_watcher") or {}).get("enabled"):
        try:
            clipboard_watcher(start=True)
        except Exception:
            pass  # no clipboard on this system: the Settings card says so


def _retention_startup() -> None:
    """Encrypt legacy token maps and delete chart data past the retention period."""
    try:
        store.purge_old_data()
    except Exception:
        pass  # housekeeping must never stop the app from starting


app.on_startup(_warm_nlp_engines)
app.on_startup(_update_startup)
app.on_startup(_clipboard_startup)
app.on_startup(_retention_startup)


def main():
    if os.environ.get("NICEGUI_USER_SIMULATION"):
        # Test harness (nicegui.testing): pages are registered at import; ui.run
        # is intercepted, but it must still be called so run config is marked.
        ui.run(title="Chart Cleaner")
        return

    if getattr(sys, "frozen", False):
        # macOS .app launches can inject multiprocessing bootstrap args
        # (--keep-parent / resource_tracker -c ...); strip them before argparse.
        clean: list[str] = [sys.argv[0]]
        skip = False
        for a in sys.argv[1:]:
            if skip:
                skip = False
                continue
            if a in ("--keep-parent",):
                continue
            if a == "-B" or a == "-S" or a == "-I":
                continue
            if a == "-c":
                skip = True
                continue
            clean.append(a)
        sys.argv = clean

    ap = argparse.ArgumentParser(description="Chart Cleaner desktop app (local web UI).")
    ap.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1 — keep it local).")
    ap.add_argument("--port", type=int, default=0, help="Port (default: first free port from 8765).")
    ap.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser.")
    ap.add_argument("--reload", action="store_true", help="Dev mode: auto-reload on code changes.")
    args = ap.parse_args()

    requested = args.port or 8765
    port = requested
    if _port_busy(port):
        if _is_chart_cleaner(port):
            url = f"http://127.0.0.1:{port}"
            print(f"Chart Cleaner is already running at {url} — opening it instead of starting a second copy.")
            if not args.no_browser:
                webbrowser.open(url)
            return
        port = _free_port(port + 1)
        print(f"Port {requested} is used by another program — using {port} instead.")

    updates.SERVER_PORT = port
    ui.run(
        host=args.host,
        port=port,
        title="Chart Cleaner",
        reload=args.reload,
        show=not args.no_browser,
        favicon="🩺",
    )


if __name__ == "__main__":
    main()
