"""tasks.branch_name, agents.max_concurrent_runs: small additive columns for Phase 2

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("branch_name", sa.Text(), nullable=True))
    op.add_column(
        "agents",
        sa.Column(
            "max_concurrent_runs", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "max_concurrent_runs")
    op.drop_column("tasks", "branch_name")
