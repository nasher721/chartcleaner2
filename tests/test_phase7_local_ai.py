"""Phase 7 group 6: local AI health and model pull, streaming, citations."""

from __future__ import annotations

import io
import json

import pytest

from chartcleaner import local_llm
from chartcleaner.chart_qa import ask_chart
from chartcleaner.citations import cite
from chartcleaner.local_llm import LocalLlmClient, health
from chartcleaner.summarizer import run_model, summarize

CHART = ("Progress Note\nPatient is a 58-y/o woman w HTN, HLD, and a\nSAH admitted following coiling "
         "of a ruptured ACOM aneurysm.\nLabs: WBC 8.2, Hgb 11.4, Na 141.\n- Nimodipine 60 mg PO q4h.\n")


class StreamClient:
    def __init__(self, pieces):
        self.pieces = pieces

    def is_available(self):
        return True

    def list_models(self):
        return ["llama3.1:latest"]

    def generate(self, prompt, model="", system=None):
        return "".join(self.pieces)

    def generate_stream(self, prompt, model="", system=None):
        yield from self.pieces


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(routes):
    def opener(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        for suffix, body in routes.items():
            if url.endswith(suffix):
                if isinstance(body, Exception):
                    raise body
                return _Resp(body if isinstance(body, bytes) else json.dumps(body).encode())
        raise OSError("no route")
    return opener


# --- client ----------------------------------------------------------------------

def test_streaming_and_pull_parse_ndjson(monkeypatch):
    stream = b"\n".join(json.dumps(x).encode() for x in (
        {"response": "Na "}, {"response": "141"}, {"response": "", "done": True}))
    pull = b"\n".join(json.dumps(x).encode() for x in (
        {"status": "pulling manifest"}, {"status": "downloading", "total": 100, "completed": 50},
        {"status": "success"}))
    monkeypatch.setattr(local_llm.urllib.request, "urlopen",
                        _fake_urlopen({"/api/generate": stream, "/api/pull": pull}))
    client = LocalLlmClient("http://127.0.0.1:11434")
    assert list(client.generate_stream("p", model="m")) == ["Na ", "141"]
    seen = []
    assert client.pull("llama3.1", on_progress=lambda st, fr: seen.append((st, fr)))
    assert ("downloading", 0.5) in seen and seen[-1][0] == "success"
    with pytest.raises(ValueError):
        client.pull("bad name; rm -rf /")


def test_pull_reports_ollama_errors(monkeypatch):
    monkeypatch.setattr(local_llm.urllib.request, "urlopen",
                        _fake_urlopen({"/api/pull": json.dumps({"error": "not found"}).encode()}))
    with pytest.raises(RuntimeError, match="not found"):
        LocalLlmClient("http://127.0.0.1:11434").pull("nope")


def test_health_reports_models_and_recommended(monkeypatch):
    tags = {"models": [{"name": "llama3.1:latest", "size": 4_900_000_000, "modified_at": "2026-09-20T10:00",
                        "details": {"family": "llama", "parameter_size": "8B"}}]}
    monkeypatch.setattr(local_llm.urllib.request, "urlopen",
                        _fake_urlopen({"/api/tags": tags, "/api/version": {"version": "0.12.0"}}))
    h = health("http://127.0.0.1:11434")
    assert h["reachable"] and h["version"] == "0.12.0" and h["has_recommended"]
    assert h["models"][0] == {"name": "llama3.1:latest", "size_gb": 4.9, "modified": "2026-09-20",
                              "family": "llama", "parameters": "8B"}


def test_health_when_down_or_off_machine(monkeypatch):
    monkeypatch.setattr(local_llm.urllib.request, "urlopen", _fake_urlopen({}))
    h = health("http://127.0.0.1:11434")
    assert not h["reachable"] and "not running" in h["error"]
    h = health("http://8.8.8.8:11434")
    assert not h["loopback"] and "loopback" in h["error"]


# --- streaming -------------------------------------------------------------------

def test_run_model_streams_when_asked():
    seen = []
    out = run_model(StreamClient(["Na ", "141"]), "p", "m", on_token=seen.append)
    assert out == "Na 141" and seen == ["Na ", "Na 141"]
    assert run_model(StreamClient(["x"]), "p", "m") == "x"


def test_summarize_and_ask_stream():
    seen = []
    res = summarize(CHART, {}, client=StreamClient(["Na 141, ", "WBC 8.2."]), on_token=seen.append)
    assert res.text == "Na 141, WBC 8.2." and seen[-1] == "Na 141, WBC 8.2."
    seen.clear()
    qa = ask_chart("Na?", CHART, {}, client=StreamClient(["141"]), on_token=seen.append)
    assert qa.answer == "141" and seen == ["141"]


def test_a_failing_display_callback_never_stops_generation():
    def boom(_t):
        raise RuntimeError("ui gone")
    assert run_model(StreamClient(["a", "b"]), "p", "m", on_token=boom) == "ab"


# --- citations -------------------------------------------------------------------

def test_citations_point_to_chart_lines():
    summary = ("## Assessment\n58-year-old woman with SAH after coiling of an ACOM aneurysm.\n"
               "- Na 141, WBC 8.2\n- Started vancomycin for MRSA\n")
    cites = cite(summary, CHART)
    assert [c.line for c in cites] == [1, 2, 3]   # the heading is skipped
    first, labs, invented = cites
    assert first.found and CHART[first.start:first.end].startswith("Patient is a 58-y/o")
    assert "ACOM aneurysm" in first.source         # wrapped chart lines are joined
    assert labs.found and CHART[labs.start:labs.end] == "Labs: WBC 8.2, Hgb 11.4, Na 141."
    assert not invented.found and invented.start == -1


def test_summary_and_answer_carry_citations():
    res = summarize(CHART, {}, client=StreamClient(["Nimodipine 60 mg PO q4h."]))
    assert res.citations and res.citations[0].found
    qa = ask_chart("meds?", CHART, {}, client=StreamClient(["Nimodipine 60 mg"]))
    assert qa.citations[0].source.startswith("- Nimodipine")
    from chartcleaner import service
    out = service.ask("meds?", CHART, config={}, client=StreamClient(["Nimodipine 60 mg"]))
    assert out["citations"][0]["start"] >= 0
