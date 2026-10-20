"""Update a source checkout by fast-forwarding to the newest upstream commit.

Installed (frozen) builds update through :mod:`chartcleaner.updater`, which
stages a signed release archive.  This module is the other channel: when Chart
Cleaner runs from a git checkout (``run-app.command``, ``python app.py``), the
Settings page can show what is new on GitHub and pull it in with one button.

Everything here talks to git only — no GitHub REST token, no rate limit, no
extra outbound host: the remote already configured in ``.git`` is the source of
truth, which is the repository the user cloned.

Rules this module never breaks:

* Read-only until :func:`apply_update`, and that only ever runs
  ``git fetch`` + ``git merge --ff-only``.  No rebase, no reset, no clean,
  no force, no checkout of anything but ``config.json`` for one moment, and
  ``core.hooksPath`` is pinned empty so no repository hook ever executes.
* A dirty working tree blocks the update — except for ``config.json``, which
  is the user's settings: its bytes are held in memory, the file is set aside
  for the merge, and put back afterwards, topped up with any keys the new
  upstream config added (the user's own values always win).
* Nothing here reads chart text, and nothing writes to ``data/``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

CHECK_INTERVAL = 24 * 60 * 60
GIT_TIMEOUT = 120.0
REMOTE_TIMEOUT = 30.0
QUICK_TIMEOUT = 15.0
MAX_COMMITS = 20
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
APP_ENTRY = "app.py"
CONFIG_NAME = "config.json"
REQUIREMENTS_NAME = "requirements.txt"

_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_SEP = "\x1f"
_LOG_FORMAT = _SEP.join(("%H", "%s", "%an", "%aI"))

log = logging.getLogger(__name__)

__all__ = [
    "CHECK_INTERVAL", "GitUpdateError", "Commit", "SourceStatus", "UpdateResult",
    "repo_root", "check_for_update", "apply_update", "restart_command", "restart_app",
]


class GitUpdateError(RuntimeError):
    """A failed git step, named so the UI can explain it without a traceback."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    author: str
    date: str

    @property
    def short(self) -> str:
        return self.sha[:7]

    def label(self) -> str:
        day = (self.date or "")[:10]
        who = f"{self.author}, " if self.author else ""
        return f"{self.short} {self.subject} ({who}{day})" if day else f"{self.short} {self.subject}"


@dataclass(frozen=True)
class SourceStatus:
    """What the checkout looks like compared with upstream."""

    state: str          # up_to_date | behind | ahead | diverged | unavailable | error
    code: str
    detail: str
    root: Path | None = None
    branch: str | None = None
    local_sha: str | None = None
    remote_sha: str | None = None
    dirty: tuple[str, ...] = ()
    behind: int = 0
    ahead: int = 0
    incoming: tuple[Commit, ...] = ()
    latest: Commit | None = None

    @property
    def updateable(self) -> bool:
        return self.state == "behind" and not self.blocking

    @property
    def blocking(self) -> tuple[str, ...]:
        """Tracked files with local edits — anything but the user's config."""
        return tuple(p for p in self.dirty if p != CONFIG_NAME)

    @property
    def short_sha(self) -> str:
        return (self.local_sha or "")[:7]


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    code: str
    detail: str
    before: str | None = None
    after: str | None = None
    applied: tuple[Commit, ...] = ()
    config_preserved: bool = False
    requirements_changed: bool = False

    @property
    def short_after(self) -> str:
        return (self.after or "")[:7]


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def _git_env() -> dict:
    env = dict(os.environ)
    # Never block on a credential prompt inside a GUI app, and never let a
    # status/fetch run hold the lock another tab wants.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _run_git(root: Path, args, *, timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
    """Run one git command with hooks disabled; returns (returncode, stdout)."""
    cmd = ["git", "-c", "core.hooksPath=/dev/null", *args]
    try:
        proc = subprocess.run(cmd, cwd=str(root), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout,
                              env=_git_env())
    except FileNotFoundError:
        raise GitUpdateError("git_missing") from None
    except subprocess.TimeoutExpired:
        raise GitUpdateError("git_timeout") from None
    except OSError:
        raise GitUpdateError("git_failed") from None
    # rstrip, not strip: `git status --porcelain` puts the status in columns
    # 0-1 of every line, and stripping the whole output would eat the first
    # line's leading space (turning " M config.json" into "M config.json").
    return proc.returncode, (proc.stdout or "").rstrip()


def repo_root(path=None) -> Path | None:
    """Top level of the checkout running this app, or None when there isn't one.

    Never raises: a page just wants to know whether the git channel applies.
    """
    if getattr(sys, "frozen", False):
        return None
    start = Path(path) if path is not None else Path(__file__).resolve().parents[1]
    try:
        rc, out = _run_git(start, ["rev-parse", "--show-toplevel"], timeout=QUICK_TIMEOUT)
    except GitUpdateError:
        return None
    if rc != 0 or not out:
        return None
    try:
        return Path(out.splitlines()[0].strip())
    except (IndexError, ValueError):
        return None


