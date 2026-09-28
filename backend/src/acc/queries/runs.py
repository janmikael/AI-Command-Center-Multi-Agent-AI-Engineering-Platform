import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, select

from acc.db.models import Agent, AgentRun, FileChange, Repository, Task, ToolCall, UsageRecord
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
    workspace_ref: str | None
    branch_name: str | None
    current_step: str | None
    cancel_requested_at: datetime | None
    queued_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    last_heartbeat_at: datetime | None
    timeout_warning_emitted_at: datetime | None
    result_summary: str | None
    error: dict[str, Any] | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
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
        workspace_ref=run.workspace_ref,
        branch_name=run.branch_name,
        current_step=run.current_step,
        cancel_requested_at=run.cancel_requested_at,
        queued_at=run.queued_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        last_heartbeat_at=run.last_heartbeat_at,
        timeout_warning_emitted_at=run.timeout_warning_emitted_at,
        result_summary=run.result_summary,
        error=run.error,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cache_read_tokens=run.cache_read_tokens,
        cache_write_tokens=run.cache_write_tokens,
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


@dataclass(frozen=True)
class AgentRuntime:
    """What the launcher needs to decide how a run should actually execute (PLAN-
    PHASE-2-M4.md §9) — separate from RunView/AgentOut, which are operator-facing."""

    adapter: str
    model: str | None


async def get_agent_runtime(sf: SessionFactory, agent_id: uuid.UUID) -> AgentRuntime | None:
    async with sf() as s:
        row = (
            await s.execute(select(Agent.adapter, Agent.model).where(Agent.id == agent_id))
        ).one_or_none()
    return AgentRuntime(*row) if row else None


@dataclass(frozen=True)
class RunRepository:
    remote_url: str
    default_branch: str


async def get_repository_for_task(sf: SessionFactory, task_id: uuid.UUID) -> RunRepository | None:
    """None means the task has no linked repository — handle_start_run's cue to skip
    the git step entirely (PLAN-PHASE-2-M3.md §0.4/§3); most tasks, still today."""
    async with sf() as s:
        row = (
            await s.execute(
                select(Repository.remote_url, Repository.default_branch)
                .join(Task, Task.repository_id == Repository.id)
                .where(Task.id == task_id)
            )
        ).one_or_none()
    return RunRepository(*row) if row else None


@dataclass(frozen=True)
class FileChangeView:
    id: uuid.UUID
    path: str
    change_type: str
    additions: int | None
    deletions: int | None
    created_at: datetime


async def list_file_changes(sf: SessionFactory, run_id: uuid.UUID) -> list[FileChangeView]:
    stmt = (
        select(FileChange)
        .where(FileChange.run_id == run_id)
        .order_by(FileChange.path)
    )
    async with sf() as s:
        rows = (await s.scalars(stmt)).all()
    return [
        FileChangeView(
            id=r.id,
            path=r.path,
            change_type=r.change_type,
            additions=r.additions,
            deletions=r.deletions,
            created_at=r.created_at,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class ToolCallView:
    id: uuid.UUID
    tool: str
    arguments: dict[str, Any]
    allowed: bool
    denial_reason: str | None
    rule_id: uuid.UUID | None
    blocked_by_effect: str | None
    result_excerpt: str | None
    exit_code: int | None
    duration_ms: int | None
    started_at: datetime
    ended_at: datetime | None


async def list_tool_calls(sf: SessionFactory, run_id: uuid.UUID) -> list[ToolCallView]:
    stmt = select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.started_at)
    async with sf() as s:
        rows = (await s.scalars(stmt)).all()
    return [
        ToolCallView(
            id=r.id,
            tool=r.tool,
            arguments=r.arguments,
            allowed=r.allowed,
            denial_reason=r.denial_reason,
            rule_id=r.rule_id,
            blocked_by_effect=r.blocked_by_effect,
            result_excerpt=r.result_excerpt,
            exit_code=r.exit_code,
            duration_ms=r.duration_ms,
            started_at=r.started_at,
            ended_at=r.ended_at,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class UsageRecordView:
    id: uuid.UUID
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal
    cost_source: str
    recorded_at: datetime


async def list_usage(sf: SessionFactory, run_id: uuid.UUID) -> list[UsageRecordView]:
    stmt = (
        select(UsageRecord)
        .where(UsageRecord.run_id == run_id)
        .order_by(UsageRecord.recorded_at)
    )
    async with sf() as s:
        rows = (await s.scalars(stmt)).all()
    return [
        UsageRecordView(
            id=r.id,
            provider=r.provider,
            model=r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_write_tokens=r.cache_write_tokens,
            cost_usd=Decimal(r.cost_usd),
            cost_source=r.cost_source,
            recorded_at=r.recorded_at,
        )
        for r in rows
    ]


TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "timed_out")


async def list_recently_finished_with_container(
    sf: SessionFactory, *, ended_after: datetime, limit: int = 200
) -> list[RunView]:
    """Terminal runs that still have a container to clean up (PLAN-PHASE-2-M2.md §3.5).
    Bounded by `ended_after` rather than scanning all history — cleanup is idempotent
    (deleting an already-gone container just 404s), so a run that ages out of this
    window without ever being successfully cleaned up stays that way; acceptable for M2,
    revisit if it proves not to be."""
    stmt = (
        _base_query()
        .where(
            AgentRun.status.in_(TERMINAL_STATUSES),
            AgentRun.container_id.isnot(None),
            AgentRun.ended_at.isnot(None),
            AgentRun.ended_at > ended_after,
        )
        .order_by(AgentRun.ended_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    async with sf() as s:
        rows = (await s.execute(stmt)).all()
    return [_view(*row) for row in rows]
