"""Sandbox container lifecycle: create, start, stop, kill, inspect, remove — all through
docker-socket-proxy, never the raw socket (PLAN-PHASE-2-M2.md §2.1). This is the only
module in acc.orchestrator that imports aiodocker; everything else works in terms of
acc.commands.runs and acc.queries.runs.

Two job handlers, registered with acc.orchestrator.scheduler by main.py:
  handle_start_run  — launch a container for a queued run
  handle_cancel_run — stop a container the operator asked to cancel

Plus two periodic passes main.py calls directly on a timer (no job kind drives either):
  reconcile_active_runs      — a container Docker already knows has exited, without the
                                run ever reaching a terminal state through the normal
                                /complete or /fail calls (not a time-based heartbeat
                                timeout — that narrower, staged watchdog is M5, §0.6)
  cleanup_finished_containers — the success-path counterpart: a run that finished
                                 normally still leaves its exited container behind for
                                 someone to remove
"""

import contextlib
import time
import uuid
from datetime import timedelta
from typing import Any

import aiodocker
import structlog

from acc.commands import runs as run_commands
from acc.commands._base import EventSink
from acc.config import Settings
from acc.db.session import SessionFactory
from acc.domain.agent_run import RunStatus
from acc.domain.errors import Conflict
from acc.domain.events import ORCHESTRATOR
from acc.ids import utcnow
from acc.orchestrator.scheduler import ClaimedJob
from acc.queries import runs as run_queries
from acc.queries.runs import RunRepository
from acc.security import mint_run_token

log = structlog.get_logger("acc.orchestrator.launcher")

STOP_GRACE_PERIOD_S = 10
CONTAINER_NOT_FOUND = 404
# Tags every runner container so a future `docker ps --filter label=...` can find them
# independently of our own bookkeeping — a debugging/ops aid, not load-bearing.
RUN_ID_LABEL = "acc.run_id"
WORKSPACE_DIR = "/workspace/repo"


def compute_branch_name(task_key: str, run_id: uuid.UUID) -> str:
    """Deterministic, computed here — not something the runner invents or reports back
    (PLAN-PHASE-2-M3.md §3). The short run-id suffix keeps a retried task's branches from
    colliding without needing an attempt counter — taken from the *end* of the UUID7,
    not the start: run_id's leading bits are a millisecond timestamp (RFC 9562), so two
    runs created moments apart share almost all of their leading hex digits and would
    collide constantly; the trailing bits are the random part (caught live: every run in
    one fast test session computing the same branch name, PLAN-PHASE-2-M3.md's own test
    run)."""
    return f"acc/{task_key.lower()}-{run_id.hex[-8:]}"


