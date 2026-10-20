"""Source-checkout updates (chartcleaner/git_update.py).

``_run_git`` is replaced in every test, so nothing here shells out or reaches
the network: the fake answers the same git questions a real checkout would.
"""
from __future__ import annotations

import json
import sys

import pytest

from chartcleaner import git_update as g

LOCAL = "1" * 40
REMOTE = "2" * 40
SEP = "\x1f"


def _commit_line(sha, subject, author="nasher721", date="2026-10-19T10:00:00-04:00"):
    return SEP.join((sha, subject, author, date))


class FakeGit:
    """Answers the git commands git_update issues for one imaginary checkout."""

    def __init__(self, root, *, head=LOCAL, upstream=REMOTE, dirty=(), incoming=(),
                 ahead=0, merge_rc=0, ls_remote_rc=0, requirements_changed=False,
                 post_merge_config=None, repo=True):
        self.root = root
        self.head = head
        self.upstream = upstream
        self.dirty = list(dirty)
        self.incoming = list(incoming)          # newest first, like git log
        self.ahead = ahead
        self.merge_rc = merge_rc
        self.ls_remote_rc = ls_remote_rc
        self.requirements_changed = requirements_changed
        self.post_merge_config = post_merge_config
        self.repo = repo
        self.calls: list[list[str]] = []

    def __call__(self, root, args, *, timeout=0.0):
        self.calls.append(list(args))
        args = list(args)
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return (0, str(self.root)) if self.repo else (128, "")
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return (0, "main")
        if args[:2] == ["rev-parse", "HEAD"]:
            return (0, self.head)
        if args[:2] == ["rev-parse", "FETCH_HEAD"]:
            return (0, self.upstream)
        if args[:2] == ["config", "--get"]:
            return (0, "https://github.com/nasher721/chartcleaner2.git")
        if args[:1] == ["status"]:
            return (0, "\n".join(self.dirty))
        if args[:1] == ["ls-remote"]:
            return (self.ls_remote_rc, "") if self.ls_remote_rc else (0, f"{self.upstream}\trefs/heads/main")
        if args[:1] == ["fetch"]:
            return (0, "")
        if args[:2] == ["rev-list", "--count"]:
            return (0, str(self.ahead if args[2].startswith("FETCH_HEAD..") else len(self.incoming)))
        if args[:1] == ["log"]:
            rev = args[-1]
            if rev == "HEAD..FETCH_HEAD":
                return (0, "\n".join(_commit_line(c[0], c[1]) for c in self.incoming))
            if rev == "FETCH_HEAD":
                newest = self.incoming[0] if self.incoming else (self.head, "upstream head")
                return (0, _commit_line(newest[0], newest[1]))
            return (0, _commit_line(self.head, "current head"))
        if args[:1] == ["diff"]:
            return (0, "requirements.txt" if self.requirements_changed else "")
        if args[:1] == ["checkout"]:
            return (0, "")
        if args[:1] == ["merge"]:
            if self.merge_rc == 0:
                self.head = self.upstream
                if self.post_merge_config is not None:
                    (root / g.CONFIG_NAME).write_text(json.dumps(self.post_merge_config))
            return (self.merge_rc, "")
        raise AssertionError(f"unexpected git call: {args}")

    def ran(self, *parts: str) -> bool:
        return any(all(part in call for part in parts) for call in self.calls)


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "chartcleaner"
    (path / ".git").mkdir(parents=True)
    (path / "app.py").write_text("print('app')\n")
    (path / g.CONFIG_NAME).write_text(json.dumps({"stages": [], "wrap_output": True}))
    return path


@pytest.fixture
def fake(root, monkeypatch):
    def build(**kwargs):
        git = FakeGit(root, **kwargs)
        monkeypatch.setattr(g, "_run_git", git)
        return git
    return build


# ---------------------------------------------------------------------------
# checking
# ---------------------------------------------------------------------------

def test_up_to_date_never_fetches(root, fake):
    git = fake(head=LOCAL, upstream=LOCAL)
    status = g.check_for_update(root)
    assert (status.state, status.code) == ("up_to_date", "ok")
    assert status.behind == 0 and not status.updateable
    assert not git.ran("fetch")
    assert "Up to date" in status.detail


def test_behind_lists_incoming_commits(root, fake):
    git = fake(incoming=[("b" * 40, "feat: add census"), ("a" * 40, "fix(clean): keep the last lab")])
    status = g.check_for_update(root)
    assert status.state == "behind" and status.behind == 2
    assert status.updateable
    assert [c.subject for c in status.incoming] == ["feat: add census", "fix(clean): keep the last lab"]
    assert status.latest.subject == "feat: add census"
    assert "2 new commits" in status.detail
    assert git.ran("fetch")


