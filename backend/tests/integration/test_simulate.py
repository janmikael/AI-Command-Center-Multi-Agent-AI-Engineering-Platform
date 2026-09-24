"""acc.commands.simulate: runs through the real command pipeline, actor = SIMULATOR."""

import random

import pytest
from sqlalchemy import select

from acc.commands.simulate import SimulatorConfig, run_simulation
from acc.commands.tasks import create_task as create
from acc.commands.tasks import transition_task
from acc.db.models import Event, Task
from acc.db.session import SessionFactory
from acc.domain.events import SIMULATOR
from acc.domain.task_status import TaskStatus

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

# The legal path from backlog to each target status (§ task state machine).
PATH_TO: dict[TaskStatus, list[TaskStatus]] = {
    TaskStatus.BACKLOG: [],
    TaskStatus.PLANNING: [TaskStatus.PLANNING],
    TaskStatus.WORKING: [TaskStatus.PLANNING, TaskStatus.WORKING],
}


async def seed_task(sf: SessionFactory, title: str, status: TaskStatus) -> str:
    ref = await create(
        sf, None, actor="user:seed", created_by=None, project_slug="acc", title=title
    )
    version = ref.version
    for step in PATH_TO[status]:
        updated = await transition_task(
            sf, None, actor="user:seed", key=ref.key, expected_version=version, to=step
        )
        version = updated.version
    return ref.key


async def event_types(sf: SessionFactory) -> list[str]:
    async with sf() as s:
        return list((await s.scalars(select(Event.type).order_by(Event.seq))).all())


class TestRunSimulation:
    async def test_every_emitted_event_is_attributed_to_the_simulator(
        self, sf: SessionFactory
    ) -> None:
        config = SimulatorConfig(ticks=15, interval_seconds=0)
        [_ async for _ in run_simulation(sf, None, config, rng=random.Random(1))]
        async with sf() as s:
            actors = set((await s.scalars(select(Event.actor))).all()) - {"system"}
        # "system" comes from the `seeded` fixture's project/agent registration events.
        assert actors <= {SIMULATOR}
        assert SIMULATOR in actors  # at least one simulator event actually happened

    async def test_yields_one_description_per_tick(self, sf: SessionFactory) -> None:
        config = SimulatorConfig(ticks=8, interval_seconds=0)
        descriptions = [d async for d in run_simulation(sf, None, config, rng=random.Random(2))]
        assert len(descriptions) == 8
        assert all(isinstance(d, str) and d for d in descriptions)

    async def test_produces_agent_activity_events(self, sf: SessionFactory) -> None:
        config = SimulatorConfig(
            ticks=10, interval_seconds=0, assign_probability=0, transition_probability=0
        )
        [_ async for _ in run_simulation(sf, None, config, rng=random.Random(3))]
        types = await event_types(sf)
        assert "agent.message" in types or "agent.decision" in types

    async def test_can_assign_and_move_a_task(self, sf: SessionFactory) -> None:
        await seed_task(sf, "Move me", TaskStatus.BACKLOG)
        config = SimulatorConfig(
            ticks=20, interval_seconds=0, assign_probability=0.9, transition_probability=0.9
        )
        [_ async for _ in run_simulation(sf, None, config, rng=random.Random(4))]
        types = await event_types(sf)
        assert "task.assigned" in types
        assert "task.status_changed" in types

    async def test_never_moves_a_task_past_completed_or_cancelled(self, sf: SessionFactory) -> None:
        await seed_task(sf, "Already working", TaskStatus.WORKING)
        config = SimulatorConfig(ticks=30, interval_seconds=0, transition_probability=1.0)
        [_ async for _ in run_simulation(sf, None, config, rng=random.Random(5))]
        async with sf() as s:
            status = await s.scalar(select(Task.status))
        assert status in {TaskStatus.WORKING, TaskStatus.REVIEW, TaskStatus.COMPLETED}

    async def test_no_enabled_agents_and_no_tasks_does_not_crash(self, sf: SessionFactory) -> None:
        from sqlalchemy import update

        from acc.db.models import Agent

        async with sf() as s, s.begin():
            await s.execute(update(Agent).values(enabled=False))
        config = SimulatorConfig(ticks=5, interval_seconds=0)
        descriptions = [d async for d in run_simulation(sf, None, config, rng=random.Random(6))]
        assert len(descriptions) == 5
        assert all("no enabled agents" in d for d in descriptions)
