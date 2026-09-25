"""Agent run lifecycle: the only code that writes agent_runs/file_changes or enqueues
run-related jobs.

M1 (Phase 2) scope: the row + job-queue side only. Actually launching a sandboxed
container is acc.orchestrator.launcher (M2); this module has no knowledge of Docker and
imports nothing from it. M3 adds record_file_changes, called from the git round trip
(PLAN-PHASE-2-M3.md); tool_calls/usage_records still have no writer — those are M4's
Claude Agent SDK adapter's concern, not this milestone's. See PLAN-PHASE-2.md §1.5 for
why acc.runner never calls into this module directly either — it goes through the
internal HTTP API instead, so the untrusted container never gets a database credential.

Import direction: this module depends on acc.commands._task_mutations, never on
acc.commands.tasks — acc.commands.tasks depends on this module (for the enqueue side
effect on transition_task), so the reverse dependency would be a cycle.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acc.commands._base import EventSink, UnitOfWork, unit_of_work
from acc.commands._task_mutations import apply_status_change, lock_task_by_id
from acc.db.models import AgentRun, FileChange, Job
from acc.db.session import SessionFactory
from acc.domain.agent_run import ACTIVE, RunStatus, validate_run_transition
from acc.domain.errors import Conflict, NotFound
from acc.domain.events import (
    EventType,
    RunCancelled,
    RunFailed,
    RunHeartbeatMissed,
    RunQueued,
    RunStarted,
    RunStep,
    RunSucceeded,
    RunTimedOut,
    Severity,
)
from acc.domain.task_status import TaskStatus, validate_orchestrator_transition
from acc.ids import utcnow

# Which task status a run's terminal outcome moves its task to. _finish_run below checks
# each one against task_status.ORCHESTRATOR_ALLOWED via validate_orchestrator_transition
# before applying it — this table alone is not what enforces legality.
_TASK_TARGET_FOR: dict[RunStatus, TaskStatus] = {
    RunStatus.SUCCEEDED: TaskStatus.REVIEW,
    RunStatus.FAILED: TaskStatus.FAILED,
    RunStatus.CANCELLED: TaskStatus.BLOCKED,
    RunStatus.TIMED_OUT: TaskStatus.BLOCKED,
}


async def _lock_run(session: AsyncSession, run_id: uuid.UUID) -> AgentRun:
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None:
        raise NotFound(f"Run {run_id} not found", run_id=str(run_id))
    return run


async def _emit_run(
    uow: UnitOfWork,
    run: AgentRun,
    event_type: EventType,
    payload: Any,
    *,
    actor: str,
    severity: Severity = Severity.INFO,
    task_key: str | None = None,
    agent_slug: str | None = None,
) -> None:
    await uow.emit(
        event_type,
        payload,
        actor=actor,
        severity=severity,
        task_id=run.task_id,
        agent_id=run.agent_id,
        run_id=run.id,
        task_key=task_key,
        agent_slug=agent_slug,
    )


async def enqueue_start_run_in_uow(
    uow: UnitOfWork,
    *,
    actor: str,
    task_id: uuid.UUID,
    task_key: str,
    agent_id: uuid.UUID,
    agent_slug: str,
    created_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """Create the agent_runs row (queued) and a start_run job, inside a transaction the
    caller already has open. Used by acc.commands.tasks.transition_task; also the inner
    half of enqueue_start_run below.
    """
    s = uow.session
    existing = await s.scalar(
        select(AgentRun.id).where(
            AgentRun.task_id == task_id, AgentRun.status.in_([st.value for st in ACTIVE])
        )
    )
    if existing is not None:
        raise Conflict(f"Task {task_key} already has an active run", task_key=task_key)

    run = AgentRun(
        task_id=task_id, agent_id=agent_id, status=RunStatus.QUEUED.value, created_by=created_by
    )
    s.add(run)
    await s.flush()

    s.add(Job(kind="start_run", payload={"run_id": str(run.id)}))

    await _emit_run(
        uow,
        run,
        EventType.RUN_QUEUED,
        RunQueued(id=run.id, task_key=task_key, agent_slug=agent_slug),
        actor=actor,
        task_key=task_key,
        agent_slug=agent_slug,
    )
    return run.id


async def enqueue_start_run(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    task_id: uuid.UUID,
    task_key: str,
    agent_id: uuid.UUID,
    agent_slug: str,
    created_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """Standalone entry point (e.g. a future manual "retry" trigger). The normal path is
    the side effect inside acc.commands.tasks.transition_task, which calls
    enqueue_start_run_in_uow directly so it shares that transaction instead of opening
    its own."""
    async with unit_of_work(sf, sink) as uow:
        return await enqueue_start_run_in_uow(
            uow,
            actor=actor,
            task_id=task_id,
            task_key=task_key,
            agent_id=agent_id,
            agent_slug=agent_slug,
            created_by=created_by,
        )


async def mark_run_starting(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    run_id: uuid.UUID,
    container_id: str,
    workspace_ref: str | None = None,
    branch_name: str | None = None,
) -> None:
    """Called by the orchestrator's launcher right after the container is created.

    workspace_ref/branch_name (M3): set here, by the launcher, before the container even
    starts — not self-reported by the runner later. The launcher computes the branch
    name deterministically from the task key (PLAN-PHASE-2-M3.md §3), so this is
    authoritative from our side; the runner is only ever told what to push to, never in
    a position to claim it pushed somewhere else.
    """
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        validate_run_transition(RunStatus(run.status), RunStatus.STARTING)
        run.status = RunStatus.STARTING.value
        run.container_id = container_id
        run.started_at = utcnow()
        if workspace_ref is not None:
            run.workspace_ref = workspace_ref
        if branch_name is not None:
            run.branch_name = branch_name
            # Visible to the operator as soon as it's decided, not only once a run
            # finishes — the fix noted in PLAN-PHASE-2-M3.md §1 (tasks.branch_name had a
            # writer for the column but nothing ever called it before this milestone).
            task = await lock_task_by_id(uow.session, run.task_id)
            task.branch_name = branch_name
        await _emit_run(
            uow, run, EventType.RUN_STARTED, RunStarted(id=run.id, container_id=container_id),
            actor=actor,
        )


async def record_heartbeat(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    run_id: uuid.UUID,
    current_step: str | None = None,
) -> None:
    """Liveness only — no event per call (too noisy; see PLAN-PHASE-2.md §0.5's reasoning
    for keeping realtime traffic proportional to what's actually informative). No actor
    parameter: nothing is emitted, so there is nothing to attribute. The first heartbeat
    after STARTING confirms the runner process is alive and flips the run to RUNNING."""
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        if run.status == RunStatus.STARTING.value:
            validate_run_transition(RunStatus.STARTING, RunStatus.RUNNING)
            run.status = RunStatus.RUNNING.value
        run.last_heartbeat_at = utcnow()
        if current_step is not None:
            run.current_step = current_step


async def record_step(
    sf: SessionFactory, sink: EventSink | None, *, actor: str, run_id: uuid.UUID, text: str
) -> None:
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        run.current_step = text
        await _emit_run(uow, run, EventType.RUN_STEP, RunStep(id=run.id, text=text), actor=actor)


async def record_heartbeat_missed(
    sf: SessionFactory, sink: EventSink | None, *, actor: str, run_id: uuid.UUID
) -> None:
    """A warning stage before the orchestrator's watchdog decides to time the run out
    (mirrors budget.warning vs budget.exceeded, ARCHITECTURE.md §5.5)."""
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        await _emit_run(
            uow,
            run,
            EventType.RUN_HEARTBEAT_MISSED,
            RunHeartbeatMissed(id=run.id, last_heartbeat_at=run.last_heartbeat_at),
            actor=actor,
            severity=Severity.WARN,
        )


async def _finish_run(
    uow: UnitOfWork,
    run: AgentRun,
    to: RunStatus,
    *,
    actor: str,
    event_type: EventType,
    payload: Any,
) -> None:
    validate_run_transition(RunStatus(run.status), to)
    run.status = to.value
    run.ended_at = utcnow()
    await _emit_run(uow, run, event_type, payload, actor=actor)

    task = await lock_task_by_id(uow.session, run.task_id)
    target = _TASK_TARGET_FOR[to]
    if task.status == TaskStatus.WORKING:
        validate_orchestrator_transition(task.status, target)
        await apply_status_change(uow, task, target, actor=actor, reason=f"run {to.value}")


async def complete_run(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    run_id: uuid.UUID,
    result_summary: str,
    branch_name: str | None = None,
) -> None:
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        run.result_summary = result_summary
        if branch_name is not None:
            run.branch_name = branch_name
        await _finish_run(
            uow,
            run,
            RunStatus.SUCCEEDED,
            actor=actor,
            event_type=EventType.RUN_SUCCEEDED,
            payload=RunSucceeded(id=run.id, result_summary=result_summary),
        )


async def fail_run(
    sf: SessionFactory, sink: EventSink | None, *, actor: str, run_id: uuid.UUID, reason: str
) -> None:
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        run.error = {"reason": reason}
        await _finish_run(
            uow,
            run,
            RunStatus.FAILED,
            actor=actor,
            event_type=EventType.RUN_FAILED,
            payload=RunFailed(id=run.id, reason=reason),
        )


async def mark_cancelled(
    sf: SessionFactory, sink: EventSink | None, *, actor: str, run_id: uuid.UUID, reason: str
) -> None:
    """Called once the orchestrator has confirmed the container is actually stopped (or
    immediately, for a run that never left QUEUED)."""
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        await _finish_run(
            uow,
            run,
            RunStatus.CANCELLED,
            actor=actor,
            event_type=EventType.RUN_CANCELLED,
            payload=RunCancelled(id=run.id, reason=reason),
        )


async def mark_timed_out(
    sf: SessionFactory, sink: EventSink | None, *, actor: str, run_id: uuid.UUID
) -> None:
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        run.error = {"reason": "heartbeat or wall-clock timeout"}
        await _finish_run(
            uow,
            run,
            RunStatus.TIMED_OUT,
            actor=actor,
            event_type=EventType.RUN_TIMED_OUT,
            payload=RunTimedOut(id=run.id),
        )


async def request_cancel(
    sf: SessionFactory, sink: EventSink | None, *, run_id: uuid.UUID
) -> None:
    """The operator-facing half of cancellation: records the request and enqueues a
    cancel_run job in the same transaction (ADR-2, same durable-queue discipline as
    enqueue_start_run_in_uow). It does not change run.status itself — the orchestrator's
    launcher (acc.orchestrator.launcher.handle_cancel_run, M2) is what confirms the
    container is actually gone and moves the run to CANCELLED, avoiding a race with a
    run that's about to succeed on its own."""
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)
        if RunStatus(run.status) not in ACTIVE:
            raise Conflict(
                f"Run {run_id} is {run.status}; it can't be cancelled", run_id=str(run_id)
            )
        run.cancel_requested_at = utcnow()
        uow.session.add(Job(kind="cancel_run", payload={"run_id": str(run.id)}))


async def record_file_changes(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    run_id: uuid.UUID,
    changes: Sequence[Mapping[str, Any]],
) -> None:
    """No event emitted per call — same reasoning as record_step's silence on heartbeats:
    a diff can touch many files in one report, and the run's file-changes view
    (acc.queries.runs, GET /api/v1/runs/{id}/file-changes) reads this table directly, not
    the event feed. Each mapping: {path, change_type, additions?, deletions?}."""
    async with unit_of_work(sf, sink) as uow:
        run = await _lock_run(uow.session, run_id)  # confirms the run exists
        for change in changes:
            uow.session.add(
                FileChange(
                    run_id=run.id,
                    task_id=run.task_id,
                    path=change["path"],
                    change_type=change["change_type"],
                    additions=change.get("additions"),
                    deletions=change.get("deletions"),
                )
            )


# record_tool_call / record_usage (writing tool_calls / usage_records) are M4's Claude
# Agent SDK adapter's concern — nothing produces that data yet, so there is nothing to
# test against until then.
