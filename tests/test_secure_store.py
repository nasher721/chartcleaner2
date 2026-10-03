"""Encrypted token maps, retention and stored-data deletion."""

from __future__ import annotations

import json
import os
import stat
import sys
import time

import pytest

from chartcleaner import secure_store, store, tokens


def test_roundtrip_and_magic(tmp_path):
    path = tmp_path / "x.enc"
    secure_store.write_text(path, "Jane Doe → [[T1]]")
    raw = path.read_bytes()
    assert raw.startswith(secure_store.MAGIC)
    assert b"Jane" not in raw
    assert secure_store.read_text(path) == "Jane Doe → [[T1]]"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permissions")
def test_files_and_key_are_owner_only(tmp_path):
    path = tmp_path / "x.enc"
    secure_store.write_text(path, "secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    key = secure_store.key_dir() / ".datakey"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_plain_text_passes_through(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert secure_store.read_text(path) == '{"a": 1}'


def test_wrong_key_raises_decrypt_error(tmp_path, monkeypatch):
    path = tmp_path / "x.enc"
    secure_store.write_text(path, "secret")
    other = tmp_path / "other_machine"
    monkeypatch.setattr(secure_store, "key_dir", lambda: other)
    with pytest.raises(secure_store.DecryptError):
        secure_store.read_text(path)


def test_keystore_failure_falls_back_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv(secure_store.BACKEND_ENV, "keychain")
    monkeypatch.setattr(secure_store, "_keychain_key", lambda: (_ for _ in ()).throw(OSError("locked")))
    monkeypatch.setattr(secure_store, "_cache", {})
    secure_store.write_text(tmp_path / "x.enc", "hi")
    assert secure_store.read_text(tmp_path / "x.enc") == "hi"
    assert "key file" in secure_store.describe()


def test_token_maps_are_saved_encrypted():
    path = store.save_token_map({"Jane Doe": "[[T1]]"}, "test")
    assert path.suffix == ".enc"
    assert b"Jane" not in path.read_bytes()
    assert store.load_token_map(path) == {"Jane Doe": "[[T1]]"}
    assert store.list_token_maps()[0]["count"] == 1
    found = tokens.newest_token_map()
    assert found and found[1] == {"Jane Doe": "[[T1]]"}
    assert tokens.load_token_map(path) == {"Jane Doe": "[[T1]]"}


def test_legacy_plain_maps_still_load_and_get_encrypted():
    store.ensure_dirs()
    legacy = store.TOKENS_DIR / "tokens-20200101-000000.json"
    legacy.write_text(json.dumps({"ts": "2020", "map": {"MRN 1": "[[T1]]"}}), encoding="utf-8")
    assert store.load_token_map(legacy) == {"MRN 1": "[[T1]]"}
    assert store.encrypt_legacy_token_maps() == 1
    assert not legacy.exists()
    enc = legacy.with_suffix(".enc")
    assert b"MRN" not in enc.read_bytes()
    assert store.load_token_map(enc) == {"MRN 1": "[[T1]]"}


def test_unreadable_map_is_listed_not_fatal(monkeypatch, tmp_path):
    path = store.save_token_map({"a": "[[T1]]"})
    monkeypatch.setattr(secure_store, "key_dir", lambda: tmp_path / "elsewhere")
    assert store.list_token_maps() == [{"file": str(path), "ts": path.stem, "count": -1}]
    assert tokens.newest_token_map() is None


def _age(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_purge_removes_only_old_chart_data():
    store.ensure_dirs()
    old_map = store.save_token_map({"a": "[[T1]]"})
    _age(old_map, 30)
    batch = store.EXPORTS_DIR / "batch_20200101"
    batch.mkdir()
    (batch / "x_cleaned.txt").write_text("chart")
    _age(batch, 30)
    store.WATCHED_OUT_DIR.mkdir(parents=True)
    fresh = store.WATCHED_OUT_DIR / "new.txt"
    fresh.write_text("chart")
    store.append_run({"ts": "2020-01-01T00:00:00", "chars_before": 1})
    _age(store.STATS_FILE, 30)

    assert store.purge_old_data(14) == 2
    assert not old_map.exists() and not batch.exists()
    assert fresh.exists() and store.STATS_FILE.exists()
    assert store.purge_old_data(0) == 0


def test_delete_all_chart_data():
    store.save_token_map({"a": "[[T1]]"})
    store.save_export("chart", "x")
    assert store.delete_all_chart_data() == 2
    assert store.list_token_maps() == []


def test_prefs_keep_settings_page_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "PREFS_FILE", tmp_path / "prefs.json")
    prefs = store.load_prefs()
    prefs.update(notes_folder="/notes", retention_days=3, note_auto_apply=True,
                 clipboard_watcher={"enabled": True, "action": "auto"},
                 note_presets={"progress": "icu"})
    store.save_prefs(prefs)
    again = store.load_prefs()
    for key in ("notes_folder", "retention_days", "note_auto_apply", "clipboard_watcher",
                "note_presets"):
        assert again[key] == prefs[key], key


async def test_settings_card_saves_retention(user):
    from nicegui import ui

    await user.open("/settings")
    await user.should_see("Stored chart data")
    await user.should_see("Token maps are encrypted")
    box = user.find(marker="retention-days").elements.pop()
    box.set_value(3)
    assert store.load_prefs()["retention_days"] == 3
    assert isinstance(box, ui.number)
