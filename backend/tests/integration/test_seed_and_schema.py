import re

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from acc.commands.seed import AGENTS, GOVERNANCE_RULES, seed
from acc.db.models import Agent, AgentCapability, Event, GovernanceRule, Project
from acc.db.session import SessionFactory

pytestmark = pytest.mark.integration


async def count(sf: SessionFactory, model: type) -> int:
    async with sf() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


class TestSeed:
    async def test_creates_project_agents_capabilities_and_events(self, sf: SessionFactory) -> None:
        result = await seed(sf)
        assert result.created_projects == ["acc"]
        assert result.created_agents == ["architect", "developer", "designer"]
        assert result.created_governance_rules == [r[1] for r in GOVERNANCE_RULES]
        assert await count(sf, Project) == 1
        assert await count(sf, Agent) == 3
        assert await count(sf, AgentCapability) == sum(len(a.capabilities) for a in AGENTS)
        assert await count(sf, GovernanceRule) == len(GOVERNANCE_RULES)
        async with sf() as session:
            types = (await session.scalars(select(Event.type).order_by(Event.seq))).all()
        expected = (
            ["project.created"]
            + ["agent.registered"] * 3
            + ["governance_rule.created"] * len(GOVERNANCE_RULES)
        )
        assert types == expected

    async def test_is_idempotent(self, sf: SessionFactory) -> None:
        await seed(sf)
        again = await seed(sf)
        assert again.created_projects == []
        assert again.created_agents == []
        assert again.created_governance_rules == []
        assert await count(sf, Agent) == 3
        assert await count(sf, GovernanceRule) == len(GOVERNANCE_RULES)
        assert await count(sf, Event) == 4 + len(GOVERNANCE_RULES)  # no duplicate events

    async def test_never_overwrites_edited_governance_rule(
        self, sf: SessionFactory, owner_conn: AsyncConnection
    ) -> None:
        await seed(sf)
        await owner_conn.execute(
            text("UPDATE governance_rules SET enabled = false WHERE title = 'Never expose secrets'")
        )
        await seed(sf)
        async with sf() as session:
            rule = await session.scalar(
                select(GovernanceRule).where(GovernanceRule.title == "Never expose secrets")
            )
        assert rule is not None
        assert rule.enabled is False

    async def test_never_overwrites_operator_changes(
        self, sf: SessionFactory, owner_conn: AsyncConnection
    ) -> None:
        await seed(sf)
        await owner_conn.execute(
            text(
                "UPDATE agents SET enabled = false, display_name = 'Renamed' "
                "WHERE slug = 'designer'"
            )
        )
        await seed(sf)
        async with sf() as session:
            designer = await session.scalar(select(Agent).where(Agent.slug == "designer"))
        assert designer is not None
        assert designer.enabled is False
        assert designer.display_name == "Renamed"

    async def test_agents_start_without_runtime(self, sf: SessionFactory) -> None:
        await seed(sf)
        async with sf() as session:
            adapters = set((await session.scalars(select(Agent.adapter))).all())
        assert adapters == {"none"}

    async def test_seeded_match_patterns_are_persisted(self, sf: SessionFactory) -> None:
        """PLAN-PHASE-2-M4.md's governance-enforcement-data fix: match is no longer {}
        for every enforceable rule once seeded fresh."""
        await seed(sf)
        async with sf() as session:
            rules = (await session.scalars(select(GovernanceRule))).all()
        by_title = {r.title: r for r in rules}
        for _category, title, _statement, effect, match in GOVERNANCE_RULES:
            assert by_title[title].match == match
            if effect in ("deny", "require_approval"):
                assert match, f"{title!r} is enforceable but has an empty match"


class TestGovernanceRuleMatchPatterns:
    """runner/agent_sdk.py's rule_matches is the actual consumer of this data — these
    patterns are only "minimum required" if they compile and actually catch a realistic
    example of what their statement describes."""

    @pytest.mark.parametrize(
        ("title", "example_command"),
        [
            ("Never expose secrets", "cat .env"),
            ("Never access protected credentials", "cat ~/.aws/credentials"),
            ("Never bypass approval requirements", "git commit --no-verify"),
            ("Paid tools require approval", "aws s3 ls"),
            ("Third-party services/APIs require approval", "curl https://example.com"),
            ("Unknown external tools require approval", "pip install requests"),
            ("Never delete important files without approval", "rm old_file.py"),
            ("Production deployment requires approval", "kubectl apply -f prod.yaml"),
            ("Database migrations require approval", "alembic upgrade head"),
            ("Destructive commands require approval", "rm -rf /tmp/build"),
        ],
    )
    def test_pattern_matches_a_realistic_example(self, title: str, example_command: str) -> None:
        by_title = {r[1]: r for r in GOVERNANCE_RULES}
        match = by_title[title][4]
        assert match, f"{title!r} has no match pattern to test"
        assert re.search(match["command_regex"], example_command), (
            f"{title!r}'s pattern does not match {example_command!r}"
        )

    def test_every_command_regex_compiles(self) -> None:
        for _category, _title, _statement, _effect, match in GOVERNANCE_RULES:
            if "command_regex" in match:
                re.compile(match["command_regex"])  # raises re.error if malformed

    def test_advisory_and_allow_rules_have_no_match(self) -> None:
        """The gate never evaluates these effects (runner/agent_sdk.py's
        make_can_use_tool) — a match here would be inert, so there is none."""
        for _category, _title, _statement, effect, match in GOVERNANCE_RULES:
            if effect in ("advisory", "allow"):
                assert match == {}


