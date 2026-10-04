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
                       "PREFS_FILE": data / "prefs.json",
                       "RECENT_DIR": data / "recent", "KNOWN_GOOD_DIR": data / "known_good",
                       "INBOX_STATE_FILE": data / "inbox_state.json",
                       "EDIT_LOG_FILE": data / "edit_log.enc",
                       "SIGNING_KEY_FILE": data / "signing_key.enc",
                       "TRUSTED_KEYS_FILE": data / "trusted_keys.json"}.items():
        monkeypatch.setattr(store, name, path)
    # Encryption keys go to a private file in tmp, never the real Keychain/DPAPI.
    from chartcleaner import secure_store
    monkeypatch.setenv(secure_store.BACKEND_ENV, "file")
    monkeypatch.setattr(secure_store, "key_dir", lambda: data)
    monkeypatch.setattr(rule_examples, "examples_path", lambda: tmp_path / "rule_examples.jsonl")
