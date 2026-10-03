import pytest

from chartcleaner.engine import Pipeline, load_default_config, validate_config


@pytest.mark.parametrize("group", [
    None, [], {"disabled": "term"}, {"disabled": [""]}, {"custom": "term"},
    {"custom": [{"term": "x", "replacement": ""}]},
    {"custom": [{"term": "x", "replacement": "X", "enabled": "false"}]},
    {"custom": [{"term": "x", "replacement": "X"}, {"term": "X", "replacement": "Y"}]},
    {"custom": [{"term": "x", "replacement": "X", "acknowledged": "yes"}]},
    {"custom": [{"term": "x", "replacement": "X", "pack": 3}]},
])
def test_invalid_abbreviation_settings_rejected(group):
    cfg = {**load_default_config(), "abbreviations": group}
    errors, _ = validate_config(cfg)
    assert any("abbreviations" in error for error in errors)


def test_custom_abbreviations_apply_in_both_pipeline_modes():
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    cfg["literal_replacements"] = []
    cfg["abbreviations"] = {"disabled": ["Anterior cerebral artery"], "custom": [
        {"term": "special custom phrase", "replacement": "SCP", "enabled": True},
    ]}
    for mode in ("clean", "abbreviations"):
        result = Pipeline(cfg, mode=mode).run("Anterior cerebral artery; special custom phrase", wrap=False)
        assert result.text == "Anterior cerebral artery; SCP"


def test_invalid_replacement_rejected_before_save():
    cfg = {**load_default_config(), "learned_rules": [["literal", r"\9"]]}
    assert any("replacement" in error for error in validate_config(cfg)[0])


async def test_supported_upload_event_formats():
    from io import BytesIO
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app import read_upload

    modern = SimpleNamespace(file=SimpleNamespace(name="rules.json", read=AsyncMock(return_value=b"{}")))
    legacy = SimpleNamespace(name="rules.json", content=BytesIO(b"{}"))
    assert await read_upload(modern) == await read_upload(legacy) == ("rules.json", b"{}")