def test_only_ahead_reports_ahead(root, fake):
    git = fake(incoming=[], ahead=2)
    status = g.check_for_update(root)
    assert status.state == "ahead" and not status.updateable
    assert "upstream does not" in status.detail


def test_diverged_refuses_to_merge(root, fake):
    git = fake(incoming=[("a" * 40, "theirs")], ahead=1)
    status = g.check_for_update(root)
    assert status.state == "diverged"
    result = g.apply_update(root)
    assert (result.ok, result.code) == (False, "diverged")
    assert not git.ran("merge")


def test_dirty_code_blocks_the_update(root, fake):
    (root / "app.py").write_text("edited\n")
    git = fake(incoming=[("a" * 40, "upstream work")], dirty=["M  app.py"])
    status = g.check_for_update(root)
    assert status.blocking == ("app.py",)
    assert status.updateable is False
    assert "blocked until local changes" in status.detail
    result = g.apply_update(root)
    assert (result.ok, result.code) == (False, "dirty_tree")
    assert not git.ran("merge")


def test_dirty_parsing_keeps_the_first_status_column(root, fake):
    """`git status --porcelain` puts the status in columns 0-1 of every line,
    so the output must never be stripped as a whole."""
    git = fake(incoming=[("a" * 40, "upstream work")], dirty=[" M config.json", " M app.py"])
    status = g.check_for_update(root)
    assert status.dirty == ("app.py", "config.json")
    assert status.blocking == ("app.py",)      # config.json alone must not block


def test_status_output_is_not_stripped(root, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(g.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=" M config.json\n"))
    assert g._dirty_tracked(root) == ("config.json",)


def test_untracked_files_never_block(root, fake):
    git = fake(incoming=[("a" * 40, "upstream work")], dirty=["?? presets/"])
    status = g.check_for_update(root)
    assert status.blocking == () and status.updateable


def test_repo_root_is_none_when_git_disagrees(root, fake):
    fake(repo=False)          # git answers "not a git repository"
    assert g.repo_root(root) is None


def test_not_a_repo(monkeypatch):
    monkeypatch.setattr(g, "repo_root", lambda path=None: None)
    status = g.check_for_update()
    assert (status.state, status.code) == ("unavailable", "not_a_repo")
    assert "clone the repository" in status.detail


def test_git_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda *_a, **_k: None)
    folder = tmp_path / "plain"
    folder.mkdir()
    status = g.check_for_update(folder)
    assert (status.state, status.code) == ("unavailable", "git_missing")
    assert "Git is not installed" in status.detail


def test_remote_unreachable(root, fake):
    fake(ls_remote_rc=128)
    status = g.check_for_update(root)
    assert (status.state, status.code) == ("error", "remote_unreachable")
    assert "Could not reach the remote" in status.detail


def test_repo_root_survives_a_broken_git(root, monkeypatch):
    def boom(_root, args, *, timeout=0.0):
        raise g.GitUpdateError("git_missing")
    monkeypatch.setattr(g, "_run_git", boom)
    assert g.repo_root(root) is None  # a page must never blow up on this


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------

def test_apply_fast_forwards(root, fake):
    git = fake(incoming=[("b" * 40, "two"), ("a" * 40, "one")])
    result = g.apply_update(root)
    assert result.ok and result.code == "ok"
    assert result.before == LOCAL and result.after == REMOTE
    assert len(result.applied) == 2
    assert git.ran("merge", "--ff-only", "FETCH_HEAD")
    assert not git.ran("reset") and not git.ran("clean") and not git.ran("stash")
    assert "Updated to" in result.detail


def test_apply_keeps_user_config_and_adds_new_keys(root, fake):
    """The user's values win; keys upstream added arrive with their defaults."""
    (root / g.CONFIG_NAME).write_text(json.dumps({"wrap_output": False, "stages": ["a"]}))
    git = fake(incoming=[("a" * 40, "new stage")], dirty=["M  config.json"],
               post_merge_config={"wrap_output": True, "stages": ["a"],
                                  "new_option": {"enabled": True}})
    result = g.apply_update(root)
    assert result.ok and result.config_preserved
    assert git.ran("checkout", "HEAD", "--", "config.json")
    kept = json.loads((root / g.CONFIG_NAME).read_text())
    assert kept["wrap_output"] is False            # the user's setting survives
    assert kept["stages"] == ["a"]
    assert kept["new_option"] == {"enabled": True}  # the new key still arrives
    assert "config.json was kept" in result.detail


