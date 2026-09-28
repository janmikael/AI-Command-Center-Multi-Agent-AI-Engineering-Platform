"""governance_rules.match: backfill for existing seeded rows.

acc.commands.seed's own contract never modifies an existing row (see
tests/integration/test_seed_and_schema.py::test_never_overwrites_edited_governance_rule)
— so a database seeded before this milestone keeps match={} forever unless something
else sets it once, deliberately. This is that one-time, deliberate cutover: the same 10
patterns now in acc.commands.seed.GOVERNANCE_RULES for a fresh install, applied here by
title to rows that already exist. Only rows still at match='{}' are touched, so an
operator who already customized a rule's match (there is no API for that yet, but a
migration should never assume there couldn't be a manual edit) keeps it.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (title, match) — kept in sync with acc.commands.seed.GOVERNANCE_RULES by hand; there
# are only 10 and they change rarely, same reasoning as every other seed-data migration
# in this project (e.g. 0007's initial seed data lives in code, not here).
PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "Never expose secrets",
        r'{"tool": "Bash", "command_regex": "\\.env\\b|\\.pem\\b|id_rsa|credentials\\.json'
        r'|\\bprintenv\\b"}',
    ),
    (
        "Never access protected credentials",
        r'{"tool": "Bash", "command_regex": "\\.aws/credentials|\\.ssh/id_|/etc/shadow'
        r'|\\.netrc\\b|\\.pgpass\\b|\\.git-credentials\\b"}',
    ),
    (
        "Never bypass approval requirements",
        r'{"tool": "Bash", "command_regex": "--no-verify\\b|\\bsudo\\b"}',
    ),
    (
        "Paid tools require approval",
        r'{"tool": "Bash", "command_regex": "\\b(aws|gcloud|az|terraform|stripe|openai)\\b"}',
    ),
    (
        "Third-party services/APIs require approval",
        r'{"tool": "Bash", "command_regex": "\\b(curl|wget|nc|ssh)\\b"}',
    ),
    (
        "Unknown external tools require approval",
        r'{"tool": "Bash", "command_regex": "\\b(pip3?\\s+install|npm\\s+install'
        r'|apt(-get)?\\s+install|brew\\s+install|gem\\s+install)\\b"}',
    ),
    (
        "Never delete important files without approval",
        r'{"tool": "Bash", "command_regex": "\\brm\\b|\\brmdir\\b|\\bunlink\\b|\\bgit\\s+rm\\b"}',
    ),
    (
        "Production deployment requires approval",
        r'{"tool": "Bash", "command_regex": "\\bkubectl\\b|\\bdocker\\s+push\\b'
        r'|\\bhelm\\s+(install|upgrade)\\b|\\bterraform\\s+apply\\b"}',
    ),
    (
        "Database migrations require approval",
        r'{"tool": "Bash", "command_regex": "\\balembic\\s+(upgrade|downgrade)\\b|\\bmigrate\\b"}',
    ),
    (
        "Destructive commands require approval",
        r'{"tool": "Bash", "command_regex": "\\brm\\s+-[a-zA-Z]*f\\b|\\bdd\\s+if=|\\bmkfs\\b'
        r'|\\bshutdown\\b|\\breboot\\b|drop\\s+table|drop\\s+database|push\\s+--force'
        r'|--force\\b.*push"}',
    ),
)


def upgrade() -> None:
    # CAST(:match AS jsonb), not :match::jsonb — SQLAlchemy's text() bind-parameter
    # scanner does not reliably recognize a bind name immediately followed by Postgres's
    # `::` cast syntax (caught live: asyncpg received the literal, un-substituted text
    # ":match::jsonb" and raised a syntax error).
    conn = op.get_bind()
    for title, match_json in PATTERNS:
        conn.execute(
            sa.text(
                "UPDATE governance_rules SET match = CAST(:match AS jsonb) "
                "WHERE title = :title AND match = '{}'::jsonb"
            ),
            {"title": title, "match": match_json},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for title, _ in PATTERNS:
        conn.execute(
            sa.text("UPDATE governance_rules SET match = '{}'::jsonb WHERE title = :title"),
            {"title": title},
        )
