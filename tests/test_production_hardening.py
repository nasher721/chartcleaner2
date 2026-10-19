"""Production hardening: rule trust, security headers, logging, readiness.

These pin the properties a hospital IT department relies on rather than the
cleaning output itself:

* listing custom rules never executes them;
* a script that was not approved by the user never runs;
* every response carries the containment headers;
* the log is bounded, redacted and truncated;
* readiness distinguishes "up" from "can actually clean".
"""

from __future__ import annotations

import logging

from chartcleaner import logging_setup, readiness, store
from chartcleaner.custom_rules import (
    apply_custom_rule,
    is_trusted,
    list_custom_rules,
    trust_rule,
    untrust_rule,
)
from chartcleaner.engine import CleanContext, StageSpec
from chartcleaner.security_headers import CSP, SecurityHeaders, STATIC_HEADERS

UPPER = '''
LABEL = "Shout"
DESCRIPTION = "Uppercases the chart."
PLACEHOLDER = False


def clean(text, ctx):
    ctx.count("upper", 1)
    return text.upper()
'''


def _write(directory, name: str, body: str):
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def _spec(directory, name: str) -> StageSpec:
    stem = name.removesuffix(".py")
    meta = {m["name"]: m for m in list_custom_rules(directory)}[stem]
    return StageSpec(f"custom:{stem}", meta["label"], "custom", True, meta)


# ---------------------------------------------------------------------------
# custom rule trust
# ---------------------------------------------------------------------------

def test_listing_custom_rules_never_executes_them(tmp_path):
    sentinel = tmp_path / "executed"
    _write(tmp_path, "sneaky.py", f'''
from pathlib import Path

Path({str(sentinel)!r}).write_text("ran")

LABEL = "Sneaky"
DESCRIPTION = "Writes a file on import."
PLACEHOLDER = True


def clean(text, ctx):
    return text
''')

    metas = list_custom_rules(tmp_path)

    assert sentinel.exists() is False, "listing a rule must not import it"
    assert [m["label"] for m in metas] == ["Sneaky"]
    assert metas[0]["placeholder"] is True
    assert metas[0]["trusted"] is False


def test_metadata_is_read_statically(tmp_path):
    _write(tmp_path, "shout.py", UPPER)

    meta = list_custom_rules(tmp_path)[0]

    assert meta["label"] == "Shout"
    assert meta["description"] == "Uppercases the chart."
    assert meta["error"] is None


def test_syntax_error_is_reported_without_raising(tmp_path):
    _write(tmp_path, "broken.py", "def clean(text, ctx)\n    return text\n")

    meta = list_custom_rules(tmp_path)[0]

    assert meta["error"].startswith("SyntaxError")
    assert meta["trusted"] is False


def test_script_without_clean_is_flagged(tmp_path):
    _write(tmp_path, "noop.py", "LABEL = 'No-op'\n")

    meta = list_custom_rules(tmp_path)[0]

    assert meta["error"] == "Script has no clean(text, ctx) function"


def test_untrusted_rule_is_skipped_not_run(tmp_path):
    path = _write(tmp_path, "shout.py", UPPER)

    out, count, _details, skip = apply_custom_rule(_spec(tmp_path, "shout.py"), "hello", CleanContext({}))

    assert out == "hello"
    assert count == 0
    assert "not trusted" in skip
    assert is_trusted(path) is False


def test_trusting_a_rule_lets_it_run(tmp_path):
    path = _write(tmp_path, "shout.py", UPPER)
    trust_rule(path)

    out, count, details, skip = apply_custom_rule(_spec(tmp_path, "shout.py"), "hello", CleanContext({}))

    assert out == "HELLO"
    assert count == 1
    assert skip is None
    assert details["counters"] == {"upper": 1}


def test_editing_a_trusted_rule_revokes_trust(tmp_path):
    path = _write(tmp_path, "shout.py", UPPER)
    trust_rule(path)
    assert is_trusted(path) is True

    _write(tmp_path, "shout.py", UPPER + "\n# a later edit\n")

    assert is_trusted(path) is False
    _out, _count, _details, skip = apply_custom_rule(
        _spec(tmp_path, "shout.py"), "hello", CleanContext({})
    )
    assert "not trusted" in skip


def test_untrust_forgets_an_approval(tmp_path):
    path = _write(tmp_path, "shout.py", UPPER)
    trust_rule(path)
    untrust_rule(path)

    assert is_trusted(path) is False


def test_placeholder_never_loads_even_when_trusted(tmp_path):
    sentinel = tmp_path / "loaded"
    path = _write(tmp_path, "stub.py", f'''
from pathlib import Path

Path({str(sentinel)!r}).write_text("ran")

PLACEHOLDER = True


def clean(text, ctx):
    return text
''')
    trust_rule(path)

    _out, _count, _details, skip = apply_custom_rule(
        _spec(tmp_path, "stub.py"), "hello", CleanContext({})
    )

    assert "placeholder" in skip
    assert sentinel.exists() is False


