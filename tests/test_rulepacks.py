"""Tests for rule packs, evaluation harness, and the synthetic benchmark."""

import json

import pytest

from chartcleaner import benchmark, evaluate, rulepacks
from chartcleaner import store
from chartcleaner.engine import load_default_config, validate_config


# -- packs ----------------------------------------------------------------------

def test_list_packs_shipped():
    names = [p["name"] for p in rulepacks.list_packs()]
    assert "HIPAA Safe Harbor (strict)" in names
    assert "Philter core PHI (UCSF-inspired)" in names


def test_packs_are_valid_configs():
    for p in rulepacks.list_packs():
        cfg = rulepacks.load_pack(p["name"])
        assert "_pack" not in cfg
        errs, _ = validate_config(cfg)
        assert not errs, f"{p['name']}: {errs}"


def test_pack_lookup_by_display_name(tmp_path, monkeypatch):
    presets = tmp_path / "presets"
    monkeypatch.setattr(store, "PRESETS_DIR", presets)
    monkeypatch.setattr(store, "ensure_dirs", lambda: presets.mkdir(parents=True,
                                                                    exist_ok=True))
    name, msg = rulepacks.install_pack("Philter core PHI (UCSF-inspired)")
    assert "[pack]" in name
    assert any("Philter" in p for p in store.list_presets())


def test_apply_pack_unknown():
    ok, msg = rulepacks.apply_pack("No Such Pack")
    assert not ok


def test_hipaa_pack_catches_what_default_misses():
    samples = benchmark.generate(n=4, seed=7)
    base = evaluate.evaluate(load_default_config(), samples)
    hipaa = rulepacks.load_pack("HIPAA Safe Harbor (strict)")
    strict = evaluate.evaluate(hipaa, samples)
    assert strict["recall"] > base["recall"]


# -- benchmark -------------------------------------------------------------------

def test_benchmark_deterministic():
    a = benchmark.generate(n=3, seed=11)
    b = benchmark.generate(n=3, seed=11)
    assert [c["text"] for c in a] == [c["text"] for c in b]


def test_benchmark_labels_present():
    charts = benchmark.generate(n=2, seed=3)
    for c in charts:
        types = {p["type"] for p in c["phi"]}
        assert {"name", "mrn", "dob", "phone", "ssn"} <= types
        for p in c["phi"]:
            assert p["value"] in c["text"]


# -- evaluation -------------------------------------------------------------------

def test_evaluate_perfect_recall_on_trivial_rules():
    samples = [{"name": "s1", "text": "MRN: 111111 ok",
                "phi": [{"type": "mrn", "value": "111111"}]}]
    cfg = load_default_config()
    report = evaluate.evaluate(cfg, samples)
    assert report["recall"] == 100.0
    assert report["missed"] == []


def test_evaluate_missed_items_listed():
    # addresses are the canonical gap in the default rules (see the packs)
    samples = [{"name": "s1", "text": "Patient lives at 1418 Elm Dr, Georgetown.",
                "phi": [{"type": "address", "value": "1418 Elm Dr"}]}]
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False  # deterministic: regex only
    report = evaluate.evaluate(cfg, samples)
    assert report["recall"] == 0.0
    assert report["missed"][0]["value"] == "1418 Elm Dr"
    assert report["by_type"]["address"]["items"] == 1


def test_evaluation_save_load(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    samples = benchmark.generate(n=2, seed=5)
    rep = evaluate.evaluate(load_default_config(), samples)
    path = evaluate.save_evaluation(rep)
    assert path.exists()
    loaded = evaluate.load_last_evaluation()
    assert loaded["recall"] == rep["recall"]
    assert set(loaded["by_type"]) == set(rep["by_type"])
