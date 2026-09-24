import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class FileChange(Base):
    __tablename__ = "file_changes"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('added', 'modified', 'deleted', 'renamed')", name="change_type"
        ),
        Index("ix_file_changes_run_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    path: Mapped[str] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(Text)
    additions: Mapped[int | None] = mapped_column(Integer)
    deletions: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