def test_apply_restores_config_when_the_merge_fails(root, fake):
    (root / g.CONFIG_NAME).write_text(json.dumps({"wrap_output": False}))
    git = fake(incoming=[("a" * 40, "one")], dirty=["M  config.json"], merge_rc=1)
    result = g.apply_update(root)
    assert (result.ok, result.code) == (False, "merge_failed")
    # set aside for the merge, then put straight back: the config is never lost
    assert json.loads((root / g.CONFIG_NAME).read_text()) == {"wrap_output": False}


def test_apply_reports_dependency_changes(root, fake):
    fake(incoming=[("a" * 40, "bump deps")], requirements_changed=True)
    result = g.apply_update(root)
    assert result.requirements_changed
    assert "install.sh" in result.detail


def test_up_to_date_apply_is_not_an_error(root, fake):
    git = fake(head=LOCAL, upstream=LOCAL)
    result = g.apply_update(root)
    assert result.ok and result.code == "up_to_date"
    assert not git.ran("merge")


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------

def test_restart_command_reuses_the_launch_arguments(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app.py", "--port", "9000"])
    cmd = g.restart_command(root)
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("app.py")
    assert cmd[2:] == ["--port", "9000"]


def test_restart_command_falls_back_to_the_entry_point(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/usr/bin/other"])
    assert g.restart_command(root) == [sys.executable, str(root / "app.py")]


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

async def test_source_check_records_the_status_in_prefs(monkeypatch):
    from app_pages import common, updates
    from chartcleaner import git_update

    status = g.SourceStatus("behind", "update_available", "2 new commits on main",
                            None, "main", LOCAL, REMOTE, (), 2, 0, (), None)
    monkeypatch.setattr(g, "check_for_update", lambda *a, **k: status)
    before = dict(common.PREFS)
    try:
        got = await updates._source_update_check()
        assert got is not None and got.state == "behind"
        assert common.PREFS["update_source_status"] == "2 new commits on main"
        assert common.PREFS["update_source_code"] == "update_available"
        assert common.PREFS["update_source_last_checked"]
    finally:
        common.PREFS.clear()
        common.PREFS.update(before)


async def test_automatic_source_check_is_opt_in(monkeypatch):
    from app_pages import common, updates
    from chartcleaner import git_update

    calls = []

    def fake_check(*_a, **_k):
        calls.append(1)
        return g.SourceStatus("up_to_date", "ok", "Up to date", None, "main", LOCAL, LOCAL)

    monkeypatch.setattr(g, "check_for_update", fake_check)
    before = dict(common.PREFS)
    try:
        common.PREFS["update_source_auto"] = False
        assert await updates._source_update_check(automatic=True) is None
        assert not calls          # never reaches the network unasked
        common.PREFS["update_source_auto"] = True
        await updates._source_update_check(automatic=True)
        assert len(calls) == 1
    finally:
        common.PREFS.clear()
        common.PREFS.update(before)


async def test_apply_offers_a_restart_only_when_commits_landed(monkeypatch):
    from app_pages import updates
    from chartcleaner import git_update

    offered = []
    monkeypatch.setattr(updates, "confirm_dialog",
                        lambda message, action, **kw: offered.append(action))
    landed = g.UpdateResult(True, "ok", "Updated to 2222222 (2 commits).", LOCAL, REMOTE,
                            (g.Commit("a" * 40, "one", "x", "2026-10-19"),))
    monkeypatch.setattr(g, "apply_update", lambda *a, **k: landed)
    notes = []
    await updates._apply_source_update(lambda msg, **kw: notes.append((msg, kw.get("type"))))
    assert notes[0][1] == "positive"
    assert offered == [updates._restart_app]

    offered.clear()
    notes.clear()
    monkeypatch.setattr(g, "apply_update",
                        lambda *a, **k: g.UpdateResult(True, "up_to_date", "Already up to date."))
    await updates._apply_source_update(lambda msg, **kw: notes.append((msg, kw.get("type"))))
    assert notes[0][1] == "info" and not offered  # nothing landed, nothing to restart


async def test_settings_shows_the_source_update_card(user, monkeypatch):
    """Settings offers the git channel when the app runs from a checkout."""
    from chartcleaner import git_update

    monkeypatch.setattr(git_update, "repo_root", lambda path=None: git_update.Path("/repo"))
    await user.open('/settings')
    await user.should_see('Source updates (GitHub)')
    await user.should_see('Check GitHub for new commits')
    await user.should_see('Update to latest commit')
