"""The runner entrypoint. No AI decision-making happens below the AGENT_ADAPTER dispatch
in main() (PLAN-PHASE-2-M2 §0.2, PLAN-PHASE-2-M3 §0.2) — the legacy path is still fixed
and hardcoded end to end. M2 proved the sandbox round trip with nothing behind it but a
timed heartbeat loop. M3 added the git round trip the same way: when a repository is
configured, clone it, create an isolated branch, make one constant, non-negotiable
change, commit, push, and report the diff. M4 (PLAN-PHASE-2-M4.md §3) adds the first real
decision-making path, gated on AGENT_ADAPTER: everything above that gate is unchanged.

Deliberately minimal dependencies for the legacy path: stdlib plus the `git` binary. The
claude_agent_sdk path (agent_sdk.py) is the one deliberate expansion of this image's
software surface since M2 (PLAN-PHASE-2-M4.md §1) — imported lazily, only when
AGENT_ADAPTER selects it, so the legacy path's dependency footprint is unaffected. Talks
to exactly one thing over the network: the internal API, over plain HTTP inside the
Docker network (see the Caddyfile and PLAN-PHASE-2-M2.md §2.6) — plus, only when a
repository is configured, the one allowlisted git remote, and, only for the SDK path, the
Anthropic API. Never touches Postgres, Redis, or any provider API directly with a
database credential — none is ever handed to this container at all.
"""

import asyncio
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

AGENT_ADAPTER = os.environ.get("AGENT_ADAPTER", "")
RUN_MAX_TURNS = int(os.environ.get("RUN_MAX_TURNS", "30"))
RUN_MAX_COST_USD = float(os.environ.get("RUN_MAX_COST_USD", "2.00"))

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


def run_claude_agent_sdk() -> int:
    """The M4 path (PLAN-PHASE-2-M4.md §3): clone/branch first if a repository is
    configured (identical to run_git_workflow's own setup), hand off to the SDK adapter
    for the actual work, then commit/push/report whatever exists afterward — attempted
    regardless of whether the agent succeeded, so a failed or truncated run still leaves
    a reviewable partial diff (PLAN-PHASE-2-M4.md §7) instead of losing it."""
    import agent_sdk  # imported lazily: only this path needs the SDK's dependencies

    has_repository = bool(GIT_REMOTE_URL)
    if has_repository:
        if not GIT_DEFAULT_BRANCH or not GIT_BRANCH_NAME:
            raise RuntimeError("GIT_* env vars must all be set together")
        git_ops.clone(GIT_REMOTE_URL, GIT_DEFAULT_BRANCH, WORKSPACE_DIR)
        git_ops.create_branch(GIT_BRANCH_NAME, WORKSPACE_DIR)
    else:
        # No repository to clone: nothing else creates this directory, but the SDK's
        # cwd must exist before it starts (caught live: "Working directory does not
        # exist", PLAN-PHASE-2-M4.md's own M4.1 live verification).
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    api = agent_sdk.RunApi(API_BASE_URL, RUN_ID, RUN_TOKEN)
    details: dict[str, object] | None
    try:
        outcome, message, details = asyncio.run(
            agent_sdk.run_agent(
                api=api,
                workspace_dir=str(WORKSPACE_DIR),
                max_turns=RUN_MAX_TURNS,
                max_cost_usd=RUN_MAX_COST_USD,
            )
        )
    except Exception as exc:  # a bug in run_agent's own setup, not the SDK session itself
        print(f"agent sdk run failed: {exc}", file=sys.stderr)
        outcome, message, details = "failed", f"agent sdk run failed: {exc}", None

    if has_repository:
        try:
            committed = git_ops.commit(f"acc: run {RUN_ID}", WORKSPACE_DIR)
            if committed:
                git_ops.push(GIT_BRANCH_NAME, WORKSPACE_DIR)
                changes = git_ops.diff_stat(GIT_DEFAULT_BRANCH, WORKSPACE_DIR)
                if changes:
                    _try_post(f"/internal/runs/{RUN_ID}/file-changes", {"changes": changes})
        except (git_ops.GitError, OSError) as exc:
            print(f"git finalize failed: {exc}", file=sys.stderr)
            if outcome == "succeeded":
                outcome, message = "failed", f"agent succeeded but git finalize failed: {exc}"

    if outcome == "cancelled":
        # No /complete or /fail call — the orchestrator's own handle_cancel_run is what
        # marks this CANCELLED (PLAN-PHASE-2-M2.md's authoritative kill, unchanged by
        # M4); self-reporting a terminal status here would race it (caught live, M4.1's
        # own verification — see agent_sdk.run_agent's docstring).
        print("cancellation requested; exiting without reporting a terminal status")
        return 0
    if outcome == "succeeded":
        _try_post(f"/internal/runs/{RUN_ID}/complete", {"result_summary": message[:4000]})
        return 0
    _try_post(f"/internal/runs/{RUN_ID}/fail", {"reason": message[:4000], "details": details})
    return 1


def main() -> int:
    _heartbeat()  # confirms the process is alive before any (potentially slower) git work

    if AGENT_ADAPTER == "claude_agent_sdk":
        return run_claude_agent_sdk()

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
