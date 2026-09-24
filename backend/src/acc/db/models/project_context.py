import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class ProjectContextEntry(Base):
    """A per-project knowledge base entry — what the AI knows about this codebase, as
    distinct from GovernanceRule (what it may do everywhere). See ARCHITECTURE.md §10.1.

    Unlike GovernanceRule, this supports a real delete: it's an editable note, not an
    audit record. The deleting command still emits an event with an id+title snapshot.
    """

    __tablename__ = "project_context_entries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('architecture', 'convention', 'integration', 'gotcha', 'glossary')",
            name="kind",
        ),
        Index("ix_project_context_entries_project_id_priority", "project_id", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
