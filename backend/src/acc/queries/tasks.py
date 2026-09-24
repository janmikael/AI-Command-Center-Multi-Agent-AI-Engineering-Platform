import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select

from acc.db.models import Agent, Event, Project, Repository, Task, User
from acc.db.session import SessionFactory
from acc.domain.events import EventType
from acc.domain.task_status import TaskStatus, allowed_transitions
from acc.queries.events import base_query, to_views

MAX_TASKS = 500


@dataclass(frozen=True)
class TaskSummary:
    id: uuid.UUID
    key: str
    title: str
    status: TaskStatus
    priority: int
    progress_pct: int
    version: int
    project_slug: str
    assignee_slug: str | None
    assignee_name: str | None
    created_at: datetime
    updated_at: datetime
    allowed_transitions: list[TaskStatus]


@dataclass(frozen=True)
class StatusChange:
    from_status: TaskStatus | None  # None for the creation entry
    to_status: TaskStatus
    reason: str | None
    actor: str
    actor_label: str
    ts: datetime


@dataclass(frozen=True)
class TaskDetail:
    task: TaskSummary
    description: str
    acceptance_criteria: list[dict[str, Any]]
    repository: str | None
    required_capability: str | None
    completed_at: datetime | None
    created_by: str | None
    status_history: list[StatusChange]


def _summary_query() -> Any:
    return (
        select(Task, Project.slug, Agent.slug, Agent.display_name)
        .join(Project, Project.id == Task.project_id)
        .outerjoin(Agent, Agent.id == Task.assigned_agent_id)
    )


def _summary(
    task: Task, project_slug: str, agent_slug: str | None, agent_name: str | None
) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        key=task.key,
        title=task.title,
        status=task.status,
        priority=task.priority,
        progress_pct=task.progress_pct,
        version=task.version,
        project_slug=project_slug,
        assignee_slug=agent_slug,
        assignee_name=agent_name,
        created_at=task.created_at,
        updated_at=task.updated_at,
        allowed_transitions=allowed_transitions(task.status),
    )


async def list_tasks(
    sf: SessionFactory,
    *,
    project_slug: str | None = None,
    statuses: Sequence[TaskStatus] | None = None,
    agent_slug: str | None = None,
    q: str | None = None,
) -> list[TaskSummary]:
    """All matching tasks (a board-sized result; capped at MAX_TASKS), most urgent first."""
    stmt = _summary_query()
    if project_slug:
        stmt = stmt.where(Project.slug == project_slug)
    if statuses:
        stmt = stmt.where(Task.status.in_(statuses))
    if agent_slug:
        stmt = stmt.where(Agent.slug == agent_slug)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Task.title.ilike(pattern), Task.key.ilike(pattern)))
    stmt = stmt.order_by(Task.priority, Task.updated_at.desc()).limit(MAX_TASKS)
    async with sf() as s:
        rows = (await s.execute(stmt)).all()
    return [_summary(*row) for row in rows]


async def get_task(sf: SessionFactory, key: str) -> TaskDetail | None:
    async with sf() as s:
        row = (await s.execute(_summary_query().where(Task.key == key))).one_or_none()
        if row is None:
            return None
        task: Task = row[0]
        repository = (
            await s.scalar(select(Repository.name).where(Repository.id == task.repository_id))
            if task.repository_id
            else None
        )
        created_by = (
            await s.scalar(select(User.display_name).where(User.id == task.created_by))
            if task.created_by
            else None
        )
        history_rows = (
            await s.execute(
                base_query()
                .where(
                    Event.task_id == task.id,
                    Event.type.in_(
                        [EventType.TASK_CREATED.value, EventType.TASK_STATUS_CHANGED.value]
                    ),
                )
                .order_by(Event.seq)
            )
        ).all()

    history = []
    for view in to_views(history_rows):
        created = view.type == EventType.TASK_CREATED.value
        history.append(
            StatusChange(
                from_status=None if created else TaskStatus(view.payload["from_status"]),
                to_status=TaskStatus(view.payload["status" if created else "to_status"]),
                reason=None if created else view.payload.get("reason"),
                actor=view.actor,
                actor_label=view.actor_label,
                ts=view.ts,
            )
        )
    return TaskDetail(
        task=_summary(*row),
        description=task.description,
        acceptance_criteria=task.acceptance_criteria,
        repository=repository,
        required_capability=task.required_capability,
        completed_at=task.completed_at,
        created_by=created_by,
        status_history=history,
    )
