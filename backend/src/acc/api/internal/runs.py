"""The runner-facing endpoints: heartbeat, complete, fail. Thin wrappers over
acc.commands.runs (M1) — the run lifecycle state machine is unchanged (PLAN-PHASE-2-M2.md
§0.5). Authenticated by RunClaimsDep, never the operator session cookie.
"""

import uuid

from fastapi import APIRouter

from acc.api.deps import EventSinkDep, SessionFactoryDep, SettingsDep
from acc.api.internal.deps import RunClaimsDep
from acc.api.internal.schemas import (
    AgentMessageIn,
    CompleteIn,
    ContextGovernanceRuleOut,
    FailIn,
    FileChangesIn,
    HeartbeatIn,
    HeartbeatOut,
    RunContextOut,
    ToolCallIn,
    UsageIn,
)
from acc.commands import runs as run_commands
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.domain.events import agent_actor
from acc.queries import context as context_queries
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


@router.get("/context")
async def context(
    claims: RunClaimsDep, sf: SessionFactoryDep, settings: SettingsDep
) -> RunContextOut:
    """Fetched once, at run start, by the Claude Agent SDK adapter (runner/agent_sdk.py) —
    not passed via env vars, so a governance-rule edit made after a run's container image
    was built still reaches the very next run (PLAN-PHASE-2-M4.md §1/§3)."""
    ctx = await context_queries.get_run_context(sf, claims.run_id)
    if ctx is None:
        raise NotFound(f"Run {claims.run_id} not found", run_id=str(claims.run_id))
    return RunContextOut(
        system_prompt=context_queries.render_system_prompt(ctx),
        task_prompt=context_queries.render_task_prompt(ctx),
        governance_rules=[
            ContextGovernanceRuleOut(
                id=rule.id,
                category=rule.category,
                title=rule.title,
                statement=rule.statement,
                effect=rule.effect,
                match=rule.match,
                applies_to=rule.applies_to,
                priority=rule.priority,
            )
            for rule in ctx.governance_rules
        ],
        model=ctx.agent_model,
        max_turns=settings.run_max_turns,
        max_cost_usd=settings.run_max_cost_usd,
    )


@router.post("/messages", status_code=204)
async def message(
    claims: RunClaimsDep, body: AgentMessageIn, sf: SessionFactoryDep, sink: EventSinkDep
) -> None:
    actor = await _actor_for(sf, claims.run_id)
    await run_commands.record_agent_message(
        sf, sink, actor=actor, run_id=claims.run_id, text=body.text
    )


@router.post("/tool-calls", status_code=204)
async def tool_call(
    claims: RunClaimsDep, body: ToolCallIn, sf: SessionFactoryDep, sink: EventSinkDep
) -> None:
    actor = await _actor_for(sf, claims.run_id)
    await run_commands.record_tool_call(
        sf,
        sink,
        actor=actor,
        run_id=claims.run_id,
        tool=body.tool,
        arguments=body.arguments,
        allowed=body.allowed,
        denial_reason=body.denial_reason,
    )


@router.post("/usage", status_code=204)
async def usage(
    claims: RunClaimsDep, body: UsageIn, sf: SessionFactoryDep, sink: EventSinkDep
) -> None:
    actor = await _actor_for(sf, claims.run_id)
    await run_commands.record_usage(
        sf,
        sink,
        actor=actor,
        run_id=claims.run_id,
        provider=body.provider,
        model=body.model,
        input_tokens=body.input_tokens,
        output_tokens=body.output_tokens,
        cache_read_tokens=body.cache_read_tokens,
        cache_write_tokens=body.cache_write_tokens,
        cost_usd=body.cost_usd,
    )
