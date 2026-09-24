import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from acc.db.models import Agent, AgentCapability, Event, Task
from acc.db.session import SessionFactory
from acc.domain.agent_status import AgentStatus, derive_agent_status
from acc.domain.events import AGENT_ACTIVITY_TYPES
from acc.domain.task_status import ACTIVE, TERMINAL, TaskStatus
from acc.queries.events import EventView, list_events


@dataclass(frozen=True)
class TaskBrief:
    key: str
    title: str
    status: TaskStatus
    priority: int


@dataclass(frozen=True)
class AgentSummary:
    id: uuid.UUID
    slug: str
    display_name: str
    role: str
    adapter: str
    enabled: bool
    capabilities: list[str]
    status: AgentStatus
    active_task_count: int
    current_task: TaskBrief | None
    last_activity_at: datetime | None


@dataclass(frozen=True)
class AgentDetail:
    agent: AgentSummary
    open_tasks: list[TaskBrief]
    recent_events: list[EventView]


async def list_agents(
    sf: SessionFactory, *, now: datetime, slug: str | None = None
) -> list[AgentSummary]:
    agent_filter = [Agent.slug == slug] if slug else []
    async with sf() as s:
        agents = (
            await s.scalars(
                select(Agent).where(*agent_filter).order_by(Agent.created_at, Agent.slug)
            )
        ).all()
        caps = (
            await s.execute(
                select(AgentCapability.agent_id, AgentCapability.capability).order_by(
                    AgentCapability.priority, AgentCapability.capability
                )
            )
        ).all()
        active = (
            await s.execute(
                select(Task.assigned_agent_id, Task.key, Task.title, Task.status, Task.priority)
                .where(Task.status.in_(ACTIVE), Task.assigned_agent_id.is_not(None))
                .order_by(Task.priority, Task.updated_at.desc())
            )
        ).all()
        last_activity = dict(
            (
                await s.execute(
                    select(Event.agent_id, func.max(Event.ts))
                    .where(
                        Event.agent_id.is_not(None),
                        Event.type.in_([t.value for t in AGENT_ACTIVITY_TYPES]),
                    )
                    .group_by(Event.agent_id)
                )
            )
            .tuples()
            .all()
        )

    caps_by_agent: dict[uuid.UUID, list[str]] = defaultdict(list)
    for agent_id, capability in caps:
        caps_by_agent[agent_id].append(capability)
    active_by_agent: dict[uuid.UUID, list[TaskBrief]] = defaultdict(list)
    for agent_id, key, title, status, priority in active:
        active_by_agent[agent_id].append(TaskBrief(key, title, status, priority))

    summaries = []
    for a in agents:
        tasks = active_by_agent.get(a.id, [])
        last = last_activity.get(a.id)
        summaries.append(
            AgentSummary(
                id=a.id,
                slug=a.slug,
                display_name=a.display_name,
                role=a.role,
                adapter=a.adapter,
                enabled=a.enabled,
                capabilities=caps_by_agent.get(a.id, []),
                status=derive_agent_status(
                    enabled=a.enabled,
                    active_task_count=len(tasks),
                    last_activity_at=last,
                    now=now,
                ),
                active_task_count=len(tasks),
                current_task=tasks[0] if tasks else None,
                last_activity_at=last,
            )
        )
    return summaries


async def get_agent(sf: SessionFactory, slug: str, *, now: datetime) -> AgentDetail | None:
    found = await list_agents(sf, now=now, slug=slug)
    if not found:
        return None
    agent = found[0]
    async with sf() as s:
        open_tasks = (
            await s.execute(
                select(Task.key, Task.title, Task.status, Task.priority)
                .where(Task.assigned_agent_id == agent.id, Task.status.not_in(TERMINAL))
                .order_by(Task.priority, Task.updated_at.desc())
            )
        ).all()
    return AgentDetail(
        agent=agent,
        open_tasks=[TaskBrief(*row) for row in open_tasks],
        recent_events=await list_events(sf, agent_slug=slug, limit=50),
    )
