"""usage_records: one row per provider usage report, so a run's cost is graphable

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cache_read_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "cache_write_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "cost_usd", sa.Numeric(12, 6), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("cost_source", sa.Text(), nullable=False),
        sa.Column("recorded_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_usage_records_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_usage_records_agent_id_agents")
        ),
        sa.CheckConstraint(
            "cost_source IN ('provider_reported', 'estimated')",
            name=op.f("ck_usage_records_cost_source"),
        ),
    )
    op.create_index("ix_usage_records_run_id", "usage_records", ["run_id"])
    op.create_index("ix_usage_records_recorded_at", "usage_records", ["recorded_at"])


def downgrade() -> None:
    op.drop_table("usage_records")
