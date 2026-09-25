"""acc.commands.runs: the agent run lifecycle, and the transition_task side effect that
enqueues it (PLAN-PHASE-2.md §0.1). Runs through the real command pipeline against
Postgres, same standard as every other command test in this suite."""

import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from acc.commands import runs as run_commands
from acc.commands.tasks import create_task, transition_task
from acc.db.models import Agent, AgentRun, Job, Task
from acc.db.session import SessionFactory
from acc.domain.agent_run import RunStatus
from acc.domain.errors import Conflict, InvalidTransition, NotFound
from acc.domain.task_status import TaskStatus

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

ACTOR = "user:seed"


async def _make_runnable(sf: SessionFactory, slug: str = "developer") -> None:
    """Seeded agents all have adapter='none' (Phase 1); simulate the M3 world where the
    developer agent has a real adapter wired up."""
    async with sf() as s, s.begin():
        await s.execute(update(Agent).where(Agent.slug == slug).values(adapter="claude_agent_sdk"))


async def _task_to_working(sf: SessionFactory, *, assignee: str | None = "developer") -> str:
    ref = await create_task(
        sf, None, actor=ACTOR, created_by=None, project_slug="acc", title="t", assignee=assignee
    )
    version = ref.version
    for to in (TaskStatus.PLANNING, TaskStatus.WORKING):
        updated = await transition_task(
            sf, None, actor=ACTOR, key=ref.key, expected_version=version, to=to
        )
        version = updated.version
    return ref.key


async def _get_task(sf: SessionFactory, key: str) -> Task:
    async with sf() as s:
        task = await s.scalar(select(Task).where(Task.key == key))
    assert task is not None
    return task


async def _get_run(sf: SessionFactory, task_key: str) -> AgentRun:
    task = await _get_task(sf, task_key)
    async with sf() as s:
        run = await s.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
    assert run is not None
    return run


class TestEnqueueOnTransition:
    async def test_working_with_runnable_agent_enqueues_a_run_and_job(
        self, sf: SessionFactory
    ) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        assert run.status == RunStatus.QUEUED.value

        async with sf() as s:
            job = await s.scalar(select(Job).where(Job.kind == "start_run"))
        assert job is not None
        assert job.payload == {"run_id": str(run.id)}
        assert job.status == "pending"

    async def test_working_with_adapter_none_enqueues_nothing(self, sf: SessionFactory) -> None:
        """Phase 1 behavior is unchanged: a plain manual task never gets a run."""
        key = await _task_to_working(sf)  # developer still has adapter='none'
        async with sf() as s:
            run = await s.scalar(select(AgentRun).where(AgentRun.task_id.isnot(None)))
        assert run is None
        task = await _get_task(sf, key)
        assert task.status == TaskStatus.WORKING

    async def test_working_with_no_assignee_enqueues_nothing(self, sf: SessionFactory) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf, assignee=None)
        async with sf() as s:
            run = await s.scalar(select(AgentRun).where(AgentRun.task_id.isnot(None)))
        assert run is None
        task = await _get_task(sf, key)
        assert task.status == TaskStatus.WORKING

    async def test_working_with_a_since_disabled_agent_enqueues_nothing(
        self, sf: SessionFactory
    ) -> None:
        """Assigned while enabled, disabled afterwards, then moved to working."""
        await _make_runnable(sf)
        ref = await create_task(
            sf, None, actor=ACTOR, created_by=None, project_slug="acc", title="t",
            assignee="developer",
        )
        await transition_task(
            sf, None, actor=ACTOR, key=ref.key, expected_version=ref.version, to=TaskStatus.PLANNING
        )
        async with sf() as s, s.begin():
            await s.execute(update(Agent).where(Agent.slug == "developer").values(enabled=False))
        await transition_task(
            sf, None, actor=ACTOR, key=ref.key, expected_version=1, to=TaskStatus.WORKING
        )
        async with sf() as s:
            run = await s.scalar(select(AgentRun).where(AgentRun.task_id.isnot(None)))
        assert run is None

    async def test_second_working_entry_with_a_still_active_run_is_a_conflict(
        self, sf: SessionFactory
    ) -> None:
        """Defensive: the DB's own partial unique index is the ultimate backstop (tested
        separately below), but the app-level check must fire first with a clean error."""
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        # Move it back out of working without the run itself ever finishing, then
        # straight back in — the still-QUEUED run is still active.
        await transition_task(
            sf, None, actor=ACTOR, key=key, expected_version=2, to=TaskStatus.BLOCKED
        )
        with pytest.raises(Conflict):
            await transition_task(
                sf, None, actor=ACTOR, key=key, expected_version=3, to=TaskStatus.WORKING
            )


