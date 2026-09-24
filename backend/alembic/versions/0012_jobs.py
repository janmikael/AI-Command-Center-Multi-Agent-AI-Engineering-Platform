"""jobs: the durable outbox queue (ADR-2), scoped to Phase 2's three job kinds

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)
JOB_KINDS = "('start_run', 'cancel_run', 'timeout_check')"


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("run_after", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", TZ, nullable=True),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.CheckConstraint(f"kind IN {JOB_KINDS}", name=op.f("ck_jobs_kind")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'dead')", name=op.f("ck_jobs_status")
        ),
    )
    op.create_index("ix_jobs_status_run_after", "jobs", ["status", "run_after"])


def downgrade() -> None:
    op.drop_table("jobs")
