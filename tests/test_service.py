"""The integration service layer behaves exactly like the app's Clean page."""

import json

import pytest

from chartcleaner import service, store
from chartcleaner.engine import Pipeline, load_default_config

TEXT = "Printed by Jane Doe on 01/02/2024\nPatient has Hypertension.\n"


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "STATS_FILE", data / "stats.jsonl")
    monkeypatch.setattr(store, "TOKENS_DIR", data / "tokens")
    monkeypatch.setattr(store, "EXPORTS_DIR", data / "exports")
    monkeypatch.setattr(store, "BACKUPS_DIR", data / "backups")
    monkeypatch.setattr(store, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(store, "CUSTOM_RULES_DIR", tmp_path / "custom_rules")
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_PATH", config)
    return {"data": data, "cfg": cfg}


def test_clean_matches_pipeline_and_records_run(paths):
    expected = Pipeline(paths["cfg"], custom_dir=None).run(TEXT).text
    out = service.clean(TEXT, source="api:test")
    assert out["text"] == expected
    assert out["chars_before"] == len(TEXT)
    runs = store.load_runs()
    assert [r["source"] for r in runs] == ["api:test"]


def test_clean_without_record_writes_nothing(paths):
    service.clean(TEXT, record=False)
    assert store.load_runs() == []


def test_clean_uses_named_preset(paths):
    preset = dict(paths["cfg"], literal_replacements=[["Patient", "PT-X"]])
    store.save_preset("mine", preset)
    assert "PT-X" in service.clean(TEXT, preset="mine", record=False)["text"]


def test_formats_and_unknown_format(paths):
    out = service.clean(TEXT, fmt="json", record=False, wrap=False)
    json.loads(out["text"])
    with pytest.raises(ValueError):
        service.clean(TEXT, fmt="pdf", record=False)


def test_abbreviate_only_touches_terms(paths):
    out = service.abbreviate(TEXT, source="api:test")
    assert out["text"] == TEXT.replace("Hypertension", "HTN")
    assert out["replacements"] == {"HTN": 1}
    assert store.load_runs()[0]["source"] == "api:test:abbreviations"


def test_ask_uses_injected_client(paths):
    class FakeClient:
        def is_available(self): return True
        def list_models(self): return ["llama3.1"]
        def generate(self, prompt, model="", system=None): return "BP 120/80."

    chart = "BP 120/80. Lisinopril 10 mg daily."
    res = service.ask("What is the BP?", chart, client=FakeClient())
    assert res["answer"] == "BP 120/80." and res["model"] == "llama3.1"
    assert 0.0 <= res["grounding_score"] <= 100.0
