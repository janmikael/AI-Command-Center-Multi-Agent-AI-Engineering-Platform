"""Project context entries: the only write path. A per-project knowledge base — see
acc.domain.project_context and ARCHITECTURE.md §10.1.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acc.commands._base import EventSink, unit_of_work
from acc.db.models import Project, ProjectContextEntry
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.domain.events import (
    EventType,
    FieldChange,
    ProjectContextEntryCreated,
    ProjectContextEntryDeleted,
    ProjectContextEntryUpdated,
)

EDITABLE_FIELDS = frozenset({"kind", "title", "body", "priority"})


async def _lock_entry(session: AsyncSession, entry_id: uuid.UUID) -> ProjectContextEntry:
    entry = await session.scalar(
        select(ProjectContextEntry).where(ProjectContextEntry.id == entry_id).with_for_update()
    )
    if entry is None:
        raise NotFound(f"Context entry {entry_id} not found", entry_id=str(entry_id))
    return entry


async def create_entry(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    created_by: uuid.UUID | None,
    project_slug: str,
    kind: str,
    title: str,
    body: str,
) -> uuid.UUID:
    async with unit_of_work(sf, sink) as uow:
        project_id = await uow.session.scalar(
            select(Project.id).where(Project.slug == project_slug)
        )
        if project_id is None:
            raise NotFound(f"Project {project_slug} not found", project=project_slug)
        entry = ProjectContextEntry(
            project_id=project_id, kind=kind, title=title, body=body, created_by=created_by
        )
        uow.session.add(entry)
        await uow.session.flush()
        await uow.emit(
            EventType.PROJECT_CONTEXT_ENTRY_CREATED,
            ProjectContextEntryCreated(id=entry.id, kind=kind, title=title),
            actor=actor,
            project_id=project_id,
        )
        return entry.id


async def update_entry(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    updated_by: uuid.UUID | None,
    entry_id: uuid.UUID,
    changes: dict[str, Any],
) -> None:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable through update_entry: {sorted(unknown)}")

    async with unit_of_work(sf, sink) as uow:
        entry = await _lock_entry(uow.session, entry_id)
        diff = {
            field: FieldChange(before=getattr(entry, field), after=value)
            for field, value in changes.items()
            if getattr(entry, field) != value
        }
        if not diff:
            return  # no-op edit, no event
        for field, change in diff.items():
            setattr(entry, field, change.after)
        entry.updated_by = updated_by
        await uow.emit(
            EventType.PROJECT_CONTEXT_ENTRY_UPDATED,
            ProjectContextEntryUpdated(id=entry.id, changes=diff),
            actor=actor,
            project_id=entry.project_id,
        )


async def delete_entry(
    sf: SessionFactory, sink: EventSink | None, *, actor: str, entry_id: uuid.UUID
) -> None:
    async with unit_of_work(sf, sink) as uow:
        entry = await _lock_entry(uow.session, entry_id)
        project_id, title = entry.project_id, entry.title
        await uow.session.delete(entry)
        # Recorded before the row is gone — the event is the durable trace, per the
        # append-only principle; the row itself doesn't need to survive to be auditable.
        await uow.emit(
            EventType.PROJECT_CONTEXT_ENTRY_DELETED,
            ProjectContextEntryDeleted(id=entry_id, title=title),
            actor=actor,
            project_id=project_id,
        )
