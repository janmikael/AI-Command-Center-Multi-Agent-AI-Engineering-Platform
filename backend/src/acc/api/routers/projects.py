import uuid

from fastapi import APIRouter, Response, status

from acc.api.deps import EventSinkDep, OperatorDep, SessionFactoryDep
from acc.api.schemas import (
    ContextEntryCreateIn,
    ContextEntryList,
    ContextEntryOut,
    ContextEntryPatchIn,
    ProjectCreateIn,
    ProjectList,
    ProjectOut,
    RepositoryCreateIn,
)
from acc.commands import project_context as context_commands
from acc.commands import projects as project_commands
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.domain.events import user_actor
from acc.queries import project_context as context_queries
from acc.queries import projects as project_queries

router = APIRouter(prefix="/projects", tags=["projects"])


async def _project(sf: SessionFactory, slug: str) -> ProjectOut:
    found = await project_queries.list_projects(sf, slug=slug)
    if not found:
        raise NotFound(f"Project {slug} not found", project=slug)
    return ProjectOut.model_validate(found[0])


@router.get("")
async def list_projects(_: OperatorDep, sf: SessionFactoryDep) -> ProjectList:
    projects = await project_queries.list_projects(sf)
    return ProjectList(items=[ProjectOut.model_validate(p) for p in projects])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateIn,
    response: Response,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> ProjectOut:
    await project_commands.create_project(
        sf,
        sink,
        actor=user_actor(operator.id),
        slug=body.slug,
        name=body.name,
        key_prefix=body.key_prefix,
        description=body.description,
    )
    response.headers["Location"] = f"/api/v1/projects/{body.slug}"
    return await _project(sf, body.slug)


@router.get("/{slug}")
async def get_project(slug: str, _: OperatorDep, sf: SessionFactoryDep) -> ProjectOut:
    return await _project(sf, slug)


@router.post("/{slug}/repositories", status_code=status.HTTP_201_CREATED)
async def add_repository(
    slug: str,
    body: RepositoryCreateIn,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> ProjectOut:
    """Registers repository metadata only; nothing is cloned in Phase 1."""
    await project_commands.add_repository(
        sf,
        sink,
        actor=user_actor(operator.id),
        project_slug=slug,
        name=body.name,
        remote_url=body.remote_url,
        default_branch=body.default_branch,
    )
    return await _project(sf, slug)


# ─── Project context ─────────────────────────────────────────────────────
# A per-project knowledge base (architecture notes, conventions, gotchas) — not a
# place for security exceptions. Those live only in governance rules; see
# acc.api.routers.governance and ARCHITECTURE.md §10.


@router.get("/{slug}/context")
async def list_context_entries(
    slug: str, _: OperatorDep, sf: SessionFactoryDep
) -> ContextEntryList:
    entries = await context_queries.list_entries(sf, slug)
    if entries is None:
        raise NotFound(f"Project {slug} not found", project=slug)
    return ContextEntryList(items=[ContextEntryOut.model_validate(e) for e in entries])


@router.post("/{slug}/context", status_code=status.HTTP_201_CREATED)
async def create_context_entry(
    slug: str,
    body: ContextEntryCreateIn,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> ContextEntryOut:
    entry_id = await context_commands.create_entry(
        sf,
        sink,
        actor=user_actor(operator.id),
        created_by=operator.id,
        project_slug=slug,
        kind=body.kind.value,
        title=body.title,
        body=body.body,
    )
    entries = await context_queries.list_entries(sf, slug)
    assert entries is not None
    entry = next(e for e in entries if e.id == entry_id)
    return ContextEntryOut.model_validate(entry)


@router.patch("/{slug}/context/{entry_id}")
async def update_context_entry(
    slug: str,
    entry_id: uuid.UUID,
    body: ContextEntryPatchIn,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> ContextEntryOut:
    changes = body.model_dump(exclude_unset=True, mode="json")
    await context_commands.update_entry(
        sf,
        sink,
        actor=user_actor(operator.id),
        updated_by=operator.id,
        entry_id=entry_id,
        changes=changes,
    )
    entries = await context_queries.list_entries(sf, slug)
    if entries is None:
        raise NotFound(f"Project {slug} not found", project=slug)
    entry = next((e for e in entries if e.id == entry_id), None)
    if entry is None:
        raise NotFound(f"Context entry {entry_id} not found", entry_id=str(entry_id))
    return ContextEntryOut.model_validate(entry)


@router.delete("/{slug}/context/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_context_entry(
    slug: str,
    entry_id: uuid.UUID,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> None:
    await context_commands.delete_entry(
        sf, sink, actor=user_actor(operator.id), entry_id=entry_id
    )
