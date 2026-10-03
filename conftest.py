pytest_plugins = ["nicegui.testing.user_plugin"]


import pytest


@pytest.fixture(autouse=True)
def _isolate_user_data(tmp_path, monkeypatch):
    """Tests must never touch the user's data/ folder: run history, config backups
    (only the newest five are kept, so test saves would push real ones out),
    exports, token maps, audit hits or learned-rule examples."""
    from chartcleaner import rule_examples, store
    data = tmp_path / "isolated_data"
    for name, path in {"STATS_FILE": data / "stats.jsonl", "BACKUPS_DIR": data / "backups",
                       "EXPORTS_DIR": data / "exports", "TOKENS_DIR": data / "tokens",
                       "AUDIT_HITS_FILE": data / "audit_hits.jsonl",
                       "SUGGESTIONS_STATE_FILE": data / "suggestions_state.json"}.items():
        monkeypatch.setattr(store, name, path)
    monkeypatch.setattr(rule_examples, "examples_path", lambda: tmp_path / "rule_examples.jsonl")
