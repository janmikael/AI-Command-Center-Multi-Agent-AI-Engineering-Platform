"""Task commands. The only code that changes a task.

Every mutation:
  * locks the row (SELECT … FOR UPDATE) and checks the caller's expected version,
  * validates against the domain rules (state machine, assignability),
  * bumps `version`, and
  * emits exactly the events that describe the change, in the same transaction.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from acc.commands._base import EventSink, UnitOfWork, unit_of_work
from acc.db.models import Agent, Project, Repository, Task
from acc.db.session import SessionFactory
from acc.domain.errors import Conflict, NotFound, StaleVersion
from acc.domain.events import (
    EventType,
    FieldChange,
    TaskAssigned,
    TaskCreated,
    TaskStatusChanged,
    TaskUpdated,
)
from acc.domain.task_status import TERMINAL, TaskStatus, validate_transition
from acc.ids import utcnow

EDITABLE_FIELDS = frozenset({"title", "description", "priority", "acceptance_criteria"})


@dataclass(frozen=True)
class TaskRef:
    id: uuid.UUID
    key: str
    version: int


async def _lock_task(session: AsyncSession, key: str) -> Task:
    task = await session.scalar(select(Task).where(Task.key == key).with_for_update())
    if task is None:
        raise NotFound(f"Task {key} not found", key=key)
    return task


def _check_version(task: Task, expected_version: int | None) -> None:
    if expected_version is not None and task.version != expected_version:
        raise StaleVersion(
            f"Task {task.key} was changed by someone else. Reload and try again.",
            key=task.key,
            current_version=task.version,
        )


async def _enabled_agent(session: AsyncSession, slug: str) -> Agent:
    agent = await session.scalar(select(Agent).where(Agent.slug == slug))
    if agent is None:
        raise NotFound(f"Agent {slug} not found", agent=slug)
    if not agent.enabled:
        raise Conflict(f"Agent {slug} is disabled", agent=slug)
    return agent


async def _agent_slug(session: AsyncSession, agent_id: uuid.UUID | None) -> str | None:
    if agent_id is None:
        return None
    slug: str | None = await session.scalar(select(Agent.slug).where(Agent.id == agent_id))
    return slug


def _touch(task: Task) -> None:
    task.version += 1
    task.updated_at = utcnow()


async def _emit_task(
    uow: UnitOfWork,
    task: Task,
    event_type: EventType,
    payload: Any,
    *,
    actor: str,
    agent_id: uuid.UUID | None = None,
    agent_slug: str | None = None,
) -> None:
    await uow.emit(
        event_type,
        payload,
        actor=actor,
        project_id=task.project_id,
        task_id=task.id,
        task_key=task.key,
        agent_id=agent_id,
        agent_slug=agent_slug,
    )


async def create_task(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    created_by: uuid.UUID | None,
    project_slug: str,
    title: str,
    description: str = "",
    priority: int = 3,
    acceptance_criteria: Sequence[Mapping[str, Any]] = (),
    repository: str | None = None,
    required_capability: str | None = None,
    assignee: str | None = None,
) -> TaskRef:
    async with unit_of_work(sf, sink) as uow:
        s = uow.session
        # Atomic key allocation: concurrent creates can never get the same number.
        row = (
            await s.execute(
                update(Project)
                .where(Project.slug == project_slug, Project.archived_at.is_(None))
                .values(next_task_number=Project.next_task_number + 1)
                .returning(Project.id, Project.key_prefix, Project.next_task_number)
            )
        ).one_or_none()
        if row is None:
            raise NotFound(f"Project {project_slug} not found", project=project_slug)
        project_id, key_prefix, next_number = row
        key = f"{key_prefix}-{next_number - 1}"

        repository_id = None
        if repository is not None:
            repository_id = await s.scalar(
                select(Repository.id).where(
                    Repository.project_id == project_id, Repository.name == repository
                )
            )
            if repository_id is None:
                raise NotFound(f"Repository {repository} not found", repository=repository)

        agent = await _enabled_agent(s, assignee) if assignee else None
        now = utcnow()
        task = Task(
            project_id=project_id,
            repository_id=repository_id,
            key=key,
            title=title,
            description=description,
            priority=priority,
            acceptance_criteria=[dict(c) for c in acceptance_criteria],
            status=TaskStatus.BACKLOG,
            required_capability=required_capability,
            assigned_agent_id=agent.id if agent else None,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            version=0,
        )
        s.add(task)
        await s.flush()

        await _emit_task(
            uow,
            task,
            EventType.TASK_CREATED,
            TaskCreated(key=key, title=title, priority=priority, status=TaskStatus.BACKLOG),
            actor=actor,
        )
        if agent is not None:
            await _emit_task(
                uow,
                task,
                EventType.TASK_ASSIGNED,
                TaskAssigned(from_agent=None, to_agent=agent.slug),
                actor=actor,
                agent_id=agent.id,
                agent_slug=agent.slug,
            )
        return TaskRef(id=task.id, key=key, version=task.version)


async def update_task_details(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    key: str,
    expected_version: int,
    changes: Mapping[str, Any],
) -> TaskRef:
    """Edit descriptive fields only. Status, assignee and progress have their own paths."""
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable through update_task_details: {sorted(unknown)}")

    async with unit_of_work(sf, sink) as uow:
        task = await _lock_task(uow.session, key)
        _check_version(task, expected_version)

        diff = {
            field: FieldChange(before=getattr(task, field), after=value)
            for field, value in changes.items()
            if getattr(task, field) != value
        }
        if not diff:
            return TaskRef(id=task.id, key=task.key, version=task.version)  # no-op, no event

        for field, change in diff.items():
            setattr(task, field, change.after)
        _touch(task)
        await _emit_task(uow, task, EventType.TASK_UPDATED, TaskUpdated(changes=diff), actor=actor)
        return TaskRef(id=task.id, key=task.key, version=task.version)


async def transition_task(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    key: str,
    expected_version: int,
    to: TaskStatus,
    reason: str | None = None,
) -> TaskRef:
    """The single path for status changes. Validated by the domain state machine."""
    async with unit_of_work(sf, sink) as uow:
        task = await _lock_task(uow.session, key)
        _check_version(task, expected_version)
        previous = task.status
        validate_transition(previous, to)

        task.status = to
        if to == TaskStatus.COMPLETED:
            task.completed_at = utcnow()
        _touch(task)
        await _emit_task(
            uow,
            task,
            EventType.TASK_STATUS_CHANGED,
            TaskStatusChanged(from_status=previous, to_status=to, reason=reason or None),
            actor=actor,
            agent_id=task.assigned_agent_id,
            agent_slug=await _agent_slug(uow.session, task.assigned_agent_id),
        )
        return TaskRef(id=task.id, key=task.key, version=task.version)


async def assign_task(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    key: str,
    agent_slug: str | None,
    expected_version: int | None = None,
) -> TaskRef:
    """Assign to an enabled agent, or unassign with agent_slug=None."""
    async with unit_of_work(sf, sink) as uow:
        s = uow.session
        task = await _lock_task(s, key)
        _check_version(task, expected_version)
        if task.status in TERMINAL:
            raise Conflict(f"Task {key} is {task.status.value}; it can't be reassigned", key=key)

        agent = await _enabled_agent(s, agent_slug) if agent_slug else None
        new_id = agent.id if agent else None
        if new_id == task.assigned_agent_id:
            return TaskRef(id=task.id, key=task.key, version=task.version)  # no-op, no event

        previous_id = task.assigned_agent_id
        previous_slug = await _agent_slug(s, previous_id)
        task.assigned_agent_id = new_id
        _touch(task)
        await _emit_task(
            uow,
            task,
            EventType.TASK_ASSIGNED,
            TaskAssigned(from_agent=previous_slug, to_agent=agent_slug),
            actor=actor,
            # Route to the agent gaining the task, or the one losing it on unassign.
            agent_id=new_id or previous_id,
            agent_slug=agent_slug or previous_slug,
        )
        return TaskRef(id=task.id, key=task.key, version=task.version)
