"""events.run_id: so a run's own event timeline (GET /runs/{id}/events) doesn't have to
infer membership from a time window against task_id

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-25

`events` is partitioned (migration 0006) and was created with raw DDL rather than
op.create_table for that reason; this migration follows the same style. ALTER TABLE ADD
COLUMN and CREATE INDEX on a partitioned parent both cascade to every existing partition
and are inherited by partitions created later by acc_ensure_event_partitions().
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE events ADD COLUMN run_id uuid REFERENCES agent_runs(id)"
    )
    op.execute("CREATE INDEX ix_events_run_id_ts ON events (run_id, ts)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_events_run_id_ts")
    op.execute("ALTER TABLE events DROP COLUMN run_id")
