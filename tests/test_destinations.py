import json
from pathlib import Path

import pytest

from chartcleaner.destinations import DESTINATIONS, LOSSY_STAGES, apply_destination
from chartcleaner.engine import Pipeline, load_config, validate_config
from chartcleaner import service


def default_config():
    return load_config(Path(__file__).parents[1] / "config.json")


def test_no_destination_preserves_legacy_config_and_object():
    cfg = default_config()
    resolved, policy = apply_destination(cfg, None)
    assert resolved is cfg
    assert policy is None


def test_unknown_destination_fails_before_cleaning():
    with pytest.raises(ValueError, match="Unknown destination"):
        apply_destination(default_config(), "public_website")


@pytest.mark.parametrize("key", sorted(DESTINATIONS))
def test_every_destination_enforces_validation_without_mutating_saved_config(key):
    cfg = default_config()
    cfg["cleaning_validation"] = {"mode": "off"}
    before = json.dumps(cfg, sort_keys=True)
    resolved, policy = apply_destination(cfg, key)
    assert policy.key == key
    assert resolved["cleaning_validation"]["mode"] == "enforce"
    errors, _warnings = validate_config(resolved)
    assert not errors
    assert json.dumps(cfg, sort_keys=True) == before


def test_external_ai_disables_intentional_detail_loss():
    cfg = default_config()
    cfg.setdefault("stage_options", {})["lab_compaction"] = {"enabled": True}
    cfg["duplicate_note_detection"] = {"enabled": True}
    resolved, _ = apply_destination(cfg, "external_ai")
    for stage_id in LOSSY_STAGES:
        assert resolved["stage_options"][stage_id]["enabled"] is False
    assert resolved["duplicate_note_detection"]["enabled"] is False
    assert resolved["abbreviations"]["level"] == "conservative"
    disabled = {stage.id for stage in Pipeline(resolved).stages if not stage.enabled}
    assert "lab_compaction" in disabled
    assert "duplicate_notes" in disabled


def test_personal_review_retains_configured_condensing():
    cfg = default_config()
    cfg.setdefault("stage_options", {})["lab_compaction"] = {"enabled": True}
    resolved, _ = apply_destination(cfg, "personal_review")
    assert resolved["stage_options"]["lab_compaction"]["enabled"] is True


def test_service_reports_destination_policy(tmp_path):
    result = service.clean("Assessment: stable.\n", config=default_config(),
                           destination="external_ai", record=False, custom_dir=tmp_path)
    assert result["destination"]["key"] == "external_ai"
    assert result["destination"]["validation_mode"] == "enforce"
    assert result["validation"]["decision"] in ("accept", "retain_original", "withhold_release")


def test_cli_exposes_destination_policy():
    from medical_cleaner import build_parser
    args = build_parser().parse_args(["--destination", "epic_paste", "--stdin", "--stdout"])
    assert args.destination == "epic_paste"
