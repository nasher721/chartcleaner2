"""CLI + service: --add-line / --add-line-start / --add-remove-clipboard,
--rule-changes and --undo-rule-change."""

from __future__ import annotations

import sys

import pytest

import medical_cleaner
from chartcleaner import rule_meta, rule_shapes, service, store
from chartcleaner.engine import load_config, load_default_config, save_config
from chartcleaner.highlight_rules import make_rule


@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    cfg = load_default_config()
    cfg["learned_rules"] = []
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "rotate_config_backup", lambda: None)
    return path


def _cli(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["clean-chart", *argv])
    with pytest.raises(SystemExit) as exc:
        medical_cleaner.main()
    return exc.value.code


def test_add_line_with_placeholders_and_line_start(cfg_path, monkeypatch, capsys):
    assert _cli(monkeypatch, "--add-line", "Signed by {name} on {date}",
                "--add-line-start", "Printed by") == 0
    out = capsys.readouterr().out
    assert "Added: Remove whole lines containing “Signed by {name} on {date}”" in out
    assert "Added: Remove lines starting with “Printed by”" in out
    cfg = load_config(cfg_path)
    shapes = [rule_meta.entry(cfg, p[0])["shape"] for p in cfg["learned_rules"]]
    assert shapes[0]["template"] and shapes[1]["kind"] == "line_start"
    assert rule_meta.entry(cfg, cfg["learned_rules"][0][0])["source"] == "cli"


def test_add_remove_clipboard(cfg_path, monkeypatch, capsys):
    import pyperclip
    monkeypatch.setattr(pyperclip, "paste", lambda: "Printed on\n\n  Page 1 of 2 \n")
    assert _cli(monkeypatch, "--add-remove-clipboard") == 0
    assert load_config(cfg_path)["learned_rules"] == [make_rule("Printed on"), make_rule("Page 1 of 2")]


def test_empty_clipboard_is_an_error(cfg_path, monkeypatch, capsys):
    import pyperclip
    monkeypatch.setattr(pyperclip, "paste", lambda: "  \n")
    assert _cli(monkeypatch, "--add-remove-clipboard") == 1
    assert "clipboard is empty" in capsys.readouterr().err


def test_rule_changes_and_undo(cfg_path, monkeypatch, capsys):
    assert _cli(monkeypatch, "--add-remove", "first") == 0
    assert _cli(monkeypatch, "--add-remove", "second") == 0
    capsys.readouterr()
    assert _cli(monkeypatch, "--rule-changes") == 0
    out = capsys.readouterr().out
    assert out.index("Remove “second”") < out.index("Remove “first”")
    assert _cli(monkeypatch, "--undo-rule-change") == 0
    assert "Undone: Added: Remove “second”" in capsys.readouterr().out
    assert load_config(cfg_path)["learned_rules"] == [make_rule("first")]
    first_id = next(c["id"] for c in service.rule_changes() if "first" in c["summary"])
    assert _cli(monkeypatch, "--undo-rule-change", first_id) == 0
    assert load_config(cfg_path)["learned_rules"] == []
    assert _cli(monkeypatch, "--undo-rule-change", "nope") == 1


def test_service_add_shape_rule_is_idempotent(cfg_path):
    shape = {"kind": "until", "text": "Patient Education", "until": "next_heading"}
    first = service.add_shape_rule(shape)
    again = service.add_shape_rule(shape)
    assert first["added"] and not again["added"]
    assert load_config(cfg_path)["learned_rules"] == [rule_shapes.build(shape)]
