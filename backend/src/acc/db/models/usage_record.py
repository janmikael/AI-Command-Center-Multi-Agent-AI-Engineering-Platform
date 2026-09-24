import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class UsageRecord(Base):
    """One row per provider usage report (typically one per SDK turn), so a run's cost
    is graphable over its timeline, not just a final total."""

    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint("cost_source IN ('provider_reported', 'estimated')", name="cost_source"),
        Index("ix_usage_records_run_id", "run_id"),
        Index("ix_usage_records_recorded_at", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cache_write_tokens: Mapped[int] = mapped_column(server_default=text("0"))
    cost_usd: Mapped[Any] = mapped_column(Numeric(12, 6), server_default=text("0"))
    cost_source: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())
