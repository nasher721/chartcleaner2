"""Tests for store persistence: audit hits, suggestions, config backups.

Every test monkeypatches the store's path constants onto a tmp_path so the
real data/ directory is never touched.
"""

import json
import time

import pytest

from chartcleaner import store
from chartcleaner.engine import load_default_config


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(store, "DATA_DIR", data)
    monkeypatch.setattr(store, "AUDIT_HITS_FILE", data / "audit_hits.jsonl")
    monkeypatch.setattr(store, "SUGGESTIONS_STATE_FILE", data / "suggestions_state.json")
    monkeypatch.setattr(store, "BACKUPS_DIR", data / "backups")
    config = tmp_path / "config.json"
    monkeypatch.setattr(store, "CONFIG_PATH", config)
    return {"data": data, "config": config}


def _ts(offset_days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - offset_days * 86400))


# -- audit hits -----------------------------------------------------------------

def test_append_and_load_hits(paths):
    store.append_audit_hits(["long_digits", "long_digits", "date_like"])
    store.append_audit_hits([])  # empty write is skipped
    hits = store.load_audit_hits()
    assert len(hits) == 1
    assert hits[0]["sigs"] == ["date_like", "long_digits"]


# -- suggestions ----------------------------------------------------------------

def test_suggestions_need_min_runs(paths):
    for _ in range(3):
        store.append_audit_hits(["long_digits"])
    store.append_audit_hits(["date_like"])
    out = {s["signature"]: s["runs"] for s in store.get_suggestions(min_runs=3)}
    assert out == {"long_digits": 3}
    out2 = {s["signature"]: s["runs"] for s in store.get_suggestions(min_runs=1)}
    assert out2 == {"long_digits": 3, "date_like": 1}


def test_suggestions_respect_days_window(paths):
    old = {"ts": _ts(45), "sigs": ["old_sig"]}
    fresh = {"ts": _ts(1), "sigs": ["fresh_sig"]}
    store.AUDIT_HITS_FILE.write_text(json.dumps(old) + "\n" + json.dumps(fresh) + "\n",
                                     encoding="utf-8")
    sigs = {s["signature"] for s in store.get_suggestions(days=30, min_runs=1)}
    assert sigs == {"fresh_sig"}


def test_dismissed_suggestions_never_return(paths):
    store.append_audit_hits(["long_digits"])
    store.dismiss_suggestion("long_digits")
    assert store.get_suggestions(min_runs=1) == []


# -- backups --------------------------------------------------------------------

def test_rotate_backup_creates_and_keeps_five(paths):
    cfg = load_default_config()
    paths["config"].write_text(json.dumps(cfg), encoding="utf-8")
    bdir = store.BACKUPS_DIR
    bdir.mkdir(parents=True, exist_ok=True)
    for i in range(1, 8):  # seven pre-existing snapshots with distinct names
        (bdir / f"config-2020010{i}-00000{i}.json").write_text(json.dumps({"n": i}),
                                                               encoding="utf-8")
    store.rotate_config_backup()  # adds today's copy, then prunes to the newest 5
    backups = store.list_config_backups()
    assert len(backups) == store.BACKUPS_TO_KEEP
    assert backups[0]["rules"] == store._rule_count(cfg)  # newest = today's copy
    names = [b["file"] for b in backups]
    assert names == sorted(names, reverse=True)  # newest first


def test_rotate_backup_without_config_is_none(paths):
    assert store.rotate_config_backup() is None


def test_restore_round_trip(paths):
    cfg = load_default_config()
    paths["config"].write_text(json.dumps(cfg), encoding="utf-8")
    b = store.rotate_config_backup()
    paths["config"].write_text("{}", encoding="utf-8")  # clobber live config
    ok, msg = store.restore_config_backup(b)
    assert ok, msg
    assert json.loads(paths["config"].read_text(encoding="utf-8"))["emr_line_metadata"] == cfg["emr_line_metadata"]


def test_restore_rejects_corrupt_backup(paths):
    bad = paths["data"] / "backups"
    bad.mkdir(parents=True)
    corrupt = bad / "config-20200101-000000.json"
    corrupt.write_text("{ not json", encoding="utf-8")
    ok, msg = store.restore_config_backup(corrupt)
    assert not ok and "unreadable" in msg.lower()


def test_restore_rejects_invalid_rules(paths):
    bad = paths["data"] / "backups"
    bad.mkdir(parents=True)
    broken = bad / "config-20200101-000001.json"
    broken.write_text(json.dumps({**load_default_config(),
                                  "emr_line_metadata": ["(?im)[unclosed"]}), encoding="utf-8")
    ok, msg = store.restore_config_backup(broken)
    assert not ok and "invalid" in msg.lower()
