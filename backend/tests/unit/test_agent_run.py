"""The run state machine, checked exhaustively, plus the pure heartbeat/cost policy
checks. Mirrors tests/unit/test_task_status.py in shape."""

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from acc.domain.agent_run import (
    ACTIVE,
    RUN_TRANSITIONS,
    TERMINAL,
    RunStatus,
    allowed_transitions,
    exceeds_cost_ceiling,
    is_heartbeat_stale,
    validate_run_transition,
)
from acc.domain.errors import Conflict, InvalidTransition

S = RunStatus

EXPECTED: dict[RunStatus, set[RunStatus]] = {
    S.QUEUED: {S.STARTING, S.CANCELLED, S.FAILED},
    S.STARTING: {S.RUNNING, S.FAILED, S.CANCELLED, S.TIMED_OUT},
    S.RUNNING: {S.SUCCEEDED, S.FAILED, S.CANCELLED, S.TIMED_OUT},
    S.SUCCEEDED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
    S.TIMED_OUT: set(),
}

ALL_PAIRS = list(itertools.product(RunStatus, RunStatus))


def test_table_covers_every_status() -> None:
    assert set(RUN_TRANSITIONS) == set(RunStatus)


@pytest.mark.parametrize(("current", "target"), ALL_PAIRS, ids=lambda s: s.value)
def test_every_pair(current: RunStatus, target: RunStatus) -> None:
    if target in EXPECTED[current]:
        validate_run_transition(current, target)
    else:
        with pytest.raises(InvalidTransition) as excinfo:
            validate_run_transition(current, target)
        assert excinfo.value.details["allowed"] == [s.value for s in allowed_transitions(current)]


def test_invalid_transition_is_a_conflict() -> None:
    assert issubclass(InvalidTransition, Conflict)


@pytest.mark.parametrize("status", sorted(TERMINAL))
def test_terminal_statuses_have_no_exit(status: RunStatus) -> None:
    assert allowed_transitions(status) == []


def test_active_set() -> None:
    assert {S.QUEUED, S.STARTING, S.RUNNING} == ACTIVE


def test_every_terminal_status_reachable_from_queued() -> None:
    reachable, frontier = {S.QUEUED}, [S.QUEUED]
    while frontier:
        for nxt in RUN_TRANSITIONS[frontier.pop()]:
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    assert reachable >= TERMINAL


class TestHeartbeatStale:
    NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    TIMEOUT = timedelta(minutes=5)

    def test_no_heartbeat_yet_is_not_stale(self) -> None:
        stale = is_heartbeat_stale(last_heartbeat_at=None, now=self.NOW, timeout=self.TIMEOUT)
        assert stale is False

    def test_within_timeout_is_not_stale(self) -> None:
        assert (
            is_heartbeat_stale(
                last_heartbeat_at=self.NOW - self.TIMEOUT, now=self.NOW, timeout=self.TIMEOUT
            )
            is False
        )

    def test_past_timeout_is_stale(self) -> None:
        assert (
            is_heartbeat_stale(
                last_heartbeat_at=self.NOW - self.TIMEOUT - timedelta(seconds=1),
                now=self.NOW,
                timeout=self.TIMEOUT,
            )
            is True
        )


class TestCostCeiling:
    def test_under_ceiling(self) -> None:
        assert exceeds_cost_ceiling(cost_usd=Decimal("1.00"), ceiling_usd=Decimal("5.00")) is False

    def test_at_ceiling_exceeds(self) -> None:
        """>= , not > : a run must stop at the ceiling, not after crossing it."""
        assert exceeds_cost_ceiling(cost_usd=Decimal("5.00"), ceiling_usd=Decimal("5.00")) is True

    def test_over_ceiling(self) -> None:
        assert exceeds_cost_ceiling(cost_usd=Decimal("5.01"), ceiling_usd=Decimal("5.00")) is True