class TestConstraints:
    @pytest.fixture
    async def project_id(self, sf: SessionFactory) -> str:
        await seed(sf)
        async with sf() as session:
            return str(await session.scalar(select(Project.id)))

    @pytest.mark.parametrize(
        ("sql", "constraint"),
        [
            (
                "INSERT INTO projects (id, slug, name, key_prefix) "
                "VALUES (gen_random_uuid(), 'x', 'X', 'lower')",
                "ck_projects_key_prefix_format",
            ),
            (
                "INSERT INTO agents (id, slug, display_name, role) "
                "VALUES (gen_random_uuid(), 'Bad Slug', 'x', 'x')",
                "ck_agents_slug_format",
            ),
            (
                "INSERT INTO tasks (id, project_id, key, title, priority) "
                "VALUES (gen_random_uuid(), :project, 'ACC-900', 't', 9)",
                "ck_tasks_priority_range",
            ),
            (
                "INSERT INTO tasks (id, project_id, key, title, progress_pct) "
                "VALUES (gen_random_uuid(), :project, 'ACC-901', 't', 101)",
                "ck_tasks_progress_range",
            ),
            (
                "INSERT INTO tasks (id, project_id, key, title) "
                "VALUES (gen_random_uuid(), :project, 'ACC-902', '')",
                "ck_tasks_title_length",
            ),
            (
                "INSERT INTO tasks (id, project_id, key, title, status) "
                "VALUES (gen_random_uuid(), :project, 'ACC-903', 't', 'done')",
                "task_status",
            ),
            (
                "INSERT INTO events (id, actor, type, severity) "
                "VALUES (gen_random_uuid(), 'system', 'x', 'fatal')",
                "ck_events_severity",
            ),
            (
                "INSERT INTO governance_rules (id, category, title, statement, effect) "
                "VALUES (gen_random_uuid(), 'not-a-category', 'x', 'x', 'deny')",
                "ck_governance_rules_category",
            ),
            (
                "INSERT INTO governance_rules (id, category, title, statement, effect) "
                "VALUES (gen_random_uuid(), 'security', 'x', 'x', 'not-an-effect')",
                "ck_governance_rules_effect",
            ),
        ],
        ids=[
            "key_prefix",
            "agent_slug",
            "priority",
            "progress",
            "title",
            "status",
            "severity",
            "governance_category",
            "governance_effect",
        ],
    )
    async def test_check_constraints(
        self, app_conn: AsyncConnection, project_id: str, sql: str, constraint: str
    ) -> None:
        with pytest.raises(DBAPIError, match=constraint):
            await app_conn.execute(text(sql), {"project": project_id})

    async def test_task_keys_are_unique(self, app_conn: AsyncConnection, project_id: str) -> None:
        sql = text(
            "INSERT INTO tasks (id, project_id, key, title) "
            "VALUES (gen_random_uuid(), :project, 'ACC-1', 't')"
        )
        await app_conn.execute(sql, {"project": project_id})
        with pytest.raises(DBAPIError, match="uq_tasks_key"):
            await app_conn.execute(sql, {"project": project_id})

    async def test_runtime_role_cannot_delete_referenced_agent(
        self, app_conn: AsyncConnection, project_id: str
    ) -> None:
        await app_conn.execute(
            text(
                "INSERT INTO tasks (id, project_id, key, title, assigned_agent_id) "
                "SELECT gen_random_uuid(), :project, 'ACC-5', 't', id FROM agents "
                "WHERE slug = 'developer'"
            ),
            {"project": project_id},
        )
        with pytest.raises(DBAPIError, match="foreign key"):
            await app_conn.execute(text("DELETE FROM agents WHERE slug = 'developer'"))

    async def test_governance_rule_titles_are_unique(self, app_conn: AsyncConnection) -> None:
        sql = text(
            "INSERT INTO governance_rules (id, category, title, statement, effect) "
            "VALUES (gen_random_uuid(), 'security', 'Dup', 'x', 'deny')"
        )
        await app_conn.execute(sql)
        with pytest.raises(DBAPIError, match="uq_governance_rules_title"):
            await app_conn.execute(sql)

    async def test_context_entry_kind_is_checked(
        self, app_conn: AsyncConnection, project_id: str
    ) -> None:
        with pytest.raises(DBAPIError, match="ck_project_context_entries_kind"):
            await app_conn.execute(
                text(
                    "INSERT INTO project_context_entries (id, project_id, kind, title, body) "
                    "VALUES (gen_random_uuid(), :project, 'not-a-kind', 't', 'b')"
                ),
                {"project": project_id},
            )

    async def test_context_entry_requires_existing_project(
        self, app_conn: AsyncConnection
    ) -> None:
        with pytest.raises(DBAPIError, match="foreign key"):
            await app_conn.execute(
                text(
                    "INSERT INTO project_context_entries (id, project_id, kind, title, body) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), 'gotcha', 't', 'b')"
                )
            )
