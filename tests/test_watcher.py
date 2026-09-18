"""Tests for the watchdog-based folder watcher (real filesystem events)."""

import json
import time

import pytest

from chartcleaner import store, watcher
from chartcleaner.engine import load_default_config


@pytest.fixture()
def watch_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "STATS_FILE", data / "stats.jsonl")
    monkeypatch.setattr(store, "EXPORTS_DIR", data / "exports")
    monkeypatch.setattr(store, "BACKUPS_DIR", data / "backups")
    monkeypatch.setattr(store, "TOKENS_DIR", data / "tokens")
    monkeypatch.setattr(store, "PRESETS_DIR", tmp_path / "presets")
    watch = tmp_path / "inbox"
    out = tmp_path / "cleaned"
    watch.mkdir()
    return watch, out, data


def _wait_for(condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return False


def test_watcher_cleans_new_txt(watch_env):
    watch, out, data = watch_env
    fw = watcher.FolderWatcher(
        watcher.WatchSpec(watch_dir=watch, out_dir=out),
        load_default_config(), custom_dir=None)
    fw.start()
    try:
        (watch / "chart.txt").write_text(
            "Patient John Doe\nMRN: 1357902\nAssessment: stable", encoding="utf-8")
        assert _wait_for(lambda: (out / "chart_cleaned.txt").exists()), \
            f"output never appeared; status={fw.status.state} errors={fw.status.errors}"
        result = (out / "chart_cleaned.txt").read_text(encoding="utf-8")
        assert "1357902" not in result
        assert fw.status.processed >= 1
        # history recorded under watch:<folder>
        lines = [json.loads(l) for l in
                 (data / "stats.jsonl").read_text().splitlines() if l.strip()]
        assert any(r.get("source") == f"watch:{watch.name}" for r in lines)
    finally:
        fw.stop()


def test_watcher_never_watches_its_own_output(watch_env):
    watch, out, data = watch_env
    fw = watcher.FolderWatcher(
        watcher.WatchSpec(watch_dir=watch, out_dir=out),
        load_default_config(), custom_dir=None)
    fw.start()
    try:
        (watch / "one.txt").write_text("MRN: 246813\nAssessment: ok", encoding="utf-8")
        assert _wait_for(lambda: fw.status.processed >= 1)
        time.sleep(1.5)  # give any (buggy) re-processing a chance
        assert fw.status.processed == 1
    finally:
        fw.stop()


def test_watcher_requires_existing_dir(tmp_path):
    with pytest.raises(NotADirectoryError):
        watcher.FolderWatcher(
            watcher.WatchSpec(watch_dir=tmp_path / "missing", out_dir=tmp_path),
            load_default_config())


def test_watch_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "WATCH_CONFIG_FILE", tmp_path / "watch.json")
    cfg = {"enabled": True, "watch_dir": "/tmp/x", "out_dir": "", "exts": [".txt"]}
    store.save_watch_config(cfg)
    loaded = store.load_watch_config()
    assert loaded["enabled"] is True and loaded["watch_dir"] == "/tmp/x"
    assert loaded["exts"] == [".txt"]
