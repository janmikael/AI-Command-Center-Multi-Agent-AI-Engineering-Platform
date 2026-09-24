import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from acc.commands.seed import AGENTS, seed
from acc.db.models import Agent, AgentCapability, Event, Project
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
        assert await count(sf, Project) == 1
        assert await count(sf, Agent) == 3
        assert await count(sf, AgentCapability) == sum(len(a.capabilities) for a in AGENTS)
        async with sf() as session:
            types = (await session.scalars(select(Event.type).order_by(Event.seq))).all()
        assert types == ["project.created"] + ["agent.registered"] * 3

    async def test_is_idempotent(self, sf: SessionFactory) -> None:
        await seed(sf)
        again = await seed(sf)
        assert again.created_projects == []
        assert again.created_agents == []
        assert await count(sf, Agent) == 3
        assert await count(sf, Event) == 4  # no duplicate events

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
        ],
        ids=["key_prefix", "agent_slug", "priority", "progress", "title", "status", "severity"],
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
