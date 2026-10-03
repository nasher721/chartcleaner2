pytest_plugins = ["nicegui.testing.user_plugin"]


import pytest


@pytest.fixture(autouse=True)
def _isolate_rule_examples(tmp_path, monkeypatch):
    """Tests that teach rules must never write the user's data/rule_examples.jsonl."""
    from chartcleaner import rule_examples
    monkeypatch.setattr(rule_examples, "examples_path", lambda: tmp_path / "rule_examples.jsonl")
