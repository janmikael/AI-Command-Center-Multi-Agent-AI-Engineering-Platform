import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from acc.api.schemas import ApiModel

Step = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Summary = Annotated[str, StringConstraints(max_length=4000)]
FilePath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
ToolName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class HeartbeatIn(ApiModel):
    current_step: Step | None = None


class HeartbeatOut(ApiModel):
    cancel_requested: bool


class CompleteIn(ApiModel):
    result_summary: Summary


class FailIn(ApiModel):
    reason: Summary


class FileChangeIn(ApiModel):
    path: FilePath
    change_type: Literal["added", "modified", "deleted", "renamed"]
    additions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)


class FileChangesIn(ApiModel):
    changes: list[FileChangeIn] = Field(min_length=1, max_length=500)


# ─── M4: Claude Agent SDK adapter reporting ───────────────────────────────
class AgentMessageIn(ApiModel):
    text: Summary


class ToolCallIn(ApiModel):
    tool: ToolName
    arguments: dict[str, Any]
    allowed: bool
    denial_reason: Annotated[str, StringConstraints(max_length=1000)] | None = None


class UsageIn(ApiModel):
    provider: Annotated[str, StringConstraints(min_length=1, max_length=50)] = "anthropic"
    model: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0, default=0)
    cache_write_tokens: int = Field(ge=0, default=0)
    cost_usd: Decimal = Field(ge=0)


class ContextGovernanceRuleOut(ApiModel):
    """A snapshot of one enabled Global AI Rule, shaped for the runner's own can_use_tool
    gate to evaluate (PLAN-PHASE-2-M4.md §4.2) — fetched once at run start, not the
    operator-facing GovernanceRuleOut (acc/api/schemas.py), which has no `match`."""

    id: uuid.UUID
    category: str
    title: str
    statement: str
    effect: str
    match: dict[str, Any]
    applies_to: list[str]
    priority: int


class RunContextOut(ApiModel):
    """The four-layer agent context (ARCHITECTURE.md §10.2), rendered once at run start.
    system_prompt is layers 1+2 (Global AI Rules + the agent's role — stable, cacheable);
    task_prompt is layers 3+4 (project context + this task's own instructions —
    volatile, per-run). See PLAN-PHASE-2-M4.md §3."""

    system_prompt: str
    task_prompt: str
    governance_rules: list[ContextGovernanceRuleOut]
    model: str | None
    max_turns: int
    max_cost_usd: Decimal
