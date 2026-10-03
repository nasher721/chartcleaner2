"""Prompt templates for 'Copy as prompt'."""

from datetime import date

import pytest

from chartcleaner.engine import load_default_config, validate_config
from chartcleaner.prompt_templates import DEFAULT_TEMPLATES, get, render, templates

DAY = date(2026, 10, 3)


def test_builtins_render_with_chart_and_date():
    names = [t["name"] for t in DEFAULT_TEMPLATES]
    assert names == ["Progress note", "Sign-out / handoff", "Assessment & plan only", "Discharge summary"]
    out = render("progress note", "Note Date: 10/01/2026\nStable.\n", today=DAY)
    assert "(2026-10-03)" in out and "<patient_chart>" in out  # xml format


def test_placeholders_inside_the_chart_are_left_alone():
    cfg = {"prompt_templates": [{"name": "T", "template": "[{chart}] {date}"}]}
    assert render("T", "a {date} {chart} b", cfg, today=DAY) == "[a {date} {chart} b] 2026-10-03"


def test_user_templates_add_and_replace():
    cfg = {"prompt_templates": [{"name": "Discharge summary", "template": "mine {chart}"},
                                {"name": "Extra", "template": "{delta}", "format": "weird"}]}
    assert get("Discharge summary", cfg)["template"] == "mine {chart}"
    assert get("extra", cfg)["format"] == "text"
    assert len(templates(cfg)) == 5
    with pytest.raises(KeyError):
        get("Nope", cfg)


@pytest.mark.parametrize("bad", [
    {"name": "", "template": "x"}, {"name": "x"}, {"name": "x", "template": "y", "format": "pdf"},
])
def test_invalid_templates_rejected(bad):
    assert validate_config({**load_default_config(), "prompt_templates": [bad]})[0]
