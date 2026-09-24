import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class ToolCall(Base):
    """One row per tool invocation a run made. No `policy_decision`/`approval_id` yet —
    those belong to the MCP gateway (Phase 3); `allowed` is this run's own can_use_tool
    decision (see PLAN-PHASE-2.md §4.3)."""

    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run_id_started_at", "run_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    allowed: Mapped[bool] = mapped_column()
    denial_reason: Mapped[str | None] = mapped_column(Text)
    result_excerpt: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