def build_container_config(
    *,
    run_token: str,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    settings: Settings,
    repository: RunRepository | None = None,
    branch_name: str | None = None,
    agent_adapter: str = "none",
) -> dict[str, Any]:
    """Pure: the exact request body sent to docker-socket-proxy's /containers/create.
    No I/O, no Docker client — testable on its own (tests/unit/test_launcher.py).

    repository/branch_name (M3): both None for a task with no linked repository — the
    runner's git step is entirely conditional on GIT_REMOTE_URL being present, so a
    plain task's container config is byte-for-byte what M2 already produced.

    agent_adapter (M4): "none" (the default) produces a container byte-for-byte
    identical to every prior milestone's — the runner falls back to its old
    git-workflow-or-heartbeat dispatch, unchanged. Only "claude_agent_sdk" adds anything
    (PLAN-PHASE-2-M4.md §1/§9): AGENT_ADAPTER plus the SDK's own credential and
    configurable run limits, all absent for every other value, same conditional-injection
    pattern M3 already established for GIT_CREDENTIAL.
    """
    env = [
        f"RUN_ID={run_id}",
        f"AGENT_ID={agent_id}",
        f"RUN_TOKEN={run_token}",
        "API_BASE_URL=http://api:8000",
        "HEARTBEAT_INTERVAL_S=5",
    ]
    if repository is not None:
        assert branch_name is not None, "branch_name is required whenever repository is set"
        env += [
            f"GIT_REMOTE_URL={repository.remote_url}",
            f"GIT_DEFAULT_BRANCH={repository.default_branch}",
            f"GIT_BRANCH_NAME={branch_name}",
            f"WORKSPACE_DIR={WORKSPACE_DIR}",
        ]
        # Absent entirely for a task with no repository — nothing for GIT_ASKPASS to
        # read, and nothing to redact if it were ever logged (PLAN-PHASE-2-M3.md §2.3).
        if settings.git_credential is not None:
            env.append(f"GIT_CREDENTIAL={settings.git_credential.get_secret_value()}")
    if agent_adapter == "claude_agent_sdk":
        assert settings.anthropic_api_key is not None, (
            "handle_start_run must check anthropic_api_key before calling this"
        )
        env += [
            "AGENT_ADAPTER=claude_agent_sdk",
            f"ANTHROPIC_API_KEY={settings.anthropic_api_key.get_secret_value()}",
            f"RUN_MAX_TURNS={settings.run_max_turns}",
            f"RUN_MAX_COST_USD={settings.run_max_cost_usd}",
        ]
    return {
        "Image": settings.runner_image,
        "Env": env,
        "Labels": {RUN_ID_LABEL: str(run_id)},
        "HostConfig": {
            # Never the internal network: a runner container must have no path to
            # Postgres or Redis even if fully compromised (PLAN-PHASE-2-M2.md §1).
            "NetworkMode": settings.runner_network,
            "Memory": 256 * 1024 * 1024,
            "NanoCpus": 500_000_000,  # 0.5 vCPU
            "PidsLimit": 64,
            "ReadonlyRootfs": True,
            # A container tmpfs mount spec, not this process's own filesystem. mode=1777
            # (world-writable + sticky, same as a normal /tmp) — without it a tmpfs
            # mount defaults to root-owned 0755, and the non-root runner user can't
            # create anything under it (caught live: git clone failing with "Permission
            # denied" creating /workspace/repo, PLAN-PHASE-2-M3.md's own test run).
            "Tmpfs": {"/tmp": "size=64m,mode=1777", "/workspace": "size=256m,mode=1777"},  # noqa: S108
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "AutoRemove": False,  # we remove explicitly, after inspecting exit status
        },
    }


async def handle_start_run(
    sf: SessionFactory,
    sink: EventSink | None,
    settings: Settings,
    docker: aiodocker.Docker,
    job: ClaimedJob,
) -> None:
    run_id = uuid.UUID(job.payload["run_id"])
    run = await run_queries.get_run(sf, run_id)
    if run is None:
        log.warning("start_run_job_for_missing_run", run_id=str(run_id))
        return

    if run.container_id is not None:
        # Idempotency: a retried job found a container a prior attempt already created.
        log.info("start_run_already_launched", run_id=str(run_id), container_id=run.container_id)
        return

    if run.cancel_requested_at is not None:
        # Cancelled before we ever got to it — nothing to launch, nothing to stop.
        await run_commands.mark_cancelled(
            sf, sink, actor=ORCHESTRATOR, run_id=run_id, reason="cancelled before starting"
        )
        return

    repository = await run_queries.get_repository_for_task(sf, run.task_id)
    branch_name = compute_branch_name(run.task_key, run_id) if repository is not None else None

    agent_runtime = await run_queries.get_agent_runtime(sf, run.agent_id)
    agent_adapter = agent_runtime.adapter if agent_runtime is not None else "none"
    if agent_adapter == "claude_agent_sdk" and settings.anthropic_api_key is None:
        # Caught here, not left to the container to discover: a container launched
        # without the key would just fail immediately anyway, but only after spending a
        # container start and a confusing in-container error (PLAN-PHASE-2-M4.md §7).
        log.error("claude_agent_sdk_run_missing_api_key", run_id=str(run_id))
        await run_commands.fail_run(
            sf, sink, actor=ORCHESTRATOR, run_id=run_id,
            reason="ANTHROPIC_API_KEY is not configured; cannot launch a claude_agent_sdk run",
        )
        return

    token = mint_run_token(
        run_id=run_id,
        agent_id=run.agent_id,
        expires_at=int(time.time()) + settings.run_token_ttl_seconds,
        secret=settings.runner_token_secret.get_secret_value(),
    )
    config = build_container_config(
        run_token=token,
        run_id=run_id,
        agent_id=run.agent_id,
        settings=settings,
        repository=repository,
        branch_name=branch_name,
        agent_adapter=agent_adapter,
    )
    try:
        container = await docker.containers.create(config, name=f"acc-run-{run_id}")
        await container.start()
    except aiodocker.DockerError as exc:
        log.error("container_launch_failed", run_id=str(run_id), error=str(exc))
        await run_commands.fail_run(
            sf, sink, actor=ORCHESTRATOR, run_id=run_id, reason=f"container launch failed: {exc}"
        )
        return

    await run_commands.mark_run_starting(
        sf,
        sink,
        actor=ORCHESTRATOR,
        run_id=run_id,
        container_id=container.id,
        workspace_ref=WORKSPACE_DIR if repository is not None else None,
        branch_name=branch_name,
    )


