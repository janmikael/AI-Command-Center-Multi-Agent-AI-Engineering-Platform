import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base


class Event(Base):
    """Append-only event log, range-partitioned by month on ts.

    UPDATE/DELETE are rejected by a trigger and not granted to the runtime role (see
    migration 0006). Rows are only ever added via acc.commands (UnitOfWork.emit).
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("severity IN ('debug', 'info', 'warn', 'error')", name="severity"),
        Index("ix_events_seq", "seq"),
        Index("ix_events_task_id_ts", "task_id", "ts"),
        Index("ix_events_agent_id_ts", "agent_id", "ts"),
        Index("ix_events_project_id_ts", "project_id", "ts"),
        Index("ix_events_type_ts", "type", "ts"),
        {"postgresql_partition_by": "RANGE (ts)"},
    )
    # Fetch the DB-assigned seq via RETURNING on insert.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(primary_key=True, server_default=func.now())
    # Global, monotonic (per assignment) sequence used for realtime resume.
    seq: Mapped[int] = mapped_column(BigInteger, server_default=text("nextval('events_seq')"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    actor: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, server_default="info")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
