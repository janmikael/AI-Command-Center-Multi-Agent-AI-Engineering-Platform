import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id

RUN_STATUSES = "('queued','starting','running','succeeded','failed','cancelled','timed_out')"
ACTIVE_RUN_STATUSES = "('queued','starting','running')"


class AgentRun(Base):
    """One agent working one task, end to end (Phase 2 has no stages — see agent_run.py).

    Only acc.commands.runs writes this table; only the orchestrator ever transitions it.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="status"),
        Index("ix_agent_runs_agent_id_status", "agent_id", "status"),
        Index("ix_agent_runs_task_id_queued_at", "task_id", "queued_at"),
        Index(
            "ix_agent_runs_one_active_per_task",
            "task_id",
            unique=True,
            postgresql_where=text(f"status IN {ACTIVE_RUN_STATUSES}"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    provider_session_id: Mapped[str | None] = mapped_column(Text)
    container_id: Mapped[str | None] = mapped_column(Text)
    workspace_ref: Mapped[str | None] = mapped_column(Text)
    branch_name: Mapped[str | None] = mapped_column(Text)
    current_step: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None]
    queued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    last_heartbeat_at: Mapped[datetime | None]
    result_summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cost_usd: Mapped[Any] = mapped_column(Numeric(12, 6), server_default=text("0"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
