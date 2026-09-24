from fastapi import APIRouter, Response, status

from acc.api.deps import EventSinkDep, OperatorDep, SessionFactoryDep
from acc.api.schemas import ProjectCreateIn, ProjectList, ProjectOut, RepositoryCreateIn
from acc.commands import projects as project_commands
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.domain.events import user_actor
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
