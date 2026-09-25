"""Request/response models: the public API contract (exported to the web client via OpenAPI).

Input models forbid unknown fields, so an attempt to set `status`, `assigned_agent_id` or
`progress_pct` through a generic edit is rejected with 422 instead of silently ignored.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

from acc.domain.agent_status import AgentStatus
from acc.domain.auth import Operator, Role
from acc.domain.governance import RuleCategory, RuleEffect
from acc.domain.project_context import ContextEntryKind
from acc.domain.task_status import TaskStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutModel(BaseModel):
    """Responses are built from the query layer's dataclasses."""

    model_config = ConfigDict(from_attributes=True)


Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=20_000)]
Priority = Annotated[int, Field(ge=1, le=5, description="1 = urgent … 5 = lowest")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
AgentSlug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,39}$")]


# ─── Health ──────────────────────────────────────────────────────────────
class HealthOut(ApiModel):
    status: Literal["ok"]


class CheckOut(ApiModel):
    ok: bool
    detail: str | None = None


class ReadinessOut(ApiModel):
    status: Literal["ready", "not_ready"]
    version: str
    checks: dict[str, CheckOut]


# ─── Auth ────────────────────────────────────────────────────────────────
class LoginIn(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(ApiModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class OperatorOut(ApiModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role

    @classmethod
    def from_domain(cls, operator: Operator) -> "OperatorOut":
        return cls(
            id=operator.id,
            email=operator.email,
            display_name=operator.display_name,
            role=operator.role,
        )


# ─── Events ──────────────────────────────────────────────────────────────
class EventOut(OutModel):
    id: uuid.UUID
    seq: int
    ts: datetime
    type: str
    severity: str
    actor: str
    actor_label: str
    task_key: str | None
    task_title: str | None
    agent_slug: str | None
    agent_name: str | None
    payload: dict[str, Any]


class EventPage(ApiModel):
    items: list[EventOut]
    next_cursor: int | None = Field(
        description="Pass as before_seq to get the next (older) page; null when exhausted"
    )


# ─── Tasks ───────────────────────────────────────────────────────────────
class TaskBriefOut(OutModel):
    key: str
    title: str
    status: TaskStatus
    priority: int


class TaskOut(OutModel):
    key: str
    title: str
    status: TaskStatus
    priority: int
    progress_pct: int
    version: int
    project_slug: str
    assignee_slug: str | None
    assignee_name: str | None
    created_at: datetime
    updated_at: datetime
    allowed_transitions: list[TaskStatus] = Field(
        description="Statuses this task may move to next (the state machine, evaluated server-side)"
    )


class CriterionOut(OutModel):
    id: str
    text: str
    done: bool


class StatusChangeOut(OutModel):
    from_status: TaskStatus | None
    to_status: TaskStatus
    reason: str | None
    actor: str
    actor_label: str
    ts: datetime


class TaskDetailOut(OutModel):
    task: TaskOut
    description: str
    acceptance_criteria: list[CriterionOut]
    repository: str | None
    required_capability: str | None
    completed_at: datetime | None
    created_by: str | None
    status_history: list[StatusChangeOut]


class TaskList(ApiModel):
    items: list[TaskOut]


class CriterionIn(ApiModel):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")] | None = None
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    done: bool = False


class TaskCreateIn(ApiModel):
    project: Slug
    title: Title
    description: Description = ""
    priority: Priority = 3
    acceptance_criteria: list[CriterionIn] = Field(default_factory=list, max_length=50)
    repository: str | None = None
    assignee: AgentSlug | None = None


class TaskPatchIn(ApiModel):
    """Descriptive fields only. Status → /transition, assignee → /assign, progress → derived."""

    title: Title | None = None
    description: Description | None = None
    priority: Priority | None = None
    acceptance_criteria: list[CriterionIn] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TransitionIn(ApiModel):
    to: TaskStatus
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class AssignIn(ApiModel):
    agent_slug: AgentSlug | None = Field(description="Agent to assign; null unassigns")


# ─── Agents ──────────────────────────────────────────────────────────────
class AgentOut(OutModel):
    slug: str
    display_name: str
    role: str
    adapter: str
    enabled: bool
    capabilities: list[str]
    status: AgentStatus = Field(description="Derived: never stored, never set by an agent")
    active_task_count: int
    current_task: TaskBriefOut | None
    last_activity_at: datetime | None


class AgentList(ApiModel):
    items: list[AgentOut]


class AgentDetailOut(OutModel):
    agent: AgentOut
    open_tasks: list[TaskBriefOut]
    recent_events: list[EventOut]


# ─── Projects ────────────────────────────────────────────────────────────
class RepositoryOut(OutModel):
    name: str
    remote_url: str
    default_branch: str


class ProjectOut(OutModel):
    slug: str
    name: str
    description: str | None
    key_prefix: str
    created_at: datetime
    repositories: list[RepositoryOut]


class ProjectList(ApiModel):
    items: list[ProjectOut]


class ProjectCreateIn(ApiModel):
    slug: Slug
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    key_prefix: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]{1,9}$")]
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None


class RepositoryCreateIn(ApiModel):
    name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,100}$")]
    remote_url: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    default_branch: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._/-]{1,100}$")] = "main"


