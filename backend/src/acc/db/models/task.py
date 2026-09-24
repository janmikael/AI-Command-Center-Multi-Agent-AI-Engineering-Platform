import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, SmallInteger, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.domain.task_status import TaskStatus
from acc.ids import new_id

TASK_STATUS_ENUM = Enum(
    TaskStatus,
    name="task_status",
    values_callable=lambda statuses: [s.value for s in statuses],
    validate_strings=True,
)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title_length"),
        CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_range"),
        Index("ix_tasks_project_id_status", "project_id", "status"),
        Index("ix_tasks_parent_id", "parent_id"),
        Index(
            "ix_tasks_assigned_agent_id_active",
            "assigned_agent_id",
            postgresql_where=text("status NOT IN ('completed', 'cancelled', 'failed')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    repository_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("repositories.id"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    key: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    acceptance_criteria: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    status: Mapped[TaskStatus] = mapped_column(TASK_STATUS_ENUM, server_default="backlog")
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("3"))
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    required_capability: Mapped[str | None] = mapped_column(Text)
    # Derived by the orchestrator from subtasks/criteria (Phase 4). No API writes it.
    progress_pct: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None]
    # Optimistic locking: every mutation bumps it; clients send it back in If-Match.
    version: Mapped[int] = mapped_column(server_default=text("0"))
