import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from acc.db.models import Project, Repository
from acc.db.session import SessionFactory


@dataclass(frozen=True)
class RepositoryView:
    name: str
    remote_url: str
    default_branch: str


@dataclass(frozen=True)
class ProjectView:
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    key_prefix: str
    created_at: datetime
    repositories: list[RepositoryView]


async def list_projects(sf: SessionFactory, *, slug: str | None = None) -> list[ProjectView]:
    stmt = select(Project).where(Project.archived_at.is_(None)).order_by(Project.created_at)
    if slug is not None:
        stmt = stmt.where(Project.slug == slug)
    async with sf() as s:
        projects = (await s.scalars(stmt)).all()
        repos = (
            await s.scalars(
                select(Repository)
                .where(Repository.project_id.in_([p.id for p in projects]))
                .order_by(Repository.name)
            )
        ).all()
    by_project: dict[uuid.UUID, list[RepositoryView]] = {p.id: [] for p in projects}
    for r in repos:
        by_project[r.project_id].append(RepositoryView(r.name, r.remote_url, r.default_branch))
    return [
        ProjectView(
            id=p.id,
            slug=p.slug,
            name=p.name,
            description=p.description,
            key_prefix=p.key_prefix,
            created_at=p.created_at,
            repositories=by_project[p.id],
        )
        for p in projects
    ]
