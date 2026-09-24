"""projects and repositories

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("next_task_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "settings", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.Column("archived_at", TZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("slug", name=op.f("uq_projects_slug")),
        sa.UniqueConstraint("key_prefix", name=op.f("uq_projects_key_prefix")),
        sa.CheckConstraint(
            "key_prefix ~ '^[A-Z][A-Z0-9]{1,9}$'", name=op.f("ck_projects_key_prefix_format")
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name=op.f("ck_projects_slug_format")
        ),
        sa.CheckConstraint(
            "next_task_number > 0", name=op.f("ck_projects_next_task_number_positive")
        ),
    )

    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("remote_url", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), server_default="main", nullable=False),
        sa.Column(
            "protected_branches",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{main}'::text[]"),
            nullable=False,
        ),
        sa.Column("created_at", TZ, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_repositories_project_id_projects")
        ),
        sa.UniqueConstraint("project_id", "name", name=op.f("uq_repositories_project_id_name")),
    )


def downgrade() -> None:
    op.drop_table("repositories")
    op.drop_table("projects")
