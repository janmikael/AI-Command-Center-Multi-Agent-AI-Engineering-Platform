"""The task state machine.

Every status change in the system goes through validate_transition(). Phase 1 has one
actor kind (the operator, working the board manually). From Phase 2 the orchestrator
gets its own transition table; agents never change status themselves.
"""

from enum import StrEnum

from acc.domain.errors import InvalidTransition


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    PLANNING = "planning"
    WORKING = "working"
    REVIEW = "review"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# No way out of these.
TERMINAL: frozenset[TaskStatus] = frozenset({TaskStatus.COMPLETED, TaskStatus.CANCELLED})

# Statuses that mean "someone is actively on it" (drives agent status and the overview).
ACTIVE: frozenset[TaskStatus] = frozenset(
    {TaskStatus.PLANNING, TaskStatus.WORKING, TaskStatus.REVIEW}
)

# Kanban columns, in order. Everything else goes in the "Other" lane.
BOARD_COLUMNS: tuple[TaskStatus, ...] = (
    TaskStatus.BACKLOG,
    TaskStatus.PLANNING,
    TaskStatus.WORKING,
    TaskStatus.REVIEW,
    TaskStatus.COMPLETED,
)

_S = TaskStatus

# Manual (operator) transitions, as approved in PLAN-PHASE-0-1.md §4.
# AWAITING_APPROVAL is deliberately unreachable until the approval system (Phase 3).
OPERATOR_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    _S.BACKLOG: frozenset({_S.PLANNING, _S.CANCELLED}),
    _S.PLANNING: frozenset({_S.WORKING, _S.BACKLOG, _S.BLOCKED, _S.CANCELLED}),
    _S.WORKING: frozenset({_S.REVIEW, _S.BLOCKED, _S.FAILED, _S.CANCELLED}),
    _S.REVIEW: frozenset({_S.WORKING, _S.COMPLETED, _S.FAILED, _S.CANCELLED}),
    _S.BLOCKED: frozenset({_S.PLANNING, _S.WORKING, _S.CANCELLED}),
    _S.FAILED: frozenset({_S.BACKLOG}),
    _S.AWAITING_APPROVAL: frozenset(),
    _S.COMPLETED: frozenset(),
    _S.CANCELLED: frozenset(),
}


def allowed_transitions(current: TaskStatus) -> list[TaskStatus]:
    """Legal targets from `current`, in board order (stable for API responses and the UI)."""
    order = list(TaskStatus)
    return sorted(OPERATOR_TRANSITIONS[current], key=order.index)


def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target == current:
        raise InvalidTransition(
            f"Task is already {current.value}",
            current=current.value,
            allowed=[s.value for s in allowed_transitions(current)],
        )
    if target not in OPERATOR_TRANSITIONS[current]:
        raise InvalidTransition(
            f"Cannot move a task from {current.value} to {target.value}",
            current=current.value,
            target=target.value,
            allowed=[s.value for s in allowed_transitions(current)],
        )


# Phase 2: which already-legal moves (see OPERATOR_TRANSITIONS above) the orchestrator,
# as an actor, may trigger automatically when a run finishes. This is an authorization
# subset, not a new set of state transitions — validate_transition() is unchanged and
# still the only thing that decides legality. review→completed is deliberately excluded:
# a run finishing review is not enough to complete a task by itself (Guiding Principle 3,
# "agents propose, the platform executes... after human approval").
ORCHESTRATOR_ALLOWED: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (_S.WORKING, _S.REVIEW),  # run succeeded
        (_S.WORKING, _S.FAILED),  # run failed
        (_S.WORKING, _S.BLOCKED),  # run cancelled or timed out; operator decides next
    }
)


def validate_orchestrator_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Like validate_transition, but also enforces the narrower ORCHESTRATOR_ALLOWED set.

    Used only by acc.commands.runs; the operator-facing transition endpoint always uses
    validate_transition directly and is unaffected by this restriction.
    """
    validate_transition(current, target)
    if (current, target) not in ORCHESTRATOR_ALLOWED:
        raise InvalidTransition(
            f"Orchestrator is not allowed to move a task from {current.value} to {target.value}",
            current=current.value,
            target=target.value,
            allowed=[t.value for (f, t) in ORCHESTRATOR_ALLOWED if f == current],
        )
