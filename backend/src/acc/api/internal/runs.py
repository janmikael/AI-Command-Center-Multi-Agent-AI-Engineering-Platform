"""The runner-facing endpoints: heartbeat, complete, fail. Thin wrappers over
acc.commands.runs (M1) — the run lifecycle state machine is unchanged (PLAN-PHASE-2-M2.md
§0.5). Authenticated by RunClaimsDep, never the operator session cookie.
"""

import uuid

from fastapi import APIRouter

from acc.api.deps import EventSinkDep, SessionFactoryDep
from acc.api.internal.deps import RunClaimsDep
from acc.api.internal.schemas import (
    CompleteIn,
    FailIn,
    FileChangesIn,
    HeartbeatIn,
    HeartbeatOut,
)
from acc.commands import runs as run_commands
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.domain.events import agent_actor
from acc.queries import runs as run_queries

router = APIRouter(prefix="/internal/runs/{run_id}", tags=["internal"])


async def _actor_for(sf: SessionFactory, run_id: uuid.UUID) -> str:
    run = await run_queries.get_run(sf, run_id)
    if run is None:
        raise NotFound(f"Run {run_id} not found", run_id=str(run_id))
    return agent_actor(run.agent_slug)


@router.post("/heartbeat")
async def heartbeat(
    claims: RunClaimsDep, body: HeartbeatIn, sf: SessionFactoryDep, sink: EventSinkDep
) -> HeartbeatOut:
    await run_commands.record_heartbeat(
        sf, sink, run_id=claims.run_id, current_step=body.current_step
    )
    run = await run_queries.get_run(sf, claims.run_id)
    cancel_requested = run is not None and run.cancel_requested_at is not None
    return HeartbeatOut(cancel_requested=cancel_requested)


@router.post("/complete", status_code=204)
async def complete(
    claims: RunClaimsDep, body: CompleteIn, sf: SessionFactoryDep, sink: EventSinkDep
) -> None:
    actor = await _actor_for(sf, claims.run_id)
    await run_commands.complete_run(
        sf, sink, actor=actor, run_id=claims.run_id, result_summary=body.result_summary
    )


@router.post("/fail", status_code=204)
async def fail(
    claims: RunClaimsDep, body: FailIn, sf: SessionFactoryDep, sink: EventSinkDep
) -> None:
    actor = await _actor_for(sf, claims.run_id)
    await run_commands.fail_run(sf, sink, actor=actor, run_id=claims.run_id, reason=body.reason)


@router.post("/file-changes", status_code=204)
async def file_changes(
    claims: RunClaimsDep, body: FileChangesIn, sf: SessionFactoryDep, sink: EventSinkDep
) -> None:
    await run_commands.record_file_changes(
        sf, sink, run_id=claims.run_id, changes=[c.model_dump() for c in body.changes]
    )
