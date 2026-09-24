"""The task state machine, checked exhaustively: every (from, to) pair is either explicitly
allowed or rejected with the list of legal targets."""

import itertools

import pytest

from acc.domain.errors import Conflict, InvalidTransition
from acc.domain.task_status import (
    ACTIVE,
    BOARD_COLUMNS,
    OPERATOR_TRANSITIONS,
    ORCHESTRATOR_ALLOWED,
    TERMINAL,
    TaskStatus,
    allowed_transitions,
    validate_orchestrator_transition,
    validate_transition,
)

S = TaskStatus

# The approved table (PLAN-PHASE-0-1.md §4), restated independently of the implementation.
EXPECTED: dict[TaskStatus, set[TaskStatus]] = {
    S.BACKLOG: {S.PLANNING, S.CANCELLED},
    S.PLANNING: {S.WORKING, S.BACKLOG, S.BLOCKED, S.CANCELLED},
    S.WORKING: {S.REVIEW, S.BLOCKED, S.FAILED, S.CANCELLED},
    S.REVIEW: {S.WORKING, S.COMPLETED, S.FAILED, S.CANCELLED},
    S.BLOCKED: {S.PLANNING, S.WORKING, S.CANCELLED},
    S.FAILED: {S.BACKLOG},
    S.AWAITING_APPROVAL: set(),
    S.COMPLETED: set(),
    S.CANCELLED: set(),
}

ALL_PAIRS = list(itertools.product(TaskStatus, TaskStatus))


def test_table_covers_every_status() -> None:
    assert set(OPERATOR_TRANSITIONS) == set(TaskStatus)


@pytest.mark.parametrize(("current", "target"), ALL_PAIRS, ids=lambda s: s.value)
def test_every_pair(current: TaskStatus, target: TaskStatus) -> None:
    if target in EXPECTED[current]:
        validate_transition(current, target)
    else:
        with pytest.raises(InvalidTransition) as excinfo:
            validate_transition(current, target)
        assert excinfo.value.details["allowed"] == [s.value for s in allowed_transitions(current)]


def test_invalid_transition_is_a_conflict() -> None:
    assert issubclass(InvalidTransition, Conflict)


@pytest.mark.parametrize("status", sorted(TERMINAL))
def test_terminal_statuses_have_no_exit(status: TaskStatus) -> None:
    assert allowed_transitions(status) == []


def test_awaiting_approval_unreachable_manually() -> None:
    assert all(S.AWAITING_APPROVAL not in targets for targets in OPERATOR_TRANSITIONS.values())


def test_every_board_column_reachable_from_backlog() -> None:
    reachable, frontier = {S.BACKLOG}, [S.BACKLOG]
    while frontier:
        for nxt in OPERATOR_TRANSITIONS[frontier.pop()]:
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    assert set(BOARD_COLUMNS) <= reachable


def test_allowed_transitions_are_in_stable_order() -> None:
    assert allowed_transitions(S.PLANNING) == [S.BACKLOG, S.WORKING, S.BLOCKED, S.CANCELLED]


def test_active_set() -> None:
    assert {S.PLANNING, S.WORKING, S.REVIEW} == ACTIVE


class TestOrchestratorAllowed:
    def test_is_a_subset_of_the_operator_table(self) -> None:
        for current, target in ORCHESTRATOR_ALLOWED:
            assert target in OPERATOR_TRANSITIONS[current]

    def test_review_to_completed_is_excluded(self) -> None:
        """A run finishing review is not enough to complete a task by itself
        (Guiding Principle 3) — completion stays a human, operator-only action."""
        assert (S.REVIEW, S.COMPLETED) not in ORCHESTRATOR_ALLOWED

    @pytest.mark.parametrize(
        "target", [S.REVIEW, S.FAILED, S.BLOCKED], ids=lambda s: s.value
    )
    def test_orchestrator_may_move_working_to(self, target: TaskStatus) -> None:
        validate_orchestrator_transition(S.WORKING, target)

    def test_orchestrator_may_not_move_working_to_cancelled(self) -> None:
        """Legal for the operator, but not something the orchestrator triggers on its
        own — cancellation goes through the explicit request_cancel path instead."""
        with pytest.raises(InvalidTransition) as excinfo:
            validate_orchestrator_transition(S.WORKING, S.CANCELLED)
        assert sorted(excinfo.value.details["allowed"]) == ["blocked", "failed", "review"]  # type: ignore[call-overload]

    def test_orchestrator_may_not_move_review_to_completed(self) -> None:
        with pytest.raises(InvalidTransition):
            validate_orchestrator_transition(S.REVIEW, S.COMPLETED)

    def test_still_rejects_moves_illegal_for_anyone(self) -> None:
        """An orchestrator-illegal move that's also operator-illegal still reports as a
        plain invalid transition, not the narrower orchestrator message."""
        with pytest.raises(InvalidTransition):
            validate_orchestrator_transition(S.COMPLETED, S.WORKING)
