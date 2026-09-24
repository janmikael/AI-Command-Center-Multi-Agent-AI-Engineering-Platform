"""agent_runs: one agent working one task, end to end (Phase 2)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)
RUN_STATUSES = "('queued','starting','running','succeeded','failed','cancelled','timed_out')"
ACTIVE_RUN_STATUSES = "('queued','starting','running')"


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("provider_session_id", sa.Text(), nullable=True),
        sa.Column("container_id", sa.Text(), nullable=True),
        sa.Column("workspace_ref", sa.Text(), nullable=True),
        sa.Column("branch_name", sa.Text(), nullable=True),
        sa.Column("current_step", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", TZ, nullable=True),
        sa.Column("queued_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", TZ, nullable=True),
        sa.Column("ended_at", TZ, nullable=True),
        sa.Column("last_heartbeat_at", TZ, nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_usd", sa.Numeric(12, 6), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_agent_runs_task_id_tasks")
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_runs_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_agent_runs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"status IN {RUN_STATUSES}", name=op.f("ck_agent_runs_status")),
    )
    op.create_index("ix_agent_runs_agent_id_status", "agent_runs", ["agent_id", "status"])
    op.create_index("ix_agent_runs_task_id_queued_at", "agent_runs", ["task_id", "queued_at"])
    op.create_index(
        "ix_agent_runs_one_active_per_task",
        "agent_runs",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN {ACTIVE_RUN_STATUSES}"),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")  # drops its indexes too
