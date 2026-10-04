"""Local REST API: guards and parity with the service layer."""

import json
import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from chartcleaner import api, service, store
from chartcleaner.engine import load_default_config


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(store, "CUSTOM_RULES_DIR", tmp_path / "custom_rules")
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    app = FastAPI()
    api.register(app)
    test_client = TestClient(app, client=("127.0.0.1", 50000))
    test_client.headers["Authorization"] = f"Bearer {api.get_token()}"
    return test_client


TEXT = "Printed by Jane Doe on 01/02/2024\nPatient has Hypertension.\n"


def test_health_needs_no_token_and_leaks_nothing(client):
    del client.headers["Authorization"]
    assert client.get("/api/v1/health").json()["ok"] is True


def test_clean_matches_service(client):
    out = client.post("/api/v1/clean", json={"text": TEXT})
    assert out.status_code == 200
    assert out.json()["text"] == service.clean(TEXT, record=False)["text"]


def test_abbreviate_expand_and_prompt(client):
    assert client.post("/api/v1/abbreviate", json={"text": "Hypertension"}).json()["text"] == "HTN"
    assert client.post("/api/v1/expand", json={"text": "HTN"}).json()["text"] == "Hypertension"
    prompt = client.post("/api/v1/prompt", json={"text": TEXT, "template": "Assessment & plan only"})
    assert prompt.json()["text"].startswith("Write only the assessment and plan")
    missing = client.post("/api/v1/prompt", json={"text": TEXT, "template": "Nope"})
    assert missing.status_code == 404


def test_token_is_required_and_compared(client):
    client.headers["Authorization"] = "Bearer wrong"
    assert client.post("/api/v1/clean", json={"text": TEXT}).status_code == 401
    del client.headers["Authorization"]
    assert client.post("/api/v1/clean", json={"text": TEXT}).status_code == 401


def test_only_loopback_callers(client):
    remote = TestClient(client.app, client=("192.168.1.20", 50000))
    remote.headers["Authorization"] = client.headers["Authorization"]
    assert remote.post("/api/v1/clean", json={"text": TEXT}).status_code == 403


@pytest.mark.parametrize("origin,status", [
    ("https://evil.example", 403), ("http://127.0.0.1:8765", 200),
    ("chrome-extension://abcdefghijklmnopabcdefghijklmnop", 200),
])
def test_origin_check(client, origin, status):
    assert client.post("/api/v1/clean", json={"text": TEXT}, headers={"Origin": origin}).status_code == status


def test_bad_bodies(client):
    assert client.post("/api/v1/clean", content=b"not json").status_code == 400
    assert client.post("/api/v1/clean", json={"txt": "x"}).status_code == 400
    big = "x" * (api.MAX_BODY_BYTES + 1)
    assert client.post("/api/v1/clean", json={"text": big}).status_code == 413
    assert client.post("/api/v1/clean", json={"text": TEXT, "preset": "missing"}).status_code == 400


def test_token_file_is_private_and_rotates(client):
    first = api.get_token()
    if os.name != "nt":  # Windows has no POSIX mode bits; the profile folder's ACL guards it
        assert oct(os.stat(api.token_path()).st_mode & 0o777) == "0o600"
    assert api.rotate_token() != first == first


def test_clean_can_skip_the_wrapper(client):
    wrapped = client.post("/api/v1/clean", json={"text": TEXT}).json()["text"]
    bare = client.post("/api/v1/clean", json={"text": TEXT, "wrap": False}).json()["text"]
    assert wrapped.startswith("<patient_chart>") and not bare.startswith("<")


def test_bad_content_length_is_rejected_not_a_crash(client):
    out = client.post("/api/v1/clean", content=b'{"text": "x"}',
                      headers={"Content-Length": "abc", "Content-Type": "application/json"})
    assert out.status_code == 400


def test_wrong_typed_optional_fields_are_ignored(client):
    out = client.post("/api/v1/clean", json={"text": TEXT, "preset": 7, "format": ["x"], "wrap": "no"})
    assert out.status_code == 200
    assert out.json()["text"] == service.clean(TEXT, record=False)["text"]
