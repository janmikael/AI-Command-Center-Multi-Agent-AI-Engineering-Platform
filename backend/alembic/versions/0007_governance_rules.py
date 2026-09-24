"""governance_rules: Global AI Rules, one set, applies to every project

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "governance_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("effect", sa.Text(), nullable=False),
        sa.Column(
            "match", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "applies_to",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_rules")),
        sa.UniqueConstraint("title", name=op.f("uq_governance_rules_title")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_governance_rules_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "category IN ('security', 'tools', 'code', 'infrastructure')",
            name=op.f("ck_governance_rules_category"),
        ),
        sa.CheckConstraint(
            "effect IN ('allow', 'require_approval', 'deny', 'advisory')",
            name=op.f("ck_governance_rules_effect"),
        ),
    )
    op.create_index(
        "ix_governance_rules_category_enabled", "governance_rules", ["category", "enabled"]
    )


def downgrade() -> None:
    op.drop_table("governance_rules")  # drops its index too
