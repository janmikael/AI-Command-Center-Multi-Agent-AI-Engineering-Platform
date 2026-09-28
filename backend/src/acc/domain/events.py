"""Event catalogue: every event type and the exact shape of its payload.

Events are append-only facts. A command may only emit a type listed here, with a payload
that validates against its schema, so the activity feed and future consumers (audit,
replay, usage accounting) can rely on the shape.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from acc.domain.task_status import TaskStatus

# ─── Actors ──────────────────────────────────────────────────────────────
SYSTEM = "system"
SIMULATOR = "system:simulator"
ORCHESTRATOR = "system:orchestrator"


def user_actor(user_id: uuid.UUID) -> str:
    return f"user:{user_id}"


def agent_actor(slug: str) -> str:
    return f"agent:{slug}"


# ─── Types ───────────────────────────────────────────────────────────────
class EventType(StrEnum):
    PROJECT_CREATED = "project.created"
    REPOSITORY_ADDED = "repository.added"
    AGENT_REGISTERED = "agent.registered"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_ASSIGNED = "task.assigned"
    AGENT_MESSAGE = "agent.message"
    AGENT_DECISION = "agent.decision"
    GOVERNANCE_RULE_CREATED = "governance_rule.created"
    GOVERNANCE_RULE_UPDATED = "governance_rule.updated"
    GOVERNANCE_RULE_ENABLED = "governance_rule.enabled"
    GOVERNANCE_RULE_DISABLED = "governance_rule.disabled"
    PROJECT_CONTEXT_ENTRY_CREATED = "project_context_entry.created"
    PROJECT_CONTEXT_ENTRY_UPDATED = "project_context_entry.updated"
    PROJECT_CONTEXT_ENTRY_DELETED = "project_context_entry.deleted"
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_STEP = "run.step"
    RUN_HEARTBEAT_MISSED = "run.heartbeat_missed"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMED_OUT = "run.timed_out"
    TOOL_CALL_DENIED = "tool_call.denied"
    USAGE_RECORDED = "usage.recorded"
    RUN_TIMEOUT_WARNING = "run.timeout_warning"


# Events that count as an agent's *own* activity for derived status. Things done *to* an
# agent (being assigned a task, being registered) deliberately don't count.
AGENT_ACTIVITY_TYPES: frozenset[EventType] = frozenset(
    {EventType.AGENT_MESSAGE, EventType.AGENT_DECISION}
)


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


# ─── Payloads ────────────────────────────────────────────────────────────
class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectCreated(Payload):
    slug: str
    name: str
    key_prefix: str


class RepositoryAdded(Payload):
    name: str
    remote_url: str
    default_branch: str


class AgentRegistered(Payload):
    slug: str
    display_name: str
    role: str
    capabilities: list[str]


class TaskCreated(Payload):
    key: str
    title: str
    priority: int
    status: TaskStatus


class FieldChange(Payload):
    before: Any
    after: Any


class TaskUpdated(Payload):
    changes: dict[str, FieldChange] = Field(min_length=1)


class TaskStatusChanged(Payload):
    from_status: TaskStatus
    to_status: TaskStatus
    reason: str | None = None


class TaskAssigned(Payload):
    from_agent: str | None
    to_agent: str | None


class AgentMessage(Payload):
    text: str = Field(min_length=1, max_length=4000)


class AgentDecision(Payload):
    decision: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(max_length=4000)
    alternatives: list[str] = Field(default_factory=list, max_length=10)


# governance_rules has no dedicated `events` column (unlike task_id/agent_id), since it
# is a low-volume admin resource with no per-rule activity view yet — so the id travels
# in the payload instead, same principle, lighter footprint.
class GovernanceRuleCreated(Payload):
    id: uuid.UUID
    category: str
    title: str
    statement: str
    effect: str


class GovernanceRuleUpdated(Payload):
    id: uuid.UUID
    changes: dict[str, FieldChange] = Field(min_length=1)


class GovernanceRuleEnabled(Payload):
    id: uuid.UUID
    title: str


class GovernanceRuleDisabled(Payload):
    id: uuid.UUID
    title: str


class ProjectContextEntryCreated(Payload):
    id: uuid.UUID
    kind: str
    title: str


class ProjectContextEntryUpdated(Payload):
    id: uuid.UUID
    changes: dict[str, FieldChange] = Field(min_length=1)


class ProjectContextEntryDeleted(Payload):
    id: uuid.UUID
    title: str


# run_id has nowhere else to live on the wire event until §1.6's internal API and the
# events.run_id column (migration 0014) both exist, so every run payload below carries it
# explicitly — same lightweight principle as governance rules carrying their id (above).
class RunQueued(Payload):
    id: uuid.UUID
    task_key: str
    agent_slug: str


class RunStarted(Payload):
    id: uuid.UUID
    container_id: str


class RunStep(Payload):
    id: uuid.UUID
    text: str = Field(min_length=1, max_length=500)


class RunHeartbeatMissed(Payload):
    id: uuid.UUID
    last_heartbeat_at: datetime | None


class RunSucceeded(Payload):
    id: uuid.UUID
    result_summary: str = Field(max_length=4000)


class RunFailed(Payload):
    id: uuid.UUID
    reason: str = Field(max_length=4000)


class RunCancelled(Payload):
    id: uuid.UUID
    reason: str = Field(max_length=500)


class RunTimedOut(Payload):
    id: uuid.UUID


# Fires once per run (agent_runs.timeout_warning_emitted_at guards against a repeat on
# every reconcile tick), before the unchanged hard kill at 100% of the wall-clock limit
# (PLAN-PHASE-2-M4.2.md §2/§7) — observability only, not a change to enforcement.
class RunTimeoutWarning(Payload):
    id: uuid.UUID
    remaining_seconds: int


# Every allowed tool call becomes a tool_calls row (M4), not an event — same volume
# reasoning record_step's silence on heartbeats already established. Only a *denial* is
# eventful: rare, and something an operator should see live (PLAN-PHASE-2-M4.md §6).
# rule_id/effect (M4.2, PLAN-PHASE-2-M4.2.md §1.3): traceability — which rule, and
# whether it was a hard deny or (today, equivalently enforced) a require_approval match
# with nowhere yet to route the approval.
class ToolCallDenied(Payload):
    id: uuid.UUID
    tool: str
    reason: str = Field(max_length=1000)
    rule_id: uuid.UUID | None = None
    effect: str | None = None


# One per run, not per turn: query() (the one-shot SDK entry point M4 uses, see
# PLAN-PHASE-2-M4.md §2) only ever surfaces a ResultMessage — and therefore real
# usage/cost numbers — once, at the end of the session, not per turn.
class UsageRecorded(Payload):
    id: uuid.UUID
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal


PAYLOAD_SCHEMAS: dict[EventType, type[Payload]] = {
    EventType.PROJECT_CREATED: ProjectCreated,
    EventType.REPOSITORY_ADDED: RepositoryAdded,
    EventType.AGENT_REGISTERED: AgentRegistered,
    EventType.TASK_CREATED: TaskCreated,
    EventType.TASK_UPDATED: TaskUpdated,
    EventType.TASK_STATUS_CHANGED: TaskStatusChanged,
    EventType.TASK_ASSIGNED: TaskAssigned,
    EventType.AGENT_MESSAGE: AgentMessage,
    EventType.AGENT_DECISION: AgentDecision,
    EventType.GOVERNANCE_RULE_CREATED: GovernanceRuleCreated,
    EventType.GOVERNANCE_RULE_UPDATED: GovernanceRuleUpdated,
    EventType.GOVERNANCE_RULE_ENABLED: GovernanceRuleEnabled,
    EventType.GOVERNANCE_RULE_DISABLED: GovernanceRuleDisabled,
    EventType.PROJECT_CONTEXT_ENTRY_CREATED: ProjectContextEntryCreated,
    EventType.PROJECT_CONTEXT_ENTRY_UPDATED: ProjectContextEntryUpdated,
    EventType.PROJECT_CONTEXT_ENTRY_DELETED: ProjectContextEntryDeleted,
    EventType.RUN_QUEUED: RunQueued,
    EventType.RUN_STARTED: RunStarted,
    EventType.RUN_STEP: RunStep,
    EventType.RUN_HEARTBEAT_MISSED: RunHeartbeatMissed,
    EventType.RUN_SUCCEEDED: RunSucceeded,
    EventType.RUN_FAILED: RunFailed,
    EventType.RUN_CANCELLED: RunCancelled,
    EventType.RUN_TIMED_OUT: RunTimedOut,
    EventType.RUN_TIMEOUT_WARNING: RunTimeoutWarning,
    EventType.TOOL_CALL_DENIED: ToolCallDenied,
    EventType.USAGE_RECORDED: UsageRecorded,
}


def check_payload(event_type: EventType, payload: Payload) -> None:
    expected = PAYLOAD_SCHEMAS[event_type]
    if not isinstance(payload, expected):
        raise TypeError(
            f"{event_type} needs a {expected.__name__} payload, got {type(payload).__name__}"
        )


# ─── Committed event (as published and returned to clients) ─────────────
@dataclass(frozen=True)
class EventRecord:
    id: uuid.UUID
    seq: int
    ts: datetime
    type: str
    actor: str
    severity: str
    project_id: uuid.UUID | None
    task_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    run_id: uuid.UUID | None
    task_key: str | None
    agent_slug: str | None
    payload: dict[str, Any]
