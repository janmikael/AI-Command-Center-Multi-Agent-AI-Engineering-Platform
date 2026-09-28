"""tool_calls lifecycle/governance columns, agent_runs timeout-warning marker
(PLAN-PHASE-2-M4.2.md §3):

- tool_use_id: correlates a call's pre-execution row (can_use_tool) with its
  post-execution completion report (the new PostToolUse hook) — nullable,
  since a denied call never executes and has nothing to correlate.
- rule_id: which governance rule caused a denial, if any (traceability).
- blocked_by_effect: 'deny' vs 'require_approval' — approval-state
  preparation only, no approval mechanism reads this yet.
- agent_runs.timeout_warning_emitted_at: lets reconcile_active_runs emit the
  wall-clock warning event exactly once per run, surviving an orchestrator
  restart (an in-memory "already warned" set would not).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("tool_calls", sa.Column("tool_use_id", sa.Text(), nullable=True))
    op.add_column(
        "tool_calls",
        sa.Column(
            "rule_id",
            sa.Uuid(),
            sa.ForeignKey("governance_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "tool_calls",
        sa.Column(
            "blocked_by_effect",
            sa.Text(),
            sa.CheckConstraint(
                "blocked_by_effect IN ('deny', 'require_approval')",
                name="ck_tool_calls_blocked_by_effect",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tool_calls_run_id_tool_use_id", "tool_calls", ["run_id", "tool_use_id"]
    )
    op.add_column("agent_runs", sa.Column("timeout_warning_emitted_at", TZ, nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "timeout_warning_emitted_at")
    op.drop_index("ix_tool_calls_run_id_tool_use_id", table_name="tool_calls")
    op.drop_column("tool_calls", "blocked_by_effect")
    op.drop_column("tool_calls", "rule_id")
    op.drop_column("tool_calls", "tool_use_id")
