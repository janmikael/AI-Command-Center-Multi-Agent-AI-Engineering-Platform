"""Controlled git operations (PLAN-PHASE-2-M3.md §4): a fixed, non-extensible set of git
subcommands. Every argument is decided by this file, never assembled from anything an
agent or a task could influence — there is no decision-making code in this milestone at
all (§0.2), only a fixed sequence of fixed commands. clone/branch/commit/push/diff_stat
are the only five operations that exist; there is no sixth to misuse.
"""

import re
import subprocess
from pathlib import Path

BRANCH_PATTERN = re.compile(r"^acc/[a-z0-9][a-z0-9-]*$")
BOT_NAME = "AI Command Center"
BOT_EMAIL = "runner@ai-command-center.local"
GIT_TIMEOUT_S = 120

_STATUS_TO_CHANGE_TYPE = {"A": "added", "M": "modified", "D": "deleted"}


class GitError(RuntimeError):
    pass


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    # args is always our own fixed list, never external input, and "git" resolving via
    # PATH is fine in this single-purpose image, whose Dockerfile is the only thing that
    # ever installs a git binary onto it.
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def clone(remote_url: str, default_branch: str, workspace_dir: Path) -> None:
    """Shallow, single-branch — minimizes data transferred and history exposed."""
    _run(
        [
            "clone", "--depth", "1", "--branch", default_branch, "--single-branch",
            remote_url, str(workspace_dir),
        ]
    )
    _run(["config", "user.name", BOT_NAME], cwd=workspace_dir)
    _run(["config", "user.email", BOT_EMAIL], cwd=workspace_dir)


def create_branch(branch_name: str, workspace_dir: Path) -> None:
    if not BRANCH_PATTERN.match(branch_name):
        raise GitError(f"refusing to create a branch outside acc/*: {branch_name!r}")
    _run(["checkout", "-b", branch_name], cwd=workspace_dir)


def commit(message: str, workspace_dir: Path) -> bool:
    """Stages everything and commits. Returns False (no-op, no commit made) if there was
    nothing to commit — the wrapper's caller decides whether that's worth reporting."""
    _run(["add", "-A"], cwd=workspace_dir)
    status = _run(["status", "--porcelain"], cwd=workspace_dir)
    if not status.strip():
        return False
    _run(["commit", "-m", message], cwd=workspace_dir)
    return True


def push(branch_name: str, workspace_dir: Path) -> None:
    if not BRANCH_PATTERN.match(branch_name):
        raise GitError(f"refusing to push a branch outside acc/*: {branch_name!r}")
    _run(["push", "origin", f"HEAD:{branch_name}"], cwd=workspace_dir)


def diff_stat(default_branch: str, workspace_dir: Path) -> list[dict[str, object]]:
    """[{path, change_type, additions, deletions}] for HEAD vs the default branch."""
    base = f"origin/{default_branch}...HEAD"
    numstat = _run(["diff", base, "--numstat"], cwd=workspace_dir)
    name_status = _run(["diff", base, "--name-status"], cwd=workspace_dir)

    counts: dict[str, tuple[int | None, int | None]] = {}
    for line in numstat.splitlines():
        if not line.strip():
            continue
        added, deleted, path = line.split("\t", 2)
        counts[path] = (
            None if added == "-" else int(added),
            None if deleted == "-" else int(deleted),
        )

    changes: list[dict[str, object]] = []
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code, path = parts[0], parts[-1]
        change_type = _STATUS_TO_CHANGE_TYPE.get(
            code[0], "renamed" if code.startswith("R") else "modified"
        )
        additions, deletions = counts.get(path, (None, None))
        changes.append(
            {
                "path": path,
                "change_type": change_type,
                "additions": additions,
                "deletions": deletions,
            }
        )
    return changes
