"""acc.queries.runs.list_recently_finished_with_container: the query
acc.orchestrator.launcher.cleanup_finished_containers relies on to bound its scan to
recent history rather than all-time (PLAN-PHASE-2-M2.md §3.5)."""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from acc.commands import runs as run_commands
from acc.commands.tasks import create_task, transition_task
from acc.db.models import Agent, AgentRun
from acc.db.session import SessionFactory
from acc.domain.task_status import TaskStatus
from acc.ids import utcnow
from acc.queries import runs as run_queries

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

ACTOR = "user:seed"


async def make_finished_run(sf: SessionFactory, *, ended_at: datetime) -> uuid.UUID:
    async with sf() as s, s.begin():
        await s.execute(
            update(Agent).where(Agent.slug == "developer").values(adapter="stub")
        )
    ref = await create_task(
        sf, None, actor=ACTOR, created_by=None, project_slug="acc", title="t", assignee="developer"
    )
    version = ref.version
    for to in (TaskStatus.PLANNING, TaskStatus.WORKING):
        updated = await transition_task(
            sf, None, actor=ACTOR, key=ref.key, expected_version=version, to=to
        )
        version = updated.version
    async with sf() as s:
        run = await s.scalar(select(AgentRun).where(AgentRun.task_id == ref.id))
    assert run is not None
    run_id = run.id

    await run_commands.mark_run_starting(
        sf, None, actor="system:orchestrator", run_id=run_id, container_id="c-test"
    )
    await run_commands.record_heartbeat(sf, None, run_id=run_id)
    await run_commands.complete_run(sf, None, actor="x", run_id=run_id, result_summary="ok")
    async with sf() as s, s.begin():
        await s.execute(update(AgentRun).where(AgentRun.id == run_id).values(ended_at=ended_at))
    return run_id


class TestListRecentlyFinishedWithContainer:
    async def test_includes_a_run_that_ended_inside_the_window(self, sf: SessionFactory) -> None:
        run_id = await make_finished_run(sf, ended_at=utcnow() - timedelta(minutes=1))
        found = await run_queries.list_recently_finished_with_container(
            sf, ended_after=utcnow() - timedelta(minutes=15)
        )
        assert found[0].id == run_id

    async def test_excludes_a_run_that_ended_outside_the_window(self, sf: SessionFactory) -> None:
        await make_finished_run(sf, ended_at=utcnow() - timedelta(hours=2))
        found = await run_queries.list_recently_finished_with_container(
            sf, ended_after=utcnow() - timedelta(minutes=15)
        )
        assert found == []

    async def test_excludes_runs_with_no_container(self, sf: SessionFactory) -> None:
        run_id = await make_finished_run(sf, ended_at=utcnow() - timedelta(minutes=1))
        async with sf() as s, s.begin():
            await s.execute(update(AgentRun).where(AgentRun.id == run_id).values(container_id=None))
        found = await run_queries.list_recently_finished_with_container(
            sf, ended_after=utcnow() - timedelta(minutes=15)
        )
        assert found == []