async def _stop_and_remove(docker: aiodocker.Docker, container_id: str) -> None:
    container = docker.containers.container(container_id)
    try:
        await container.stop(t=STOP_GRACE_PERIOD_S)
    except aiodocker.DockerError as exc:
        if exc.status != CONTAINER_NOT_FOUND:
            log.warning("container_stop_failed", container_id=container_id, error=str(exc))
    try:
        info = await container.show()
        if info.get("State", {}).get("Running"):
            await container.kill()
    except aiodocker.DockerError as exc:
        if exc.status != CONTAINER_NOT_FOUND:
            log.warning("container_inspect_failed", container_id=container_id, error=str(exc))
    with contextlib.suppress(aiodocker.DockerError):
        await container.delete(force=True)


async def handle_cancel_run(
    sf: SessionFactory, sink: EventSink | None, docker: aiodocker.Docker, job: ClaimedJob
) -> None:
    run_id = uuid.UUID(job.payload["run_id"])
    run = await run_queries.get_run(sf, run_id)
    if run is None:
        log.warning("cancel_run_job_for_missing_run", run_id=str(run_id))
        return

    if RunStatus(run.status) not in {RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING}:
        # Already terminal — the run may have finished on its own in the race between
        # the cancel request and this job being claimed. Nothing left to do.
        return

    if run.container_id is not None:
        await _stop_and_remove(docker, run.container_id)

    try:
        await run_commands.mark_cancelled(
            sf, sink, actor=ORCHESTRATOR, run_id=run_id, reason="operator requested cancellation"
        )
    except Conflict:
        # The run reached a terminal state between our status check above and now
        # (e.g. it completed right as we were stopping its already-finished container).
        log.info("cancel_run_raced_a_finish", run_id=str(run_id))


