"""tool_calls, file_changes: run history detail

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("denial_reason", sa.Text(), nullable=True),
        sa.Column("result_excerpt", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("ended_at", TZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_tool_calls_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tool_calls_run_id_started_at", "tool_calls", ["run_id", "started_at"])

    op.create_table(
        "file_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=True),
        sa.Column("deletions", sa.Integer(), nullable=True),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_changes")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_file_changes_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_file_changes_task_id_tasks")
        ),
        sa.CheckConstraint(
            "change_type IN ('added', 'modified', 'deleted', 'renamed')",
            name=op.f("ck_file_changes_change_type"),
        ),
    )
    op.create_index("ix_file_changes_run_id", "file_changes", ["run_id"])


def downgrade() -> None:
    op.drop_table("file_changes")
    op.drop_table("tool_calls")