def _resolve_root(root=None) -> Path | None:
    if root is not None:
        path = Path(root)
        return path if (path / ".git").exists() else None
    return repo_root()


def _no_root_code() -> str:
    return "not_a_repo" if shutil.which("git") else "git_missing"


def _same_sha(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.startswith(right) or right.startswith(left)


def _pick_remote(root: Path, remote: str) -> tuple[str, str]:
    rc, url = _run_git(root, ["config", "--get", f"remote.{remote}.url"], timeout=QUICK_TIMEOUT)
    if rc == 0 and url.strip():
        return remote, url.strip()
    rc, out = _run_git(root, ["remote"], timeout=QUICK_TIMEOUT)
    names = [line.strip() for line in out.splitlines() if line.strip()]
    if not names:
        raise GitUpdateError("no_remote")
    name = names[0]
    rc, url = _run_git(root, ["config", "--get", f"remote.{name}.url"], timeout=QUICK_TIMEOUT)
    return name, url.strip() if rc == 0 else ""


def _local_branch(root: Path, branch: str | None) -> tuple[str, bool]:
    rc, out = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=QUICK_TIMEOUT)
    if rc != 0 or not out.strip():
        raise GitUpdateError("not_a_repo")
    current = out.strip()
    detached = current == "HEAD"
    if branch:
        return branch, detached
    return (DEFAULT_BRANCH if detached else current), detached


def _head_sha(root: Path) -> str | None:
    rc, out = _run_git(root, ["rev-parse", "HEAD"], timeout=QUICK_TIMEOUT)
    if rc != 0 or not _SHA.match(out.strip()):
        return None
    return out.strip()


def _dirty_tracked(root: Path) -> tuple[str, ...]:
    """Tracked paths with local changes. Untracked/ignored files never block."""
    rc, out = _run_git(root, ["status", "--porcelain"], timeout=QUICK_TIMEOUT * 2)
    if rc != 0:
        raise GitUpdateError("status_failed")
    dirty = []
    for line in out.splitlines():
        line = line.rstrip()
        if len(line) < 4:
            continue
        if line[:2].strip() in {"??", "!!"}:
            continue
        dirty.append(line[3:].strip().strip('"'))
    return tuple(sorted(set(dirty)))


def _remote_sha(root: Path, remote: str, branch: str) -> str:
    rc, out = _run_git(root, ["ls-remote", remote, f"refs/heads/{branch}"], timeout=REMOTE_TIMEOUT)
    if rc != 0:
        raise GitUpdateError("remote_unreachable")
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{branch}" and _SHA.match(sha.strip()):
            return sha.strip()
    raise GitUpdateError("branch_missing")


def _fetch(root: Path, remote: str, branch: str) -> str:
    """Fetch just this branch (refs only — the working tree is untouched)."""
    rc, _ = _run_git(root, ["fetch", "--no-tags", "--quiet", remote, branch])
    if rc != 0:
        raise GitUpdateError("remote_unreachable")
    rc, out = _run_git(root, ["rev-parse", "FETCH_HEAD"], timeout=QUICK_TIMEOUT)
    if rc != 0 or not _SHA.match(out.strip()):
        raise GitUpdateError("remote_unreachable")
    return out.strip()


