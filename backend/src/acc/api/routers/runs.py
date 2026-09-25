"""Operator-facing run endpoints. Reads are thin wrappers over acc.queries.runs (M1);
the only write is /cancel, which records the request and lets the orchestrator's
launcher (M2) confirm the container is actually stopped before the run is marked
cancelled — see acc.commands.runs.request_cancel.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from acc.api.deps import EventSinkDep, OperatorDep, SessionFactoryDep
from acc.api.schemas import RunList, RunOut
from acc.commands import runs as run_commands
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.queries import runs as run_queries

router = APIRouter(prefix="/runs", tags=["runs"])


async def _run(sf: SessionFactory, run_id: uuid.UUID) -> RunOut:
    run = await run_queries.get_run(sf, run_id)
    if run is None:
        raise NotFound(f"Run {run_id} not found", run_id=str(run_id))
    return RunOut.model_validate(run)


@router.get("")
async def list_runs(
    _: OperatorDep,
    sf: SessionFactoryDep,
    task_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
) -> RunList:
    runs = await run_queries.list_runs(sf, task_id=task_id, agent_id=agent_id, status=status_)
    return RunList(items=[RunOut.model_validate(r) for r in runs])


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, _: OperatorDep, sf: SessionFactoryDep) -> RunOut:
    return await _run(sf, run_id)


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(
    run_id: uuid.UUID, _: OperatorDep, sf: SessionFactoryDep, sink: EventSinkDep
) -> RunOut:
    await run_commands.request_cancel(sf, sink, run_id=run_id)
    return await _run(sf, run_id)
