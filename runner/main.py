"""The runner entrypoint. Still no SDK, no tools, no AI decision-making (PLAN-PHASE-2-M2
§0.2, PLAN-PHASE-2-M3 §0.2) — every action here is fixed and hardcoded. M2 proved the
sandbox round trip with nothing behind it but a timed heartbeat loop. M3 adds the git
round trip the same way: when a repository is configured, clone it, create an isolated
branch, make one constant, non-negotiable change, commit, push, and report the diff —
never a decision about *what* to change, only whether there's a repository to use at all.

Deliberately minimal dependencies: stdlib plus the `git` binary (the only thing this
image installs beyond the base Python image), so there's as little as possible for a
compromised process to find useful. Talks to exactly one thing over the network: the
internal API, over plain HTTP inside the Docker network (see the Caddyfile and
PLAN-PHASE-2-M2.md §2.6) — plus, only when a repository is configured, the one
allowlisted git remote. Never touches Postgres, Redis, or any provider API.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import git_ops

RUN_ID = os.environ["RUN_ID"]
RUN_TOKEN = os.environ["RUN_TOKEN"]
API_BASE_URL = os.environ["API_BASE_URL"]
HEARTBEAT_INTERVAL_S = float(os.environ.get("HEARTBEAT_INTERVAL_S", "5"))
WORK_DURATION_S = float(os.environ.get("WORK_DURATION_S", "20"))
REQUEST_TIMEOUT_S = 10

GIT_REMOTE_URL = os.environ.get("GIT_REMOTE_URL")
GIT_DEFAULT_BRANCH = os.environ.get("GIT_DEFAULT_BRANCH")
GIT_BRANCH_NAME = os.environ.get("GIT_BRANCH_NAME")
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace/repo"))
MARKER_FILE_NAME = "ACC_SMOKE_TEST_MARKER.txt"


def _post(path: str, body: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(  # noqa: S310 - fixed internal URL, not user input
        f"{API_BASE_URL}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {RUN_TOKEN}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:  # noqa: S310
        raw = response.read()
        return json.loads(raw) if raw else {}


def _try_post(path: str, body: dict[str, object]) -> None:
    try:
        _post(path, body)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"POST {path} failed: {exc}", file=sys.stderr)


def _heartbeat() -> dict[str, object]:
    try:
        return _post(f"/internal/runs/{RUN_ID}/heartbeat", {})
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"heartbeat failed: {exc}", file=sys.stderr)
        return {}


def run_git_workflow(remote_url: str, default_branch: str, branch_name: str) -> str:
    """Clone -> branch -> one fixed marker-file change -> commit -> push -> report the
    diff. No step here depends on anything the task, the agent, or the operator asked
    for — the marker file's name and content are constants (PLAN-PHASE-2-M3.md §0.2)."""
    git_ops.clone(remote_url, default_branch, WORKSPACE_DIR)
    git_ops.create_branch(branch_name, WORKSPACE_DIR)

    marker = WORKSPACE_DIR / MARKER_FILE_NAME
    marker.write_text(f"AI Command Center smoke-test run {RUN_ID}\n")

    committed = git_ops.commit(f"acc: smoke-test run {RUN_ID}", WORKSPACE_DIR)
    if not committed:
        return "git workflow OK (nothing to commit)"

    git_ops.push(branch_name, WORKSPACE_DIR)
    changes = git_ops.diff_stat(default_branch, WORKSPACE_DIR)
    if changes:
        _try_post(f"/internal/runs/{RUN_ID}/file-changes", {"changes": changes})
    return f"pushed {branch_name}"


def main() -> int:
    _heartbeat()  # confirms the process is alive before any (potentially slower) git work

    if GIT_REMOTE_URL:
        if not GIT_DEFAULT_BRANCH or not GIT_BRANCH_NAME:
            raise RuntimeError("GIT_* env vars must all be set together")
        try:
            result_summary = run_git_workflow(GIT_REMOTE_URL, GIT_DEFAULT_BRANCH, GIT_BRANCH_NAME)
        except (git_ops.GitError, OSError) as exc:
            print(f"git workflow failed: {exc}", file=sys.stderr)
            _try_post(f"/internal/runs/{RUN_ID}/fail", {"reason": f"git workflow failed: {exc}"})
            return 1
        _try_post(f"/internal/runs/{RUN_ID}/complete", {"result_summary": result_summary})
        return 0

    # No repository configured: unchanged M2 behavior — a timed heartbeat loop proving
    # the sandbox round trip with nothing else behind it.
    elapsed = 0.0
    while elapsed < WORK_DURATION_S:
        reply = _heartbeat()
        if reply.get("cancel_requested"):
            print("cancellation requested; exiting")
            return 0
        time.sleep(HEARTBEAT_INTERVAL_S)
        elapsed += HEARTBEAT_INTERVAL_S

    try:
        _post(f"/internal/runs/{RUN_ID}/complete", {"result_summary": "M2 smoke run OK"})
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"complete call failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
