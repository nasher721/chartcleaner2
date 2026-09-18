"""Tests for reversible tokenization: tokens module + engine stage + store maps."""

import json

import pytest

from chartcleaner import store
from chartcleaner import tokens as tok
from chartcleaner.engine import load_default_config, Pipeline, validate_config


PATTERNS = [
    [r"\bMRN\s*[:#]?\s*\d+\b", "[REDACTED_ID]"],
    [r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]"],
]


def test_tokenize_basic_and_roundtrip():
    text = "Patient: John Doe\nMRN: 123456\nSSN 123-45-6789\nagain MRN: 123456"
    out, mapping = tok.tokenize(text, PATTERNS, labels=["Patient"])
    assert "123456" not in out and "6789" not in out and "John Doe" not in out
    assert out.count("[[T1]]") + out.count("[[T2]]") + out.count("[[T3]]") >= 4
    # same value ⇒ same token (both MRN lines use the identical string)
    assert out.count(mapping["MRN: 123456"]) == 2
    restored, n = tok.untokenize(out, mapping)
    assert restored == text and n >= 4


def test_tokenize_longest_match_wins():
    text = "Met John Smith Jr today."
    out, mapping = tok.tokenize(text, [[r"\bJohn Smith Jr\b", ""], [r"\bJohn Smith\b", ""]])
    assert "John Smith Jr" in mapping          # the longer span wins…
    assert "John Smith" not in mapping         # …the shorter overlap is dropped
    assert "John Smith" not in out


def test_engine_stage_default_off():
    cfg = load_default_config()
    res = Pipeline(cfg).run("MRN: 998877\nAssessment: ok", wrap=False)
    stage = next(s for s in res.stages if s.id == "tokenize_phi")
    assert stage.skipped and "token_map" not in stage.details


def test_engine_stage_issues_tokens_and_strips_history():
    cfg = load_default_config()
    cfg["tokenization"] = {"enabled": True, "prefix": "P"}
    text = "Patient John Doe\nMRN: 4422119\nDOB: 05/04/1955\nAssessment: stable"
    res = Pipeline(cfg).run(text, wrap=False)
    stage = next(s for s in res.stages if s.id == "tokenize_phi")
    assert not stage.skipped
    assert stage.details["tokens_issued"] >= 1
    assert "[[P1]]" in res.text
    hist = next(s for s in res.to_history_dict("t")["stages"] if s["id"] == "tokenize_phi")
    assert "token_map" not in hist["details"]  # map never lands in stats.jsonl


def test_stage_runs_before_redaction():
    cfg = load_default_config()
    cfg["tokenization"] = {"enabled": True}
    res = Pipeline(cfg).run("MRN: 4422119\nAssessment: stable", wrap=False)
    order = [s.id for s in res.stages]
    assert order.index("tokenize_phi") < order.index("phi_patterns")
    phi = next(s for s in res.stages if s.id == "phi_patterns")
    assert phi.matches == 0  # nothing left to redact after tokenization


def test_validate_config_tokenization():
    cfg = load_default_config()
    cfg["tokenization"] = {"enabled": True, "prefix": "ZZ"}
    errs, _ = validate_config(cfg)
    assert not errs
    cfg["tokenization"] = {"enabled": "yes"}
    errs, _ = validate_config(cfg)
    assert any("tokenization" in e for e in errs)
    cfg["tokenization"] = {"enabled": True, "prefix": "bad prefix!"}
    errs, _ = validate_config(cfg)
    assert any("prefix" in e for e in errs)


# -- store persistence -----------------------------------------------------------

@pytest.fixture()
def token_paths(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "TOKENS_DIR", data / "tokens")
    return data


def test_token_map_persistence_roundtrip(token_paths):
    mapping = {"MRN: 123": "[[T1]]", "Jane": "[[T2]]"}
    path = store.save_token_map(mapping, "test")
    maps = store.list_token_maps()
    assert len(maps) == 1 and maps[0]["count"] == 2
    loaded = store.load_token_map(path)
    assert loaded == mapping
    assert store.delete_token_map(path)
    assert store.list_token_maps() == []


def test_newest_token_map(token_paths):
    store.save_token_map({"a": "[[T1]]"}, "one")
    found = tok.newest_token_map()
    assert found is not None and found[1] == {"a": "[[T1]]"}


def test_untokenize_from_saved_map(token_paths):
    text = "MRN: 553311"
    out, mapping = tok.tokenize(text, [[r"MRN\s*[:#]?\s*\d+", ""]])
    store.save_token_map(mapping, "t")
    _path, m = tok.newest_token_map()
    restored, n = tok.untokenize(out, m)
    assert restored == text and n == 1
