import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class Agent(Base):
    """Registry entry. Agents are data: adding one needs no schema change.

    There is deliberately no status column: status is derived (acc.domain.agent_status).
    """

    __tablename__ = "agents"
    __table_args__ = (CheckConstraint("slug ~ '^[a-z][a-z0-9-]{1,39}$'", name="slug_format"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    # Runtime adapter ('none' until Phase 2 wires real runtimes).
    adapter: Mapped[str] = mapped_column(Text, server_default="none")
    model: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AgentCapability(Base):
    __tablename__ = "agent_capabilities"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(Text, primary_key=True)
    # Lower wins when several agents offer the same capability.
    priority: Mapped[int] = mapped_column(server_default=text("100"))
