"""agent_runs.cache_write_tokens: closes a real asymmetry with usage_records (PLAN-
PHASE-2-M4.md §8) — both tables were added together in 0009/0010, but only usage_records
ever got all five usage columns. M4 is the first real writer of either, so this is the
moment to fix it rather than carry the gap forward.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("cache_write_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "cache_write_tokens")
