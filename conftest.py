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
                       "SUGGESTIONS_STATE_FILE": data / "suggestions_state.json",
                       "WATCHED_OUT_DIR": data / "watched_out",
                       "PREFS_FILE": data / "prefs.json"}.items():
        monkeypatch.setattr(store, name, path)
    # Encryption keys go to a private file in tmp, never the real Keychain/DPAPI.
    from chartcleaner import secure_store
    monkeypatch.setenv(secure_store.BACKEND_ENV, "file")
    monkeypatch.setattr(secure_store, "key_dir", lambda: data)
    monkeypatch.setattr(rule_examples, "examples_path", lambda: tmp_path / "rule_examples.jsonl")
