"""Both cleaning modes use the bundled dictionary, with isolated side effects."""

import pytest

from chartcleaner.engine import BUILTIN_STAGE_IDS, Pipeline, clean_text, load_default_config


def test_existing_config_gets_abbreviations_after_cleaning_rules():
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    cfg["stage_order"] = [s for s in BUILTIN_STAGE_IDS if s != "medical_abbreviations"]
    pipe = Pipeline(cfg)
    order = [s.id for s in pipe.stages]
    assert order.index("medical_abbreviations") == order.index("line_length") - 1
    result = pipe.run("Heart failure with reduced ejection fraction and anterior cerebral artery.", wrap=False)
    assert result.text == "HFrEF and ACA."
    assert not result.warnings
    assert next(s for s in result.stages if s.id == "medical_abbreviations").matches == 2


@pytest.mark.parametrize("legacy_order", [
    ["line_length"],
    ["line_length"] + [s for s in BUILTIN_STAGE_IDS
                       if s not in {"medical_abbreviations", "line_length"}],
])
def test_legacy_early_line_length_keeps_abbreviations_after_cleaning_rules(legacy_order):
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    cfg["stage_order"] = legacy_order
    cfg["learned_rules"] = [["Anterior cerebral artery", "matched original term"]]
    pipe = Pipeline(cfg)
    order = [s.id for s in pipe.stages]
    assert order.index("medical_abbreviations") > order.index("learned_rules")
    assert order.index("medical_abbreviations") > order.index("phi_patterns")
    assert order.index("medical_abbreviations") > order.index("line_length")
    assert pipe.run("Anterior cerebral artery", wrap=False).text == "matched original term"


def test_explicit_abbreviation_position_is_preserved():
    cfg = load_default_config()
    cfg["stage_order"] = ["medical_abbreviations"] + [
        s for s in BUILTIN_STAGE_IDS if s != "medical_abbreviations"]
    assert [s.id for s in Pipeline(cfg).stages] == cfg["stage_order"]


def test_rules_see_original_terms_before_abbreviating():
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    cfg["learned_rules"] = [["quiet hours testing", ""]]
    cfg["clinical_headers"] = ["Oncology", "Imaging"]
    cfg["section_filter"] = {"mode": "drop", "sections": ["Oncology"]}
    result = Pipeline(cfg).run(
        "Oncology\nremove this section\nImaging\nquiet hours testing anterior cerebral artery", wrap=False)
    assert "remove this section" not in result.text
    assert "quiet" not in result.text
    assert "ACA" in result.text


def test_abbreviations_run_before_long_line_wrapping():
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    cfg["line_length"] = {"mode": "wrap", "max_chars": 20}
    assert Pipeline(cfg).run("Anterior cerebral artery", wrap=False).text == "ACA"


def test_abbreviations_only_preserves_everything_else_and_skips_scripts(tmp_path):
    marker = tmp_path / "custom-imported"
    (tmp_path / "destructive.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        "def clean(text, ctx): return ''\n")
    cfg = load_default_config()
    cfg["stage_options"] = {"medical_abbreviations": {"enabled": False}}
    cfg["literal_replacements"] = [["ACA", "CHANGED"]]
    cfg["learned_rules"] = [["MRN.*", ""]]
    raw = "  MRN: 1234567\r\n• Anterior cerebral artery  \r\n\r\n\tunchanged  "
    result = clean_text(raw, cfg, custom_dir=tmp_path, wrap=True, mode="abbreviations")
    assert result.text == "  MRN: 1234567\r\n• ACA  \r\n\r\n\tunchanged  "
    assert not marker.exists()
    assert not result.wrapped and not result.warnings and not result.phi_counts()
    assert [s.id for s in result.stages] == ["medical_abbreviations"]
    assert result.stages[0].matches == 1


def test_dictionary_stage_can_be_disabled_in_full_clean():
    cfg = load_default_config()
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    cfg["stage_options"] = {"medical_abbreviations": {"enabled": False}}
    result = Pipeline(cfg).run("Anterior cerebral artery", wrap=False)
    assert result.text == "Anterior cerebral artery"
    assert next(s for s in result.stages if s.id == "medical_abbreviations").skipped


def test_abbreviations_only_does_not_require_cleaning_config():
    assert Pipeline({}, mode="abbreviations").run("Hypertension").text == "HTN"
    with pytest.raises(ValueError, match="Unknown cleaning mode"):
        Pipeline({}, mode="typo")