def _count(root: Path, rev_range: str) -> int:
    rc, out = _run_git(root, ["rev-list", "--count", rev_range], timeout=GIT_TIMEOUT)
    if rc != 0:
        return 0
    try:
        return int(out.strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0


def _commits(root: Path, rev_range: str | None, *, limit: int = MAX_COMMITS) -> tuple[Commit, ...]:
    args = ["log", f"--max-count={limit}", f"--format={_LOG_FORMAT}"]
    if rev_range:
        args.append(rev_range)
    rc, out = _run_git(root, args, timeout=GIT_TIMEOUT)
    if rc != 0 or not out:
        return ()
    commits = []
    for line in out.splitlines():
        parts = line.split(_SEP)
        if len(parts) != 4 or not _SHA.match(parts[0].strip()):
            continue
        sha, subject, author, date = (p.strip() for p in parts)
        commits.append(Commit(sha, subject, author, date))
    return tuple(commits)


# ---------------------------------------------------------------------------
# read-only check
# ---------------------------------------------------------------------------

def _describe(status: SourceStatus) -> str:
    branch = status.branch or "HEAD"
    where = f"{branch} @ {status.short_sha}" if status.local_sha else branch
    if status.state == "up_to_date":
        subject = f" — {status.latest.subject}" if status.latest else ""
        return f"Up to date with upstream ({where}){subject}"
    if status.state == "behind":
        count = status.behind or len(status.incoming)
        text = f"{count} new commit{'s' if count != 1 else ''} on {branch}"
        if status.latest:
            text += f" — latest: {status.latest.label()}"
        if status.blocking:
            text += f" · blocked until local changes to {', '.join(status.blocking[:3])} are committed or stashed"
        elif CONFIG_NAME in status.dirty:
            text += " · your config.json is kept as-is"
        return text
    if status.state == "ahead":
        return (f"Your checkout has {status.ahead} commit(s) that upstream does not; "
                "nothing to fast-forward.")
    if status.state == "diverged":
        return (f"This checkout and {branch} have diverged ({status.behind} behind, "
                f"{status.ahead} ahead) — finish that in git before updating here.")
    return status.detail or "Update status unavailable"


def check_for_update(root=None, *, remote: str = DEFAULT_REMOTE,
                     branch: str | None = None) -> SourceStatus:
    """Compare the checkout with the newest commit on the upstream branch.

    Read-only apart from ``git fetch``, which moves refs only: the working
    tree is never touched.
    """
    resolved = _resolve_root(root)
    if resolved is None:
        code = _no_root_code()
        return SourceStatus("unavailable", code, _failure(code))
    try:
        name, _url = _pick_remote(resolved, remote)
        branch_name, _detached = _local_branch(resolved, branch)
        local_sha = _head_sha(resolved)
        dirty = _dirty_tracked(resolved)
        upstream = _remote_sha(resolved, name, branch_name)

        behind = ahead = 0
        incoming: tuple[Commit, ...] = ()
        if local_sha and _same_sha(local_sha, upstream):
            latest = _commits(resolved, None, limit=1)
            status = SourceStatus("up_to_date", "ok", "", resolved, branch_name, local_sha,
                                  upstream, dirty, 0, 0, (), latest[0] if latest else None)
            return replace(status, detail=_describe(status))

        fetched = _fetch(resolved, name, branch_name)
        upstream = fetched or upstream
        behind = _count(resolved, "HEAD..FETCH_HEAD")
        ahead = _count(resolved, "FETCH_HEAD..HEAD")
        incoming = _commits(resolved, "HEAD..FETCH_HEAD")
        head_commit = _commits(resolved, "FETCH_HEAD", limit=1)
        latest = head_commit[0] if head_commit else None

        if behind and ahead:
            state, code = "diverged", "diverged"
        elif behind:
            state, code = "behind", "update_available"
        elif ahead:
            state, code = "ahead", "ahead"
        else:
            state, code = "up_to_date", "ok"
        status = SourceStatus(state, code, "", resolved, branch_name, local_sha, upstream,
                              dirty, behind, ahead, incoming, latest)
        return replace(status, detail=_describe(status))
    except GitUpdateError as exc:
        detail = {
            "git_missing": "Git is not installed, so this checkout cannot be updated here.",
            "git_timeout": "Git did not finish in time; try again, or run `git pull` yourself.",
            "no_remote": "This checkout has no remote to update from.",
            "remote_unreachable": "Could not reach the remote — check your network or credentials.",
            "branch_missing": "The upstream branch was not found on the remote.",
            "not_a_repo": "Not a git checkout — clone the repository to update from GitHub.",
        }.get(exc.code, f"Update check failed ({exc.code}).")
        return SourceStatus("error", exc.code, detail, resolved)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _fill_missing(base, override):
    """Recursive merge where ``override`` wins and ``base`` only fills gaps.

    The user's config keeps every value they set; keys the new upstream config
    introduced (new stages, new options) are added with their new defaults.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            other = base.get(key)
            out[key] = _fill_missing(other, value) if isinstance(other, dict) and isinstance(value, dict) else value
        return out
    return override


def _restore_config(path: Path, user_text: str) -> bool:
    """Put the user's config back after the merge; True when it was merged in."""
    try:
        upstream_text = path.read_text(encoding="utf-8")
    except OSError:
        upstream_text = ""
    try:
        user_cfg = json.loads(user_text)
        upstream_cfg = json.loads(upstream_text) if upstream_text.strip() else {}
    except ValueError:
        user_cfg = upstream_cfg = None
    if not isinstance(user_cfg, dict):
        path.write_text(user_text, encoding="utf-8")  # not JSON we understand: verbatim
        return False
    if not isinstance(upstream_cfg, dict):
        upstream_cfg = {}
    path.write_text(json.dumps(_fill_missing(upstream_cfg, user_cfg), indent=4,
                               ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def _requirements_changed(root: Path, before: str | None, after: str | None) -> bool:
    if not before or not after:
        return False
    rc, out = _run_git(root, ["diff", "--name-only", f"{before}..{after}", "--",
                              REQUIREMENTS_NAME], timeout=QUICK_TIMEOUT)
    return rc == 0 and bool(out.strip())


def _failure(code: str) -> str:
    return {
        "up_to_date": "Already up to date — nothing to pull.",
        "dirty_tree": "Local edits to tracked files would be overwritten; commit or stash them first.",
        "diverged": "This checkout has diverged from upstream; merge it in git first.",
        "ahead": "Your checkout is ahead of upstream — nothing to fast-forward.",
        "merge_failed": "The fast-forward failed; nothing was changed. Run `git pull` to see why.",
        "config_stash_failed": "Could not set config.json aside for the merge; nothing was changed.",
        "remote_unreachable": "Could not reach the remote — check your network or credentials.",
        "git_missing": "Git is not installed, so this checkout cannot be updated here.",
        "not_a_repo": "Not a git checkout — clone the repository to update from GitHub.",
    }.get(code, f"Update failed ({code}).")


def apply_update(root=None, *, remote: str = DEFAULT_REMOTE,
                 branch: str | None = None) -> UpdateResult:
    """Fast-forward the checkout to the newest upstream commit.

    Refuses outright unless the move is a clean fast-forward with a clean tree
    (``config.json`` excepted — see the module docstring).
    """
    resolved = _resolve_root(root)
    if resolved is None:
        code = _no_root_code()
        return UpdateResult(False, code, _failure(code))
    status = check_for_update(resolved, remote=remote, branch=branch)
    if status.state == "error":
        return UpdateResult(False, status.code, status.detail)
    if status.state == "up_to_date":
        return UpdateResult(True, "up_to_date", _failure("up_to_date"),
                            before=status.local_sha, after=status.local_sha)
    if status.state == "diverged":
        return UpdateResult(False, "diverged", _failure("diverged"))
    if status.state == "ahead":
        return UpdateResult(False, "ahead", _failure("ahead"))
    if status.blocking:
        names = ", ".join(status.blocking[:5])
        return UpdateResult(False, "dirty_tree",
                            f"Local changes to {names} would be overwritten — commit or stash them first.",
                            before=status.local_sha)

    config_path = resolved / CONFIG_NAME
    user_config: str | None = None
    restored = False
    try:
        if CONFIG_NAME in status.dirty:
            user_config = config_path.read_text(encoding="utf-8")
            rc, _ = _run_git(resolved, ["checkout", "HEAD", "--", CONFIG_NAME], timeout=GIT_TIMEOUT)
            if rc != 0:
                raise GitUpdateError("config_stash_failed")
        rc, _ = _run_git(resolved, ["merge", "--ff-only", "FETCH_HEAD"])
        if rc != 0:
            raise GitUpdateError("merge_failed")
        after = _head_sha(resolved) or status.remote_sha
        result = UpdateResult(
            True, "ok", "", before=status.local_sha, after=after, applied=status.incoming,
            requirements_changed=_requirements_changed(resolved, status.local_sha, after))
        log.info("source update: %s -> %s (%d commit(s))", status.local_sha, after,
                 len(status.incoming))
    except GitUpdateError as exc:
        return UpdateResult(False, exc.code, _failure(exc.code), before=status.local_sha)
    except OSError:
        return UpdateResult(False, "io_error", "Could not read or write config.json; nothing was changed.",
                            before=status.local_sha)
    finally:
        if user_config is not None:
            try:
                restored = _restore_config(config_path, user_config)
            except Exception:  # the merge already succeeded; never lose the config
                log.exception("could not restore config.json after update")

    count = len(result.applied)
    detail = f"Updated to {result.short_after}" + (f" ({count} commit{'' if count == 1 else 's'})." if count else ".")
    if restored:
        detail += " Your config.json was kept as-is."
    if result.requirements_changed:
        detail += " Dependencies changed — run install.sh (or install.bat) once."
    return replace(result, detail=detail, config_preserved=restored)


# ---------------------------------------------------------------------------
# restart so the new code actually runs
# ---------------------------------------------------------------------------

def restart_command(root=None) -> list[str]:
    """argv that relaunches this app from the updated checkout."""
    resolved = _resolve_root(root) or Path.cwd()
    argv = [a for a in sys.argv if a]
    entry = resolved / APP_ENTRY
    if argv and Path(argv[0]).name == APP_ENTRY:
        return [sys.executable, str(entry), *argv[1:]]
    return [sys.executable, str(entry)]


def restart_app(root=None) -> None:
    """Replace this process with a fresh one running the updated code."""
    resolved = _resolve_root(root) or Path.cwd()
    cmd = restart_command(resolved)
    os.chdir(str(resolved))
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os.execv(cmd[0], cmd)  # noqa: S606 — deliberate: reload in place, no shell
