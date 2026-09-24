"""tasks and the task_status enum

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)

# The full lifecycle up front, so later phases need no enum migration.
TASK_STATUSES = (
    "backlog",
    "planning",
    "working",
    "review",
    "awaiting_approval",
    "blocked",
    "completed",
    "failed",
    "cancelled",
)


def upgrade() -> None:
    postgresql.ENUM(*TASK_STATUSES, name="task_status").create(op.get_bind())

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "acceptance_criteria",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(*TASK_STATUSES, name="task_status", create_type=False),
            server_default="backlog",
            nullable=False,
        ),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("3"), nullable=False),
        sa.Column("assigned_agent_id", sa.Uuid(), nullable=True),
        sa.Column("required_capability", sa.Text(), nullable=True),
        sa.Column("progress_pct", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", TZ, nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("key", name=op.f("uq_tasks_key")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_tasks_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], name=op.f("fk_tasks_repository_id_repositories")
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["tasks.id"], name=op.f("fk_tasks_parent_id_tasks")),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id"], ["agents.id"], name=op.f("fk_tasks_assigned_agent_id_agents")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_tasks_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name=op.f("ck_tasks_title_length")
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 5", name=op.f("ck_tasks_priority_range")),
        sa.CheckConstraint("progress_pct BETWEEN 0 AND 100", name=op.f("ck_tasks_progress_range")),
    )
    op.create_index("ix_tasks_project_id_status", "tasks", ["project_id", "status"])
    op.create_index("ix_tasks_parent_id", "tasks", ["parent_id"])
    op.create_index(
        "ix_tasks_assigned_agent_id_active",
        "tasks",
        ["assigned_agent_id"],
        postgresql_where=sa.text("status NOT IN ('completed', 'cancelled', 'failed')"),
    )


def downgrade() -> None:
    op.drop_table("tasks")  # drops its indexes
    postgresql.ENUM(name="task_status").drop(op.get_bind())