# ─── Governance ──────────────────────────────────────────────────────────
RuleTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RuleStatement = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class GovernanceRuleOut(OutModel):
    id: uuid.UUID
    category: RuleCategory
    title: str
    statement: str
    effect: RuleEffect
    applies_to: list[str]
    priority: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class GovernanceRuleList(ApiModel):
    items: list[GovernanceRuleOut]


class GovernanceRuleCreateIn(ApiModel):
    category: RuleCategory
    title: RuleTitle
    statement: RuleStatement
    effect: RuleEffect


class GovernanceRulePatchIn(ApiModel):
    """title / statement / effect only — category is fixed at creation, and
    match / applies_to / priority stay API-only for this phase (see PLAN-PHASE-1.5.md)."""

    title: RuleTitle | None = None
    statement: RuleStatement | None = None
    effect: RuleEffect | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


# ─── Project context ─────────────────────────────────────────────────────
class ContextEntryOut(OutModel):
    id: uuid.UUID
    kind: ContextEntryKind
    title: str
    body: str
    priority: int
    created_at: datetime
    updated_at: datetime


class ContextEntryList(ApiModel):
    items: list[ContextEntryOut]


class ContextEntryCreateIn(ApiModel):
    kind: ContextEntryKind
    title: RuleTitle
    body: Annotated[str, StringConstraints(max_length=20_000)] = ""
    priority: int = Field(default=100, ge=1, le=1000)


class ContextEntryPatchIn(ApiModel):
    kind: ContextEntryKind | None = None
    title: RuleTitle | None = None
    body: Annotated[str, StringConstraints(max_length=20_000)] | None = None
    priority: Annotated[int, Field(ge=1, le=1000)] | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


# ─── Runs ────────────────────────────────────────────────────────────────
class RunOut(OutModel):
    id: uuid.UUID
    task_id: uuid.UUID
    task_key: str
    agent_id: uuid.UUID
    agent_slug: str
    status: str
    provider_session_id: str | None
    container_id: str | None
    branch_name: str | None
    current_step: str | None
    cancel_requested_at: datetime | None
    queued_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    last_heartbeat_at: datetime | None
    result_summary: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal


class RunList(ApiModel):
    items: list[RunOut]


# ─── Overview ────────────────────────────────────────────────────────────
class SystemOut(ApiModel):
    database: bool
    redis: bool
    realtime_clients: int


class OverviewOut(OutModel):
    agents: list[AgentOut]
    task_counts: dict[TaskStatus, int]
    active_tasks: list[TaskOut]
    events_last_24h: int
    recent_events: list[EventOut]
    latest_seq: int
    system: SystemOut
