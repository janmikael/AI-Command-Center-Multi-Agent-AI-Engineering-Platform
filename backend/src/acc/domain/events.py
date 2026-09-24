"""Event catalogue: every event type and the exact shape of its payload.

Events are append-only facts. A command may only emit a type listed here, with a payload
that validates against its schema, so the activity feed and future consumers (audit,
replay, usage accounting) can rely on the shape.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from acc.domain.task_status import TaskStatus

# ─── Actors ──────────────────────────────────────────────────────────────
SYSTEM = "system"
SIMULATOR = "system:simulator"


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
    task_key: str | None
    agent_slug: str | None
    payload: dict[str, Any]
