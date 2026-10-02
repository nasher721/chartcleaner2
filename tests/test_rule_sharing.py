from copy import deepcopy

import pytest

from chartcleaner.engine import load_default_config
from chartcleaner.rule_sharing import (
    RuleSharingPanel, apply_import, export_csv, export_json, import_csv, import_json,
    payload_from_config, preview_import, validate_payload,
)


def test_merge_preserves_order_and_existing_conflicts_without_mutating_config():
    cfg = load_default_config()
    cfg["learned_rules"] = [["foo", "A"], ["keep", "X"], ["keep", "X"]]
    cfg["stage_options"] = {"learned_rules": {"enabled": False}}
    before = deepcopy(cfg)
    payload = payload_from_config({"learned_rules": [["foo", "B"], ["new", "N"], ["new", "N"]]})
    preview = preview_import(cfg, payload)
    assert preview["added"] == 1
    assert preview["duplicates"] == 1
    assert preview["rule_conflicts"] == 1
    assert preview["enables_learned_rules"]
    result = apply_import(cfg, payload)
    assert result["learned_rules"] == cfg["learned_rules"] + [["new", "N"]]
    assert result["stage_options"]["learned_rules"]["enabled"] is True
    assert cfg == before


def test_json_roundtrip_and_csv_preserves_abbreviations_in_both_modes(tmp_path):
    cfg = load_default_config()
    cfg["learned_rules"] = [["foo", "bar"], ["bar", ""]]
    cfg["abbreviations"] = {"disabled": ["artery"], "custom": [
        {"term": "special phrase", "replacement": "SP", "enabled": False},
    ]}
    json_payload = import_json(export_json(cfg, tmp_path / "rules.json"))
    target = apply_import(load_default_config(), json_payload, "replace")
    assert target["learned_rules"] == cfg["learned_rules"]
    assert target["abbreviations"] == cfg["abbreviations"]
    csv_payload = import_csv(export_csv(cfg, tmp_path / "rules.csv"))
    assert "abbreviations" not in csv_payload
    for mode in ("merge", "replace"):
        assert apply_import(cfg, csv_payload, mode)["abbreviations"] == cfg["abbreviations"]


def test_abbreviation_merge_keeps_existing_conflict_and_unions_disabled():
    cfg = load_default_config()
    cfg["abbreviations"] = {"disabled": ["artery"], "custom": [
        {"term": "my term", "replacement": "OLD", "enabled": True},
    ]}
    payload = payload_from_config({"abbreviations": {
        "disabled": ["hypertension"], "custom": [
            {"term": "MY TERM", "replacement": "NEW", "enabled": True},
            {"term": "added", "replacement": "ADD", "enabled": False},
        ]}})
    assert preview_import(cfg, payload)["abbreviation_conflicts"] == 1
    merged = apply_import(cfg, payload)["abbreviations"]
    assert merged["disabled"] == ["artery", "hypertension"]
    assert [x["replacement"] for x in merged["custom"]] == ["OLD", "ADD"]
    assert preview_import(cfg, payload, "replace")["abbreviation_conflicts"] == 0


@pytest.mark.parametrize("mode", ["merge", "replace"])
def test_conflicting_patterns_within_one_file_are_reported_and_skipped(mode):
    cfg = load_default_config()
    payload = payload_from_config({"learned_rules": [["foo", "A"], ["foo", "B"], ["foo", "A"]]})
    preview = preview_import(cfg, payload, mode)
    assert preview["rule_conflicts"] == 1
    assert preview["duplicates"] == 1
    assert preview["result"] == 1
    assert apply_import(cfg, payload, mode)["learned_rules"] == [["foo", "A"]]


@pytest.mark.parametrize("bad", [
    {"version": True}, {"version": 0},
    {"rules": [{"mode": "replace", "pattern": "x", "replacement": r"\9"}]},
    {"abbreviations": []},
    {"abbreviations": {"custom": [{"term": "a", "replacement": ""}]}},
    {"abbreviations": {"custom": [{"term": " a ", "replacement": "A"},
                                   {"term": "A", "replacement": "B"}]}},
])
def test_invalid_import_is_rejected_before_preview_or_save(bad):
    cfg = load_default_config()
    saved = []
    panel = RuleSharingPanel(lambda: cfg, saved.append)
    payload = {**payload_from_config(cfg), **bad}
    with pytest.raises(ValueError):
        validate_payload(payload)
    with pytest.raises(ValueError):
        panel.apply(payload)
    assert saved == []
