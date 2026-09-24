import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("key_prefix ~ '^[A-Z][A-Z0-9]{1,9}$'", name="key_prefix_format"),
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name="slug_format"),
        CheckConstraint("next_task_number > 0", name="next_task_number_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Task keys are "<key_prefix>-<n>", e.g. ACC-12. The counter is bumped atomically.
    key_prefix: Mapped[str] = mapped_column(Text, unique=True)
    next_task_number: Mapped[int] = mapped_column(server_default=text("1"))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    archived_at: Mapped[datetime | None]


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_repositories_project_id_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(Text)
    remote_url: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(Text, server_default="main")
    protected_branches: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{main}'::text[]")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