class TestLifecycle:
    async def test_starting_running_succeeded_moves_task_to_review(
        self, sf: SessionFactory
    ) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)

        await run_commands.mark_run_starting(
            sf, None, actor="system:orchestrator", run_id=run.id, container_id="c1"
        )
        await run_commands.record_heartbeat(sf, None, run_id=run.id)
        run = await _get_run(sf, key)
        assert run.status == RunStatus.RUNNING.value
        assert run.last_heartbeat_at is not None

        await run_commands.record_step(
            sf, None, actor="system:orchestrator", run_id=run.id, text="Running tests"
        )
        run = await _get_run(sf, key)
        assert run.current_step == "Running tests"

        await run_commands.complete_run(
            sf, None, actor="system:orchestrator", run_id=run.id, result_summary="done",
            branch_name="acc/t-1",
        )
        run = await _get_run(sf, key)
        assert run.status == RunStatus.SUCCEEDED.value
        assert run.ended_at is not None
        assert run.branch_name == "acc/t-1"
        task = await _get_task(sf, key)
        assert task.status == TaskStatus.REVIEW

    async def test_fail_run_moves_task_to_failed(self, sf: SessionFactory) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.fail_run(
            sf, None, actor="system:orchestrator", run_id=run.id, reason="crashed"
        )
        run = await _get_run(sf, key)
        assert run.status == RunStatus.FAILED.value
        assert run.error == {"reason": "crashed"}
        task = await _get_task(sf, key)
        assert task.status == TaskStatus.FAILED

    async def test_mark_cancelled_moves_task_to_blocked(self, sf: SessionFactory) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.mark_cancelled(
            sf, None, actor="system:orchestrator", run_id=run.id, reason="operator stopped it"
        )
        task = await _get_task(sf, key)
        assert task.status == TaskStatus.BLOCKED

    async def test_mark_timed_out_moves_task_to_blocked(self, sf: SessionFactory) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.mark_run_starting(
            sf, None, actor="system:orchestrator", run_id=run.id, container_id="c1"
        )
        await run_commands.mark_timed_out(sf, None, actor="system:orchestrator", run_id=run.id)
        run = await _get_run(sf, key)
        assert run.status == RunStatus.TIMED_OUT.value
        task = await _get_task(sf, key)
        assert task.status == TaskStatus.BLOCKED

    async def test_finishing_a_run_whose_task_already_left_working_does_not_touch_the_task(
        self, sf: SessionFactory
    ) -> None:
        """The task may have been moved on manually in the meantime; a late-arriving run
        result must not clobber whatever the operator already decided."""
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.mark_run_starting(
            sf, None, actor="system:orchestrator", run_id=run.id, container_id="c1"
        )
        await run_commands.record_heartbeat(sf, None, run_id=run.id)
        # Operator manually moves the task away from working before the run reports back.
        await transition_task(
            sf, None, actor=ACTOR, key=key, expected_version=2, to=TaskStatus.BLOCKED
        )
        await run_commands.complete_run(
            sf, None, actor="system:orchestrator", run_id=run.id, result_summary="late"
        )
        task = await _get_task(sf, key)
        assert task.status == TaskStatus.BLOCKED  # unchanged by the late completion

    async def test_double_finish_is_rejected_by_the_run_state_machine(
        self, sf: SessionFactory
    ) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.fail_run(sf, None, actor="x", run_id=run.id, reason="first")
        with pytest.raises(InvalidTransition):
            await run_commands.fail_run(sf, None, actor="x", run_id=run.id, reason="second")

    async def test_unknown_run_is_not_found(self, sf: SessionFactory) -> None:
        with pytest.raises(NotFound):
            await run_commands.fail_run(
                sf, None, actor="x", run_id=uuid.uuid4(), reason="doesn't exist"
            )


class TestRequestCancel:
    async def test_sets_cancel_requested_at_on_an_active_run(self, sf: SessionFactory) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.request_cancel(sf, None, run_id=run.id)
        run = await _get_run(sf, key)
        assert run.cancel_requested_at is not None

    async def test_rejects_a_terminal_run(self, sf: SessionFactory) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.fail_run(sf, None, actor="x", run_id=run.id, reason="already done")
        with pytest.raises(Conflict):
            await run_commands.request_cancel(sf, None, run_id=run.id)

    async def test_enqueues_a_cancel_run_job(self, sf: SessionFactory) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.request_cancel(sf, None, run_id=run.id)
        async with sf() as s:
            job = await s.scalar(select(Job).where(Job.kind == "cancel_run"))
        assert job is not None
        assert job.payload == {"run_id": str(run.id)}
        assert job.status == "pending"

    async def test_rejecting_the_request_does_not_enqueue_a_job(
        self, sf: SessionFactory
    ) -> None:
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        run = await _get_run(sf, key)
        await run_commands.fail_run(sf, None, actor="x", run_id=run.id, reason="already done")
        with pytest.raises(Conflict):
            await run_commands.request_cancel(sf, None, run_id=run.id)
        async with sf() as s:
            job = await s.scalar(select(Job).where(Job.kind == "cancel_run"))
        assert job is None


class TestConstraints:
    async def test_db_rejects_a_second_concurrent_active_run_for_the_same_task(
        self, sf: SessionFactory, app_conn: AsyncConnection
    ) -> None:
        """The application-level check in TestEnqueueOnTransition covers the normal
        path; this confirms the partial unique index is a real backstop, not decoration,
        by inserting directly — bypassing enqueue_start_run_in_uow's own check entirely."""
        await _make_runnable(sf)
        key = await _task_to_working(sf)
        task = await _get_task(sf, key)
        with pytest.raises(DBAPIError, match="ix_agent_runs_one_active_per_task"):
            await app_conn.execute(
                text(
                    "INSERT INTO agent_runs (id, task_id, agent_id, status) "
                    "VALUES (gen_random_uuid(), :task_id, :agent_id, 'queued')"
                ),
                {"task_id": str(task.id), "agent_id": str(task.assigned_agent_id)},
            )
