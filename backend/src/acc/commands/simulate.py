"""Dev-only activity generator: `acc simulate-activity`.

Exercises the real pipeline — every event goes through the same commands and the same
EventSink a real agent would use, with actor = SIMULATOR, so it is a faithful way to
demo and test the live dashboard before any agent runtime exists (Phase 2+). The
production guard lives in the CLI layer (acc.cli), which refuses to invoke this at all
when ACC_ENV=production.
"""

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import func, select

from acc.commands._base import EventSink, unit_of_work
from acc.commands.tasks import assign_task, transition_task
from acc.db.models import Agent, Task
from acc.db.session import SessionFactory
from acc.domain.events import SIMULATOR, AgentDecision, AgentMessage, EventType
from acc.domain.task_status import TERMINAL, TaskStatus

MESSAGES = (
    "Reading through the existing implementation before making changes.",
    "Checking test coverage for the affected module.",
    "Looking at recent commits for context on this area.",
    "Reproducing the reported behavior locally.",
    "Drafting the approach before writing any code.",
    "Running the test suite to confirm the current baseline.",
)

DECISIONS: tuple[tuple[str, str, list[str]], ...] = (
    (
        "Reuse the existing repository pattern",
        "Keeps the change consistent with the rest of the codebase.",
        ["Introduce a new abstraction"],
    ),
    (
        "Add a regression test before changing behavior",
        "Confirms the fix actually fixes something, and guards against it recurring.",
        [],
    ),
    (
        "Keep the change scoped to one module",
        "A wider refactor would be harder to review and riskier to ship.",
        ["Refactor the surrounding code at the same time"],
    ),
)

# Where a task moves next, absent a reason to do otherwise.
FORWARD: dict[TaskStatus, TaskStatus] = {
    TaskStatus.BACKLOG: TaskStatus.PLANNING,
    TaskStatus.PLANNING: TaskStatus.WORKING,
    TaskStatus.WORKING: TaskStatus.REVIEW,
    TaskStatus.REVIEW: TaskStatus.COMPLETED,
    TaskStatus.BLOCKED: TaskStatus.WORKING,
    TaskStatus.FAILED: TaskStatus.BACKLOG,
}


@dataclass(frozen=True)
class SimulatorConfig:
    ticks: int
    interval_seconds: float = 3.0
    # Probability, per tick, of each kind of action (independent; a tick may do neither).
    decision_probability: float = 0.25
    assign_probability: float = 0.2
    transition_probability: float = 0.3


async def _random_enabled_agent(sf: SessionFactory, rng: random.Random) -> Agent | None:
    async with sf() as s:
        agents = (await s.scalars(select(Agent).where(Agent.enabled))).all()
    return rng.choice(agents) if agents else None


async def _emit_agent_activity(
    sf: SessionFactory,
    sink: EventSink | None,
    rng: random.Random,
    agent: Agent,
    decision_probability: float,
) -> str:
    async with unit_of_work(sf, sink) as uow:
        if rng.random() < decision_probability:
            decision, rationale, alternatives = rng.choice(DECISIONS)
            await uow.emit(
                EventType.AGENT_DECISION,
                AgentDecision(decision=decision, rationale=rationale, alternatives=alternatives),
                actor=SIMULATOR,
                agent_id=agent.id,
                agent_slug=agent.slug,
            )
            return f"{agent.slug}: decided — {decision}"
        text = rng.choice(MESSAGES)
        await uow.emit(
            EventType.AGENT_MESSAGE,
            AgentMessage(text=text),
            actor=SIMULATOR,
            agent_id=agent.id,
            agent_slug=agent.slug,
        )
        return f"{agent.slug}: {text}"


async def _maybe_assign(
    sf: SessionFactory, sink: EventSink | None, rng: random.Random
) -> str | None:
    async with sf() as s:
        candidates = (
            await s.execute(
                select(Task.key, Task.version)
                .where(Task.status == TaskStatus.BACKLOG, Task.assigned_agent_id.is_(None))
                .order_by(func.random())
                .limit(1)
            )
        ).first()
        agents = (await s.scalars(select(Agent).where(Agent.enabled))).all()
    if candidates is None or not agents:
        return None
    key, version = candidates
    agent = rng.choice(agents)
    await assign_task(
        sf, sink, actor=SIMULATOR, key=key, agent_slug=agent.slug, expected_version=version
    )
    return f"{agent.slug}: assigned to {key}"


async def _maybe_transition(
    sf: SessionFactory, sink: EventSink | None, rng: random.Random
) -> str | None:
    async with sf() as s:
        candidates = (
            await s.execute(
                select(Task.key, Task.status, Task.version).where(Task.status.not_in(TERMINAL))
            )
        ).all()
    if not candidates:
        return None
    key, status, version = rng.choice(candidates)
    target = FORWARD.get(status)
    if target is None:
        return None
    try:
        await transition_task(
            sf,
            sink,
            actor=SIMULATOR,
            key=key,
            expected_version=version,
            to=target,
            reason="simulated progress",
        )
    except Exception:  # a concurrent change made this stale/illegal; skip this tick
        return None
    return f"{key}: {status.value} → {target.value}"


async def run_simulation(
    sf: SessionFactory,
    sink: EventSink | None,
    config: SimulatorConfig,
    rng: random.Random | None = None,
) -> AsyncIterator[str]:
    """Runs `config.ticks` ticks, yielding a short description of whatever happened each
    tick (nothing, an agent message/decision, an assignment, or a task transition)."""
    rng = rng or random.Random()  # noqa: S311 — dev-only fake activity, not a security context
    for _ in range(config.ticks):
        said = None
        if rng.random() < config.assign_probability:
            said = await _maybe_assign(sf, sink, rng)
        if said is None and rng.random() < config.transition_probability:
            said = await _maybe_transition(sf, sink, rng)
        if said is None:
            agent = await _random_enabled_agent(sf, rng)
            said = (
                await _emit_agent_activity(sf, sink, rng, agent, config.decision_probability)
                if agent
                else None
            )
        yield said or "(no enabled agents or eligible tasks this tick)"
        if config.interval_seconds > 0:
            await asyncio.sleep(config.interval_seconds)
