"""The agent run state machine, plus the pure policy checks around it (heartbeat
staleness, cost ceiling). Mirrors acc.domain.task_status in shape.

A run is one agent working one task, end to end (Phase 2 has no stages/workflow yet —
see PLAN-PHASE-2.md §0.1). Only the orchestrator ever transitions a run; there is no
operator-facing "set run status" endpoint, only /runs/{id}/cancel, which requests a
cancellation the run/watchdog then carries out.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from acc.domain.errors import InvalidTransition


class RunStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL: frozenset[RunStatus] = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)

# Counts as "the agent has a run in flight" for agent_status derivation and for the
# one-active-run-per-task rule (mirrored by a partial unique index in migration 0009).
ACTIVE: frozenset[RunStatus] = frozenset({RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING})

_S = RunStatus

# The orchestrator is the only actor; there is no separate "operator" table here the way
# task_status has one, because no human ever sets a run's status directly.
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    _S.QUEUED: frozenset({_S.STARTING, _S.CANCELLED, _S.FAILED}),
    _S.STARTING: frozenset({_S.RUNNING, _S.FAILED, _S.CANCELLED, _S.TIMED_OUT}),
    _S.RUNNING: frozenset({_S.SUCCEEDED, _S.FAILED, _S.CANCELLED, _S.TIMED_OUT}),
    _S.SUCCEEDED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELLED: frozenset(),
    _S.TIMED_OUT: frozenset(),
}


def allowed_transitions(current: RunStatus) -> list[RunStatus]:
    order = list(RunStatus)
    return sorted(RUN_TRANSITIONS[current], key=order.index)


def validate_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target == current:
        raise InvalidTransition(
            f"Run is already {current.value}",
            current=current.value,
            allowed=[s.value for s in allowed_transitions(current)],
        )
    if target not in RUN_TRANSITIONS[current]:
        raise InvalidTransition(
            f"Cannot move a run from {current.value} to {target.value}",
            current=current.value,
            target=target.value,
            allowed=[s.value for s in allowed_transitions(current)],
        )


def is_heartbeat_stale(
    *, last_heartbeat_at: datetime | None, now: datetime, timeout: timedelta
) -> bool:
    """True once a running agent has gone quiet for longer than `timeout`.

    A run with no heartbeat yet (still starting) is never considered stale by this check
    alone — the orchestrator's separate wall-clock timeout on `started_at` covers that.
    """
    if last_heartbeat_at is None:
        return False
    return now - last_heartbeat_at > timeout


def exceeds_cost_ceiling(*, cost_usd: Decimal, ceiling_usd: Decimal) -> bool:
    return cost_usd >= ceiling_usd
