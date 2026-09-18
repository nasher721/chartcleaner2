"""Tests for the optional medspaCy section detector (skipped when not installed)."""

import pytest

from chartcleaner import nlp_medspacy
from chartcleaner.engine import load_default_config, Pipeline

ms = pytest.mark.skipif(not nlp_medspacy.medspacy_available(),
                        reason="medspacy not installed")


@ms
def test_detects_common_sections():
    text = ("Hospital Course: stable day two.\n\n"
            "Assessment/Plan: continue antibiotics.\n\nfree text")
    sections = nlp_medspacy.medspacy_sections(text)
    titles = [t for t, _s, _e in sections]
    assert any("Hospital Course" in t for t in titles)
    assert any("Assessment" in t for t in titles)


@ms
def test_engine_header_stage_with_medspacy():
    cfg = load_default_config()
    cfg["headers_engine"] = "medspacy"
    result = Pipeline(cfg).run(
        "Brief Hospital Course: patient improved.\n\nAssessment/Plan: discharge.",
        wrap=False)
    assert "## " in result.text
    stage = next(s for s in result.stages if s.id == "headers")
    assert stage.matches >= 1


def test_engine_falls_back_when_medspacy_unavailable(monkeypatch):
    monkeypatch.setattr(nlp_medspacy, "medspacy_available", lambda: False)

    class Boom:
        def __call__(self, *_a, **_k):
            raise RuntimeError("medspaCy is not usable in this environment")

    monkeypatch.setattr(nlp_medspacy, "_get_stack", Boom())
    cfg = load_default_config()
    cfg["headers_engine"] = "medspacy"
    cfg["clinical_headers"] = ["Hospital Course"]
    result = Pipeline(cfg).run("Hospital Course:\nstable today.", wrap=False)
    assert "## Hospital Course" in result.text  # regex fallback fired


def test_validate_headers_engine_value():
    from chartcleaner.engine import validate_config
    cfg = load_default_config()
    cfg["headers_engine"] = "medspacy"
    errs, _ = validate_config(cfg)
    assert not errs
    cfg["headers_engine"] = "gpt"
    errs, _ = validate_config(cfg)
    assert any("headers_engine" in e for e in errs)
