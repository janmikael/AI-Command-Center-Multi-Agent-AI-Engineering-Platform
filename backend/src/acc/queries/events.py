import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, Text, cast, func, select
from sqlalchemy.orm import aliased

from acc.db.models import Agent, Event, Project, Task, User
from acc.db.session import SessionFactory
from acc.domain.events import SIMULATOR, SYSTEM

MAX_PAGE = 500
# ^ Bounds two different callers: the REST feed (which additionally caps `limit` at 200
# via a stricter Query() constraint in the router, well under this) and the WebSocket
# resume path (acc.api.routers.ws), whose gap-too-large threshold IS this ceiling.


@dataclass(frozen=True)
class EventView:
    id: uuid.UUID
    seq: int
    ts: datetime
    type: str
    severity: str
    actor: str
    actor_label: str
    task_key: str | None
    task_title: str | None
    agent_slug: str | None
    agent_name: str | None
    run_id: uuid.UUID | None
    payload: dict[str, Any]


def actor_label(actor: str, user_name: str | None) -> str:
    """Human-readable actor: operator's name, 'System', 'Simulator', or the agent slug."""
    if actor.startswith("user:"):
        return user_name or "Operator"
    if actor == SYSTEM:
        return "System"
    if actor == SIMULATOR:
        return "Simulator"
    if actor.startswith("agent:"):
        return actor.removeprefix("agent:").replace("-", " ").title()
    return actor


def base_query() -> Select[Any]:
    """Events joined with the names the UI needs. Outer joins: most refs are optional."""
    actor_user = aliased(User)
    return (
        select(
            Event,
            Task.key,
            Task.title,
            Agent.slug,
            Agent.display_name,
            actor_user.display_name,
        )
        .outerjoin(Task, Task.id == Event.task_id)
        .outerjoin(Agent, Agent.id == Event.agent_id)
        .outerjoin(actor_user, Event.actor == func.concat("user:", cast(actor_user.id, Text)))
    )


def to_views(rows: Sequence[Any]) -> list[EventView]:
    return [
        EventView(
            id=event.id,
            seq=event.seq,
            ts=event.ts,
            type=event.type,
            severity=event.severity,
            actor=event.actor,
            actor_label=actor_label(event.actor, user_name),
            task_key=task_key,
            task_title=task_title,
            agent_slug=agent_slug,
            agent_name=agent_name,
            run_id=event.run_id,
            payload=event.payload,
        )
        for event, task_key, task_title, agent_slug, agent_name, user_name in rows
    ]


async def list_events(
    sf: SessionFactory,
    *,
    project_slug: str | None = None,
    task_key: str | None = None,
    agent_slug: str | None = None,
    run_id: uuid.UUID | None = None,
    types: Sequence[str] | None = None,
    severities: Sequence[str] | None = None,
    before_seq: int | None = None,
    after_seq: int | None = None,
    limit: int = 50,
) -> list[EventView]:
    """Newest first, paging backwards with before_seq.

    With after_seq the order flips to oldest first: that is the catch-up/replay mode
    used after a reconnect.

    run_id (Phase 2.5): scopes to one agent run — e.g. its agent.message narration,
    which has no other dedicated read path (PLAN-PHASE-2.5.md §2.1).
    """
    stmt = base_query()
    if project_slug is not None:
        stmt = stmt.where(
            Event.project_id
            == select(Project.id).where(Project.slug == project_slug).scalar_subquery()
        )
    if task_key is not None:
        stmt = stmt.where(Task.key == task_key)
    if agent_slug is not None:
        stmt = stmt.where(Agent.slug == agent_slug)
    if run_id is not None:
        stmt = stmt.where(Event.run_id == run_id)
    if types:
        stmt = stmt.where(Event.type.in_(types))
    if severities:
        stmt = stmt.where(Event.severity.in_(severities))
    if after_seq is not None:
        stmt = stmt.where(Event.seq > after_seq).order_by(Event.seq.asc())
    else:
        if before_seq is not None:
            stmt = stmt.where(Event.seq < before_seq)
        stmt = stmt.order_by(Event.seq.desc())
    stmt = stmt.limit(max(1, min(limit, MAX_PAGE)))

    async with sf() as session:
        rows = (await session.execute(stmt)).all()
    return to_views(rows)


async def latest_seq(sf: SessionFactory) -> int:
    async with sf() as session:
        return int(await session.scalar(select(func.max(Event.seq))) or 0)
