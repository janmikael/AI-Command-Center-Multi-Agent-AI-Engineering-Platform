import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class ToolCall(Base):
    """One row per tool invocation a run made. No `approval_id` yet — that belongs to
    the MCP gateway (Phase 3); `allowed` is this run's own can_use_tool decision (see
    PLAN-PHASE-2.md §4.3). `blocked_by_effect` is approval-state *preparation* only
    (PLAN-PHASE-2-M4.2.md §3) — a queryable "deny" vs "require_approval" distinction,
    not an approval mechanism; a require_approval match still denies today, same as deny.
    """

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_run_id_started_at", "run_id", "started_at"),
        Index("ix_tool_calls_run_id_tool_use_id", "run_id", "tool_use_id"),
        CheckConstraint(
            "blocked_by_effect IN ('deny', 'require_approval')",
            name="ck_tool_calls_blocked_by_effect",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    allowed: Mapped[bool] = mapped_column()
    denial_reason: Mapped[str | None] = mapped_column(Text)
    # Correlates this row (written by can_use_tool, pre-execution) with its completion
    # report (the PostToolUse hook, post-execution) — null for a denied call, which
    # never executes (PLAN-PHASE-2-M4.2.md §1.2).
    tool_use_id: Mapped[str | None] = mapped_column(Text)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("governance_rules.id", ondelete="SET NULL")
    )
    blocked_by_effect: Mapped[str | None] = mapped_column(Text)
    result_excerpt: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
