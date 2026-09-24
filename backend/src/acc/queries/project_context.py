import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from acc.db.models import Project, ProjectContextEntry
from acc.db.session import SessionFactory


@dataclass(frozen=True)
class ContextEntryView:
    id: uuid.UUID
    kind: str
    title: str
    body: str
    priority: int
    created_at: datetime
    updated_at: datetime


async def list_entries(sf: SessionFactory, project_slug: str) -> list[ContextEntryView] | None:
    """None means the project itself doesn't exist (caller returns 404); an empty list
    means the project exists but has no context entries yet."""
    async with sf() as s:
        project_id = await s.scalar(select(Project.id).where(Project.slug == project_slug))
        if project_id is None:
            return None
        entries = (
            await s.scalars(
                select(ProjectContextEntry)
                .where(ProjectContextEntry.project_id == project_id)
                .order_by(ProjectContextEntry.priority, ProjectContextEntry.title)
            )
        ).all()
    return [
        ContextEntryView(
            id=e.id,
            kind=e.kind,
            title=e.title,
            body=e.body,
            priority=e.priority,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )
        for e in entries
    ]
