"""Task-row mutation helpers shared between acc.commands.tasks (the operator path) and
acc.commands.runs (the orchestrator path), so both can apply a status change inside their
own already-open transaction without importing each other — see PLAN-PHASE-2.md §0.1.

Not a command module itself: nothing here opens a unit_of_work. Callers do.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acc.commands._base import UnitOfWork
from acc.db.models import Agent, Task
from acc.domain.errors import NotFound
from acc.domain.events import EventType, TaskStatusChanged
from acc.domain.task_status import TaskStatus
from acc.ids import utcnow


async def lock_task_by_id(session: AsyncSession, task_id: uuid.UUID) -> Task:
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None:
        raise NotFound(f"Task {task_id} not found", task_id=str(task_id))
    return task


async def agent_slug_for(session: AsyncSession, agent_id: uuid.UUID | None) -> str | None:
    if agent_id is None:
        return None
    slug: str | None = await session.scalar(select(Agent.slug).where(Agent.id == agent_id))
    return slug


async def apply_status_change(
    uow: UnitOfWork, task: Task, to: TaskStatus, *, actor: str, reason: str | None = None
) -> None:
    """Mutates `task`, bumps its version, and emits task.status_changed. Caller has
    already validated the transition is legal and locked the row."""
    previous = task.status
    task.status = to
    if to == TaskStatus.COMPLETED:
        task.completed_at = utcnow()
    task.version += 1
    task.updated_at = utcnow()
    await uow.emit(
        EventType.TASK_STATUS_CHANGED,
        TaskStatusChanged(from_status=previous, to_status=to, reason=reason),
        actor=actor,
        project_id=task.project_id,
        task_id=task.id,
        task_key=task.key,
        agent_id=task.assigned_agent_id,
        agent_slug=await agent_slug_for(uow.session, task.assigned_agent_id),
    )
