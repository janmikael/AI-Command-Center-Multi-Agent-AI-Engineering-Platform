import uuid

from sqlalchemy import or_, select

from acc.commands._base import EventSink, unit_of_work
from acc.db.models import Project, Repository
from acc.db.session import SessionFactory
from acc.domain.errors import Conflict, NotFound
from acc.domain.events import EventType, ProjectCreated, RepositoryAdded


async def create_project(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    slug: str,
    name: str,
    key_prefix: str,
    description: str | None = None,
) -> uuid.UUID:
    async with unit_of_work(sf, sink) as uow:
        s = uow.session
        clash = await s.scalar(
            select(Project.slug).where(or_(Project.slug == slug, Project.key_prefix == key_prefix))
        )
        if clash is not None:
            raise Conflict("A project with this slug or key prefix already exists", slug=slug)
        project = Project(slug=slug, name=name, key_prefix=key_prefix, description=description)
        s.add(project)
        await s.flush()
        await uow.emit(
            EventType.PROJECT_CREATED,
            ProjectCreated(slug=slug, name=name, key_prefix=key_prefix),
            actor=actor,
            project_id=project.id,
        )
        return project.id


async def add_repository(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    project_slug: str,
    name: str,
    remote_url: str,
    default_branch: str = "main",
) -> uuid.UUID:
    """Register repository metadata. Nothing is cloned in Phase 1."""
    async with unit_of_work(sf, sink) as uow:
        s = uow.session
        project_id = await s.scalar(select(Project.id).where(Project.slug == project_slug))
        if project_id is None:
            raise NotFound(f"Project {project_slug} not found", project=project_slug)
        exists = await s.scalar(
            select(Repository.id).where(
                Repository.project_id == project_id, Repository.name == name
            )
        )
        if exists is not None:
            raise Conflict(f"Repository {name} already exists in {project_slug}", name=name)
        repo = Repository(
            project_id=project_id,
            name=name,
            remote_url=remote_url,
            default_branch=default_branch,
            protected_branches=[default_branch],
        )
        s.add(repo)
        await s.flush()
        await uow.emit(
            EventType.REPOSITORY_ADDED,
            RepositoryAdded(name=name, remote_url=remote_url, default_branch=default_branch),
            actor=actor,
            project_id=project_id,
        )
        return repo.id
