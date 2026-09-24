import uuid
from datetime import datetime

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class GovernanceRule(Base):
    """A Global AI Rule (ARCHITECTURE.md §10). Applies to every project identically —
    there is deliberately no project_id here; see Guiding Principle 8.

    `match`/`priority` are shaped to match the not-yet-built `policies` table so a
    future MCP gateway can consume this directly. No hard delete: disabling is a PATCH
    (enabled=false), itself an event, same as everything else in this system.
    """

    __tablename__ = "governance_rules"
    __table_args__ = (
        CheckConstraint(
            "category IN ('security', 'tools', 'code', 'infrastructure')", name="category"
        ),
        CheckConstraint(
            "effect IN ('allow', 'require_approval', 'deny', 'advisory')", name="effect"
        ),
        Index("ix_governance_rules_category_enabled", "category", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    category: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, unique=True)
    statement: Mapped[str] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(Text)
    # Machine-checkable pattern for a future gateway, e.g. {"tool_cost_tier": "paid"}.
    # Empty for most seed rows today — nothing evaluates it yet in Phase 1.5.
    match: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    applies_to: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    priority: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
