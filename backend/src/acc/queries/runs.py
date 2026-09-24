import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, select

from acc.db.models import Agent, AgentRun, Task
from acc.db.session import SessionFactory


@dataclass(frozen=True)
class RunView:
    id: uuid.UUID
    task_id: uuid.UUID
    task_key: str
    agent_id: uuid.UUID
    agent_slug: str
    status: str
    provider_session_id: str | None
    container_id: str | None
    branch_name: str | None
    current_step: str | None
    cancel_requested_at: datetime | None
    queued_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    last_heartbeat_at: datetime | None
    result_summary: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal


def _base_query() -> Select[Any]:
    return (
        select(AgentRun, Task.key, Agent.slug)
        .join(Task, Task.id == AgentRun.task_id)
        .join(Agent, Agent.id == AgentRun.agent_id)
    )


def _view(run: AgentRun, task_key: str, agent_slug: str) -> RunView:
    return RunView(
        id=run.id,
        task_id=run.task_id,
        task_key=task_key,
        agent_id=run.agent_id,
        agent_slug=agent_slug,
        status=run.status,
        provider_session_id=run.provider_session_id,
        container_id=run.container_id,
        branch_name=run.branch_name,
        current_step=run.current_step,
        cancel_requested_at=run.cancel_requested_at,
        queued_at=run.queued_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        last_heartbeat_at=run.last_heartbeat_at,
        result_summary=run.result_summary,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cache_read_tokens=run.cache_read_tokens,
        cost_usd=Decimal(run.cost_usd),
    )


async def get_run(sf: SessionFactory, run_id: uuid.UUID) -> RunView | None:
    async with sf() as s:
        row = (await s.execute(_base_query().where(AgentRun.id == run_id))).one_or_none()
    return _view(*row) if row else None


async def list_runs(
    sf: SessionFactory,
    *,
    task_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[RunView]:
    stmt = _base_query()
    if task_id is not None:
        stmt = stmt.where(AgentRun.task_id == task_id)
    if agent_id is not None:
        stmt = stmt.where(AgentRun.agent_id == agent_id)
    if status is not None:
        stmt = stmt.where(AgentRun.status == status)
    stmt = stmt.order_by(AgentRun.queued_at.desc()).limit(max(1, min(limit, 200)))
    async with sf() as s:
        rows = (await s.execute(stmt)).all()
    return [_view(*row) for row in rows]