# ---------------------------------------------------------------------------
# security headers
# ---------------------------------------------------------------------------

async def _drive(app, scope=None):
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    await SecurityHeaders(app)(scope or {"type": "http", "headers": []}, receive, send)
    return sent


def _header_map(message) -> dict[bytes, bytes]:
    return {name.lower(): value for name, value in message["headers"]}


async def test_every_response_gets_the_containment_headers():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/html; charset=utf-8")]})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = await _drive(app)
    headers = _header_map(sent[0])

    for name, value in STATIC_HEADERS:
        assert headers[name] == value


async def test_csp_blocks_remote_origins_but_allows_own_websocket():
    assert "default-src 'self'" in CSP
    assert "frame-ancestors 'none'" in CSP
    assert "connect-src 'self'" in CSP
    assert "ws://127.0.0.1:*" in CSP
    # No remote http(s) origin is allowed anywhere in the policy.
    assert "http://" not in CSP and "https://" not in CSP


async def test_existing_headers_are_not_duplicated():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"x-frame-options", b"SAMEORIGIN")]})
        await send({"type": "http.response.body", "body": b""})

    sent = await _drive(app)
    names = [name.lower() for name, _ in sent[0]["headers"]]

    assert names.count(b"x-frame-options") == 1
    assert _header_map(sent[0])[b"x-frame-options"] == b"SAMEORIGIN"


async def test_websockets_are_passed_through_untouched():
    seen = {}

    async def app(scope, receive, send):
        seen["scope"] = scope

    await _drive(app, {"type": "websocket", "headers": []})

    assert seen["scope"]["type"] == "websocket"


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

def _read_log(tmp_path) -> str:
    return (tmp_path / logging_setup.LOG_FILENAME).read_text(encoding="utf-8")


def test_log_redacts_identifiers(tmp_path):
    logging_setup.setup_logging(tmp_path)
    logging.getLogger("chartcleaner.test").info(
        "SSN 123-45-6789, phone (555) 010-2030, MRN: 12345678, mail a@b.org"
    )
    for handler in logging.getLogger("chartcleaner").handlers:
        handler.flush()

    text = _read_log(tmp_path)

    assert "123-45-6789" not in text
    assert "010-2030" not in text
    assert "12345678" not in text
    assert "a@b.org" not in text
    assert "[SSN]" in text


def test_log_truncates_an_oversized_record(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "MAX_MESSAGE", 200)
    logging_setup.setup_logging(tmp_path)
    logging.getLogger("chartcleaner.test").info("x" * 5000)
    for handler in logging.getLogger("chartcleaner").handlers:
        handler.flush()

    text = _read_log(tmp_path)

    assert "chars truncated" in text
    assert len(text) < 1000


def test_log_rotates_instead_of_growing_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 512)
    logging_setup.setup_logging(tmp_path)
    logger = logging.getLogger("chartcleaner.test")
    for _ in range(20):
        logger.info("y" * 200)
    for handler in logging.getLogger("chartcleaner").handlers:
        handler.flush()

    assert (tmp_path / f"{logging_setup.LOG_FILENAME}.1").exists()
    assert (tmp_path / logging_setup.LOG_FILENAME).stat().st_size <= 512 * 2


def test_console_capture_routes_prints_through_the_logger(tmp_path):
    logging_setup.setup_logging(tmp_path)
    stream = logging_setup.capture_console(logging.INFO)
    stream.write("first line\nsecond line\n")
    stream.flush()
    for handler in logging.getLogger("chartcleaner").handlers:
        handler.flush()

    text = _read_log(tmp_path)

    assert "first line" in text
    assert "second line" in text


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

def test_readiness_reports_each_subsystem():
    report = readiness.check()

    assert set(report["checks"]) == {"config", "data_dir", "exports", "rules", "nlp"}
    assert isinstance(report["ready"], bool)
    assert report["version"]
    for name, check in report["checks"].items():
        assert "ok" in check, name


def test_readiness_is_true_when_the_required_directories_exist(tmp_path, monkeypatch):
    exports = tmp_path / "exports"
    exports.mkdir()
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "EXPORTS_DIR", exports)

    report = readiness.check(config_path=store.CONFIG_PATH)

    assert report["checks"]["config"]["ok"] is True
    assert report["checks"]["data_dir"]["ok"] is True
    assert report["ready"] is True


def test_readiness_flags_a_broken_config(tmp_path):
    broken = tmp_path / "config.json"
    broken.write_text("{not json", encoding="utf-8")

    report = readiness.check(config_path=broken)

    assert report["checks"]["config"]["ok"] is False
    assert report["ready"] is False
