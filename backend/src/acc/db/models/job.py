import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id

JOB_KINDS = "('start_run', 'cancel_run', 'timeout_check')"


class Job(Base):
    """The durable job queue (ADR-2): status changes and job enqueues commit in one
    transaction, so "task says working but no job exists" can't happen. Claimed with
    SELECT ... FOR UPDATE SKIP LOCKED by acc.orchestrator.scheduler."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(f"kind IN {JOB_KINDS}", name="kind"),
        CheckConstraint("status IN ('pending', 'running', 'done', 'dead')", name="status"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
