from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from acc.db.models import Event, Task
from acc.db.session import SessionFactory
from acc.domain.task_status import ACTIVE, TaskStatus
from acc.queries.agents import AgentSummary, list_agents
from acc.queries.events import EventView, latest_seq, list_events
from acc.queries.tasks import TaskSummary, list_tasks


@dataclass(frozen=True)
class Overview:
    agents: list[AgentSummary]
    task_counts: dict[TaskStatus, int]
    active_tasks: list[TaskSummary]
    events_last_24h: int
    recent_events: list[EventView]
    latest_seq: int


async def get_overview(sf: SessionFactory, *, now: datetime) -> Overview:
    async with sf() as s:
        counts = dict(
            (await s.execute(select(Task.status, func.count()).group_by(Task.status)))
            .tuples()
            .all()
        )
        events_24h = int(
            await s.scalar(
                select(func.count()).select_from(Event).where(Event.ts >= now - timedelta(hours=24))
            )
            or 0
        )
    return Overview(
        agents=await list_agents(sf, now=now),
        task_counts={status: counts.get(status, 0) for status in TaskStatus},
        active_tasks=(await list_tasks(sf, statuses=sorted(ACTIVE)))[:8],
        events_last_24h=events_24h,
        recent_events=await list_events(sf, limit=20),
        latest_seq=await latest_seq(sf),
    )
