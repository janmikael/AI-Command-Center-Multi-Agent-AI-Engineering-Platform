"""agents registry and capabilities

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No status column on purpose: agent status is derived, never stored.
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("adapter", sa.Text(), server_default="none", nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("slug", name=op.f("uq_agents_slug")),
        sa.CheckConstraint("slug ~ '^[a-z][a-z0-9-]{1,39}$'", name=op.f("ck_agents_slug_format")),
    )

    op.create_table(
        "agent_capabilities",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.PrimaryKeyConstraint("agent_id", "capability", name=op.f("pk_agent_capabilities")),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_capabilities_agent_id_agents"),
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_capabilities")
    op.drop_table("agents")
