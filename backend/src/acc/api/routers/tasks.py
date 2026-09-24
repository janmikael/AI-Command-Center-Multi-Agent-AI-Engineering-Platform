"""Task endpoints. Reads come from acc.queries; every write is one acc.commands call.

Status changes have exactly one path: POST /tasks/{key}/transition, validated by the
domain state machine. PATCH can't touch status, assignee or progress (422).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from acc.api.deps import (
    EventSinkDep,
    ExpectedVersion,
    OperatorDep,
    OptionalVersion,
    SessionFactoryDep,
    etag,
)
from acc.api.schemas import (
    AssignIn,
    CriterionIn,
    EventOut,
    EventPage,
    TaskCreateIn,
    TaskDetailOut,
    TaskList,
    TaskOut,
    TaskPatchIn,
    TransitionIn,
)
from acc.commands import tasks as task_commands
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.domain.events import user_actor
from acc.domain.task_status import TaskStatus
from acc.ids import new_id
from acc.queries import events as event_queries
from acc.queries import tasks as task_queries

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _criteria(items: list[CriterionIn]) -> list[dict[str, object]]:
    return [{"id": c.id or new_id().hex[:12], "text": c.text, "done": c.done} for c in items]


async def _detail(sf: SessionFactory, key: str, response: Response) -> TaskDetailOut:
    detail = await task_queries.get_task(sf, key)
    if detail is None:
        raise NotFound(f"Task {key} not found", key=key)
    response.headers["ETag"] = etag(detail.task.version)
    return TaskDetailOut.model_validate(detail)


@router.get("")
async def list_tasks(
    _: OperatorDep,
    sf: SessionFactoryDep,
    project: str | None = None,
    status_: Annotated[list[TaskStatus] | None, Query(alias="status")] = None,
    agent: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> TaskList:
    items = await task_queries.list_tasks(
        sf, project_slug=project, statuses=status_, agent_slug=agent, q=q
    )
    return TaskList(items=[TaskOut.model_validate(t) for t in items])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreateIn,
    response: Response,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> TaskDetailOut:
    ref = await task_commands.create_task(
        sf,
        sink,
        actor=user_actor(operator.id),
        created_by=operator.id,
        project_slug=body.project,
        title=body.title,
        description=body.description,
        priority=body.priority,
        acceptance_criteria=_criteria(body.acceptance_criteria),
        repository=body.repository,
        assignee=body.assignee,
    )
    response.headers["Location"] = f"/api/v1/tasks/{ref.key}"
    return await _detail(sf, ref.key, response)


@router.get("/{key}")
async def get_task(
    key: str, response: Response, _: OperatorDep, sf: SessionFactoryDep
) -> TaskDetailOut:
    return await _detail(sf, key, response)


@router.patch("/{key}")
async def update_task(
    key: str,
    body: TaskPatchIn,
    version: ExpectedVersion,
    response: Response,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> TaskDetailOut:
    changes = body.model_dump(exclude_unset=True)
    if body.acceptance_criteria is not None:
        changes["acceptance_criteria"] = _criteria(body.acceptance_criteria)
    await task_commands.update_task_details(
        sf, sink, actor=user_actor(operator.id), key=key, expected_version=version, changes=changes
    )
    return await _detail(sf, key, response)


@router.post("/{key}/transition")
async def transition_task(
    key: str,
    body: TransitionIn,
    version: ExpectedVersion,
    response: Response,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> TaskDetailOut:
    await task_commands.transition_task(
        sf,
        sink,
        actor=user_actor(operator.id),
        key=key,
        expected_version=version,
        to=body.to,
        reason=body.reason,
    )
    return await _detail(sf, key, response)


@router.post("/{key}/assign")
async def assign_task(
    key: str,
    body: AssignIn,
    version: OptionalVersion,
    response: Response,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> TaskDetailOut:
    await task_commands.assign_task(
        sf,
        sink,
        actor=user_actor(operator.id),
        key=key,
        agent_slug=body.agent_slug,
        expected_version=version,
    )
    return await _detail(sf, key, response)


@router.get("/{key}/events")
async def task_events(
    key: str,
    _: OperatorDep,
    sf: SessionFactoryDep,
    before_seq: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EventPage:
    if await task_queries.get_task(sf, key) is None:
        raise NotFound(f"Task {key} not found", key=key)
    items = await event_queries.list_events(sf, task_key=key, before_seq=before_seq, limit=limit)
    return EventPage(
        items=[EventOut.model_validate(e) for e in items],
        next_cursor=items[-1].seq if len(items) == limit else None,
    )