async def reconcile_active_runs(
    sf: SessionFactory,
    sink: EventSink | None,
    docker: aiodocker.Docker,
    *,
    max_wall_clock: timedelta | None = None,
    timeout_warning_fraction: float | None = None,
) -> None:
    """Not a job handler — called directly on a timer by main.py. Reacts to something
    Docker already knows (a container has exited) rather than a time-based heartbeat
    staleness check, which is M5's narrower, separately-staged watchdog concern
    (PLAN-PHASE-2-M2.md §0.6). Runs with a pending cancellation are left to
    handle_cancel_run; this only catches *unexpected* exits.

    max_wall_clock (M4, PLAN-PHASE-2-M4.md §5): an orchestrator-side backstop on top of
    the SDK's own in-runner max_turns/max_budget_usd — stops a run's container directly
    regardless of whether the runner process inside it is cooperating, the same way an
    operator cancel already does. None (main.py's default when unconfigured) disables it,
    same as every other run limit in this system being configurable rather than assumed.

    timeout_warning_fraction (M4.2, PLAN-PHASE-2-M4.2.md §2/§7): fires a warning event
    once (agent_runs.timeout_warning_emitted_at guards the repeat, surviving a restart)
    when a run crosses this fraction of max_wall_clock — observability only, does not
    change when the hard kill above actually fires.
    """
    active = [
        *(await run_queries.list_runs(sf, status=RunStatus.STARTING.value, limit=200)),
        *(await run_queries.list_runs(sf, status=RunStatus.RUNNING.value, limit=200)),
    ]
    for run in active:
        if run.container_id is None or run.cancel_requested_at is not None:
            continue

        container = docker.containers.container(run.container_id)
        try:
            info = await container.show()
            still_running = info.get("State", {}).get("Running", False)
        except aiodocker.DockerError as exc:
            if exc.status != CONTAINER_NOT_FOUND:
                log.warning("reconcile_inspect_failed", run_id=str(run.id), error=str(exc))
                continue
            still_running = False
            info = {}

        if still_running:
            if (
                max_wall_clock is not None
                and run.started_at is not None
                and utcnow() - run.started_at > max_wall_clock
            ):
                await _stop_and_remove(docker, run.container_id)
                try:
                    await run_commands.fail_run(
                        sf, sink, actor=ORCHESTRATOR, run_id=run.id,
                        reason=f"wall-clock limit exceeded ({max_wall_clock})",
                    )
                except Conflict:
                    log.info("reconcile_wall_clock_raced_a_finish", run_id=str(run.id))
                else:
                    log.warning("run_failed_wall_clock_exceeded", run_id=str(run.id))
                continue

            if (
                max_wall_clock is not None
                and timeout_warning_fraction is not None
                and run.started_at is not None
                and run.timeout_warning_emitted_at is None
                and utcnow() - run.started_at > max_wall_clock * timeout_warning_fraction
            ):
                remaining = max_wall_clock - (utcnow() - run.started_at)
                await run_commands.record_timeout_warning(
                    sf, sink, actor=ORCHESTRATOR, run_id=run.id,
                    remaining_seconds=max(0, int(remaining.total_seconds())),
                )
            continue

        exit_code = info.get("State", {}).get("ExitCode")
        reason = (
            f"container exited unexpectedly (exit code {exit_code})"
            if info
            else "container no longer exists"
        )
        try:
            await run_commands.fail_run(sf, sink, actor=ORCHESTRATOR, run_id=run.id, reason=reason)
        except Conflict:
            # Raced a normal /complete or /fail call landing between our query and now.
            log.info("reconcile_raced_a_finish", run_id=str(run.id))
            continue
        log.warning("run_failed_container_exited", run_id=str(run.id), reason=reason)
        with contextlib.suppress(aiodocker.DockerError):
            await container.delete(force=True)


async def cleanup_finished_containers(
    sf: SessionFactory, docker: aiodocker.Docker, *, window: timedelta = timedelta(minutes=15)
) -> None:
    """The success-path counterpart to reconcile_active_runs's failure-path cleanup: a
    run that finishes normally (SUCCEEDED via /complete, or FAILED via /fail) leaves its
    now-exited container behind — nothing else removes it, since the internal API
    process never touches Docker (PLAN-PHASE-2-M2.md §1.6's whole point). Bounded to
    recently-finished runs rather than all history; deleting an already-gone container
    is a harmless no-op, so retrying within the window costs nothing but a few wasted
    calls (see acc.queries.runs.list_recently_finished_with_container)."""
    finished = await run_queries.list_recently_finished_with_container(
        sf, ended_after=utcnow() - window
    )
    for run in finished:
        assert run.container_id is not None
        with contextlib.suppress(aiodocker.DockerError):
            await docker.containers.container(run.container_id).delete(force=True)
