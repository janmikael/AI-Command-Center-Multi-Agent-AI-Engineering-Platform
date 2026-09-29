import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from acc.api.deps import OperatorDep, SessionFactoryDep
from acc.api.schemas import EventOut, EventPage
from acc.queries import events as event_queries

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def list_events(
    _: OperatorDep,
    sf: SessionFactoryDep,
    project: str | None = None,
    task: str | None = None,
    agent: str | None = None,
    run_id: Annotated[
        uuid.UUID | None, Query(description="Scope to one agent run, e.g. its agent.message events")
    ] = None,
    type_: Annotated[list[str] | None, Query(alias="type")] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    before_seq: int | None = None,
    after_seq: Annotated[
        int | None, Query(description="Catch-up mode: events after this seq, oldest first")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EventPage:
    """The activity feed: newest first, paged with before_seq (the append-only log)."""
    items = await event_queries.list_events(
        sf,
        project_slug=project,
        task_key=task,
        agent_slug=agent,
        run_id=run_id,
        types=type_,
        severities=severity,
        before_seq=before_seq,
        after_seq=after_seq,
        limit=limit,
    )
    paging_back = after_seq is None and len(items) == limit
    return EventPage(
        items=[EventOut.model_validate(e) for e in items],
        next_cursor=items[-1].seq if paging_back else None,
    )
