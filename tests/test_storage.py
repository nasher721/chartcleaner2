"""Focused frozen-data migration and configuration safety checks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from chartcleaner.paths import user_data_dir
from chartcleaner import storage
from chartcleaner.storage import migrate_config, migrate_portable_data


def valid(cfg):
    return ([], []) if cfg.get("ok") else (["invalid"], [])


def test_platform_user_data_paths(tmp_path):
    assert user_data_dir(platform="darwin", home=tmp_path) == \
        tmp_path / "Library/Application Support/Chart Cleaner"
    assert user_data_dir(platform="win32", home=tmp_path,
                         env={"LOCALAPPDATA": str(tmp_path / "Local")}) == \
        tmp_path / "Local/Chart Cleaner"


def test_portable_migration_is_atomic_and_preserves_source(tmp_path):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir()
    (source / "config.json").write_bytes(b'{"ok": true}\n')
    (source / "data").mkdir()
    (source / "data" / "history.jsonl").write_bytes(b"sentinel\n")
    (source / "presets").mkdir()
    (source / "presets" / "rounds.json").write_bytes(b"preset\n")
    (source / "custom_rules").mkdir()
    (source / "custom_rules" / "rule.py").write_bytes(b"rule\n")
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    assert migrate_portable_data(source, destination, valid)
    assert (destination / "config.json").read_bytes() == before[Path("config.json")]
    assert (destination / "data/history.jsonl").read_bytes() == b"sentinel\n"
    assert (destination / "presets/rounds.json").read_bytes() == b"preset\n"
    assert (destination / "custom_rules/rule.py").read_bytes() == b"rule\n"
    assert {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()} == before


def test_existing_destination_wins_without_merge(tmp_path):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir(); destination.mkdir()
    (source / "config.json").write_text('{"ok": true}')
    (destination / "config.json").write_text('{"ok": true, "user": 1}')
    assert not migrate_portable_data(source, destination, valid)
    assert json.loads((destination / "config.json").read_text())["user"] == 1


def test_malformed_config_and_unsafe_names_are_untouched(tmp_path):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir()
    (source / "config.json").write_text("not json")
    assert not migrate_portable_data(source, destination, valid)

    (source / "config.json").write_text('{"ok": true}')
    (source / "data").mkdir()
    (source / "data" / "outside").symlink_to(tmp_path)
    assert not migrate_portable_data(source, destination, valid)


def test_symlink_source_root_is_rejected(tmp_path):
    external = tmp_path / "external"
    external.mkdir(); (external / "config.json").write_text('{"ok": true}')
    source = tmp_path / "portable"
    source.symlink_to(external, target_is_directory=True)
    assert not migrate_portable_data(source, tmp_path / "user", valid)


def test_reparse_source_root_is_rejected_independent_of_platform(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir(); (source / "config.json").write_text('{"ok": true}')
    monkeypatch.setattr(storage, "_is_reparse", lambda path, info: path == source)
    assert not migrate_portable_data(source, destination, valid)


@pytest.mark.skipif(sys.platform != "win32", reason="junction semantics are Windows-only")
def test_windows_junction_source_root_is_rejected(tmp_path):
    external = tmp_path / "external"; external.mkdir()
    config = external / "config.json"
    config.write_bytes(b'{"ok": true}\n')
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(source), str(external)],
                   check=True, capture_output=True)
    assert not migrate_portable_data(source, destination, valid)
    assert not destination.exists()
    assert config.read_bytes() == b'{"ok": true}\n'


def test_interrupted_copy_leaves_destination_absent(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir(); (source / "config.json").write_text('{"ok": true}')
    (source / "data").mkdir(); (source / "data/file").write_text("x")

    def fail(*args, **kwargs):
        raise OSError("interrupted")

    monkeypatch.setattr("chartcleaner.storage._copy_file_nofollow", fail)
    assert not migrate_portable_data(source, destination, valid)
    assert not destination.exists()
    assert (source / "data/file").read_text() == "x"


def test_config_migration_backs_up_before_atomic_write(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b'{"ok": true, "old": 1}\n')
    assert migrate_config(path, valid, lambda cfg: {**cfg, "new": 2})
    assert path.with_suffix(".json.bak").read_bytes() == b'{"ok": true, "old": 1}\n'
    assert json.loads(path.read_text())["new"] == 2


def test_config_migration_failure_retains_previous_bytes(tmp_path):
    path = tmp_path / "config.json"
    original = b'{"ok": true, "old": 1}\n'
    path.write_bytes(original)
    assert not migrate_config(path, valid, lambda cfg: {"ok": False})
    assert path.read_bytes() == original


@pytest.mark.parametrize("loop_at", ["source", "destination"])
def test_portable_symlink_loop_is_a_preserved_failure(tmp_path, loop_at):
    source, destination = tmp_path / "portable", tmp_path / "user"
    if loop_at == "source":
        source.symlink_to(source.name, target_is_directory=True)
    else:
        source.mkdir()
        (source / "config.json").write_bytes(b'{"ok": true}\n')
        destination.symlink_to(destination.name, target_is_directory=True)
    assert not migrate_portable_data(source, destination, valid)
    assert (source if loop_at == "source" else destination).is_symlink()
    if loop_at == "destination":
        assert (source / "config.json").read_bytes() == b'{"ok": true}\n'


def test_config_noop_preserves_formatting_and_existing_backup(tmp_path):
    path = tmp_path / "config.json"
    original = b'{ "ok": true, "learned_rules": [] }\r\n'
    path.write_bytes(original)
    backup = path.with_suffix(".json.bak")
    assert migrate_config(path, valid, lambda cfg: cfg)
    assert path.read_bytes() == original
    assert not backup.exists()
    backup.write_bytes(b"previous migration")
    assert migrate_config(path, valid)
    assert path.read_bytes() == original
    assert backup.read_bytes() == b"previous migration"


def test_config_inplace_migration_is_detected_and_backed_up(tmp_path):
    path = tmp_path / "config.json"
    original = b'{ "ok": true }\n'
    path.write_bytes(original)

    def update(cfg):
        cfg["new"] = 2
        return cfg

    assert migrate_config(path, valid, update)
    assert json.loads(path.read_bytes())["new"] == 2
    assert path.with_suffix(".json.bak").read_bytes() == original


@pytest.mark.parametrize("link_at", ["config.json", "config.json.bak"])
def test_config_migration_rejects_links_without_changing_external_bytes(tmp_path, link_at):
    path = tmp_path / "config.json"
    external = tmp_path / "external.json"
    original = b'{ "ok": true }\n'
    external.write_bytes(original)
    if link_at != path.name:
        path.write_bytes(original)
    link = tmp_path / link_at
    link.symlink_to(external)
    assert not migrate_config(path, valid, lambda cfg: {**cfg, "new": 2})
    assert link.is_symlink()
    assert path.read_bytes() == external.read_bytes() == original


def test_config_migration_rejects_reparse_file_before_reading(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = b'{ "ok": true }\n'
    path.write_bytes(original)
    monkeypatch.setattr(storage, "_is_reparse", lambda candidate, info: candidate == path)
    assert not migrate_config(path, valid, lambda cfg: {**cfg, "new": 2})
    assert path.read_bytes() == original
    assert not path.with_suffix(".json.bak").exists()


@pytest.mark.parametrize("failure_at", ["migrate", "serialize", "backup", "replace"])
def test_config_failures_preserve_original_bytes(tmp_path, monkeypatch, failure_at):
    path = tmp_path / "config.json"
    original = b'{ "ok": true }\r\n'
    path.write_bytes(original)

    def fail(*args, **kwargs):
        raise RuntimeError("migration failed") if failure_at == "migrate" else OSError("write failed")

    migration = lambda cfg: {**cfg, "new": object() if failure_at == "serialize" else 2}
    if failure_at == "migrate":
        migration = fail
    elif failure_at == "backup":
        monkeypatch.setattr(storage.shutil, "copy2", fail)
    elif failure_at == "replace":
        monkeypatch.setattr(storage.os, "replace", fail)
    assert not migrate_config(path, valid, migration)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_regular_file_cannot_replace_required_directory(tmp_path):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir()
    (source / "config.json").write_text('{"ok": true}')
    (source / "data").write_text("not a directory")
    assert not migrate_portable_data(source, destination, valid)
    assert not destination.exists()
    assert (source / "data").read_text() == "not a directory"


def test_existing_empty_destination_is_replaced_atomically(tmp_path):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir(); destination.mkdir()
    (source / "config.json").write_text('{"ok": true}')
    assert migrate_portable_data(source, destination, valid)
    assert (destination / "config.json").read_text() == '{"ok": true}'


def test_existing_empty_destination_is_restored_when_install_fails(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir(); destination.mkdir()
    (source / "config.json").write_text('{"ok": true}')
    real_replace = __import__("os").replace

    def fail_install(src, dst):
        if (dst == str(destination) or dst == destination) and "old-" not in str(src):
            raise OSError("locked")
        return real_replace(src, dst)

    monkeypatch.setattr("chartcleaner.storage.os.replace", fail_install)
    assert not migrate_portable_data(source, destination, valid)
    assert destination.is_dir() and not any(destination.iterdir())


def test_failed_atomic_install_leaves_source_and_destination_unchanged(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    destination = tmp_path / "user"
    source.mkdir(); (source / "config.json").write_bytes(b'{"ok": true}\n')
    monkeypatch.setattr("chartcleaner.storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("locked")))
    assert not migrate_portable_data(source, destination, valid)
    assert not destination.exists()
    assert (source / "config.json").read_bytes() == b'{"ok": true}\n'


def test_unwritable_destination_parent_is_a_preserved_failure(tmp_path):
    source = tmp_path / "portable"
    source.mkdir(); (source / "config.json").write_text('{"ok": true}')
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory")
    assert not migrate_portable_data(source, blocked_parent / "user", valid)
    assert (source / "config.json").read_text() == '{"ok": true}'


def test_validator_exception_is_a_preserved_failure(tmp_path):
    source = tmp_path / "portable"
    source.mkdir(); (source / "config.json").write_text('{"ok": true}')

    def broken(_):
        raise RuntimeError("validator crashed")

    assert not migrate_portable_data(source, tmp_path / "user", broken)


def test_frozen_startup_runs_portable_and_config_migration(tmp_path, monkeypatch):
    from chartcleaner import store

    mutable = tmp_path / "user"
    source = tmp_path / "portable"
    source.mkdir(); (source / "config.json").write_text('{"ok": true}')
    dirs = [mutable / "data", mutable / "data/exports", mutable / "presets",
            mutable / "custom_rules", mutable / "data/backups", mutable / "data/tokens"]
    monkeypatch.setattr(store.paths, "is_frozen", lambda: True)
    monkeypatch.setattr(store.paths, "portable_root", lambda: source)
    monkeypatch.setattr(store, "MUTABLE_DIR", mutable)
    monkeypatch.setattr(store, "CONFIG_PATH", mutable / "config.json")
    monkeypatch.setattr(store, "DATA_DIR", mutable / "data")
    monkeypatch.setattr(store, "EXPORTS_DIR", mutable / "data/exports")
    monkeypatch.setattr(store, "PRESETS_DIR", mutable / "presets")
    monkeypatch.setattr(store, "CUSTOM_RULES_DIR", mutable / "custom_rules")
    monkeypatch.setattr(store, "BACKUPS_DIR", mutable / "data/backups")
    monkeypatch.setattr(store, "TOKENS_DIR", mutable / "data/tokens")
    monkeypatch.setattr(store, "_MIGRATION_CHECKED", False)
    monkeypatch.setattr("chartcleaner.engine.validate_config", lambda cfg: ([], []))

    store.ensure_dirs()
    assert (mutable / "config.json").exists()
    assert json.loads((mutable / "config.json").read_text())["learned_rules"] == []
    assert all(d.is_dir() for d in dirs)

    config = mutable / "config.json"
    backup = mutable / "config.json.bak"
    before = config.read_bytes(), backup.read_bytes()
    monkeypatch.setattr(store, "_MIGRATION_CHECKED", False)
    store.ensure_dirs()
    assert (config.read_bytes(), backup.read_bytes()) == before
