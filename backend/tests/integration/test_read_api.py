"""Agents, events feed, overview and projects endpoints."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from acc.commands._base import unit_of_work
from acc.domain.events import SIMULATOR, AgentMessage, EventType
from tests.integration.test_auth import assert_problem
from tests.integration.test_tasks_api import create, move

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]


async def agent_says(app: FastAPI, slug: str, text: str) -> None:
    """Simulate agent activity the way the Phase 1 simulator does (through the command layer)."""
    from sqlalchemy import select

    from acc.db.models import Agent

    async with unit_of_work(app.state.session_factory, None) as uow:
        agent_id = await uow.session.scalar(select(Agent.id).where(Agent.slug == slug))
        await uow.emit(
            EventType.AGENT_MESSAGE,
            AgentMessage(text=text),
            actor=SIMULATOR,
            agent_id=agent_id,
            agent_slug=slug,
        )


def by_slug(agents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {a["slug"]: a for a in agents}


class TestAgents:
    async def test_seeded_agents_are_honestly_offline(self, signed_in: httpx.AsyncClient) -> None:
        agents = by_slug((await signed_in.get("/api/v1/agents")).json()["items"])
        assert set(agents) == {"architect", "developer", "designer"}
        assert {a["status"] for a in agents.values()} == {"offline"}
        assert agents["developer"]["capabilities"] == ["implement", "test"]
        assert agents["developer"]["adapter"] == "none"
        assert agents["developer"]["last_activity_at"] is None

    async def test_status_is_derived_from_activity_and_active_tasks(
        self, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        key = (await create(signed_in, assignee="developer"))["task"]["key"]
        agents = by_slug((await signed_in.get("/api/v1/agents")).json()["items"])
        # Being assigned is something done *to* the agent: still offline.
        assert agents["developer"]["status"] == "offline"

        await agent_says(app, "developer", "Reading the codebase")
        await agent_says(app, "designer", "Idle thought")
        agents = by_slug((await signed_in.get("/api/v1/agents")).json()["items"])
        assert agents["developer"]["status"] == "idle"  # active but task still in backlog
        assert agents["designer"]["status"] == "idle"

        # Create-with-assignee is one operation, so the task is still at version 0.
        assert (await move(signed_in, key, "planning", 0)).status_code == 200
        agents = by_slug((await signed_in.get("/api/v1/agents")).json()["items"])
        assert agents["developer"]["status"] == "working"
        assert agents["developer"]["current_task"]["key"] == key
        assert agents["developer"]["active_task_count"] == 1

    async def test_detail(self, signed_in: httpx.AsyncClient, app: FastAPI) -> None:
        key = (await create(signed_in, assignee="architect"))["task"]["key"]
        await agent_says(app, "architect", "Drafting the plan")
        detail = (await signed_in.get("/api/v1/agents/architect")).json()
        assert detail["agent"]["display_name"] == "Architect"
        assert [t["key"] for t in detail["open_tasks"]] == [key]
        assert detail["recent_events"][0]["type"] == "agent.message"
        assert detail["recent_events"][0]["actor_label"] == "Simulator"

    async def test_unknown_agent(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(await signed_in.get("/api/v1/agents/ghost"), 404)


class TestEventsFeed:
    async def test_newest_first_with_labels_and_cursor(self, signed_in: httpx.AsyncClient) -> None:
        for i in range(5):
            await create(signed_in, title=f"t{i}")
        page1 = (await signed_in.get("/api/v1/events", params={"limit": 3})).json()
        seqs1 = [e["seq"] for e in page1["items"]]
        assert seqs1 == sorted(seqs1, reverse=True)
        assert page1["next_cursor"] == seqs1[-1]
        first = page1["items"][0]
        assert first["type"] == "task.created"
        assert first["task_key"] == "ACC-5"
        assert first["actor_label"] == "Test Operator"

        page2 = (
            await signed_in.get(
                "/api/v1/events", params={"limit": 3, "before_seq": page1["next_cursor"]}
            )
        ).json()
        assert all(e["seq"] < seqs1[-1] for e in page2["items"])

    async def test_after_seq_replays_oldest_first(self, signed_in: httpx.AsyncClient) -> None:
        start = (await signed_in.get("/api/v1/events", params={"limit": 1})).json()["items"][0]
        await create(signed_in, title="a")
        await create(signed_in, title="b")
        replay = (await signed_in.get("/api/v1/events", params={"after_seq": start["seq"]})).json()
        assert [e["payload"]["title"] for e in replay["items"]] == ["a", "b"]
        assert replay["next_cursor"] is None

    async def test_filters(self, signed_in: httpx.AsyncClient, app: FastAPI) -> None:
        key = (await create(signed_in, assignee="developer"))["task"]["key"]
        await agent_says(app, "designer", "hello")

        async def types(**params: Any) -> list[str]:
            items = (await signed_in.get("/api/v1/events", params=params)).json()["items"]
            return [e["type"] for e in items]

        assert await types(task=key) == ["task.assigned", "task.created"]
        assert await types(agent="designer") == ["agent.message", "agent.registered"]
        assert await types(type="agent.message") == ["agent.message"]
        assert await types(project="acc", type="project.created") == ["project.created"]


class TestOverview:
    async def test_aggregates(self, signed_in: httpx.AsyncClient) -> None:
        a = (await create(signed_in, title="A", assignee="developer"))["task"]["key"]
        await create(signed_in, title="B")
        assert (await move(signed_in, a, "planning", 0)).status_code == 200

        overview = (await signed_in.get("/api/v1/overview")).json()
        assert overview["task_counts"]["planning"] == 1
        assert overview["task_counts"]["backlog"] == 1
        assert overview["task_counts"]["completed"] == 0
        assert [t["key"] for t in overview["active_tasks"]] == [a]
        assert len(overview["agents"]) == 3
        assert overview["recent_events"][0]["type"] == "task.status_changed"
        assert overview["latest_seq"] == overview["recent_events"][0]["seq"]
        assert overview["events_last_24h"] >= 4
        assert overview["system"] == {"database": True, "redis": True, "realtime_clients": 0}


class TestProjects:
    async def test_create_project_and_repository(self, signed_in: httpx.AsyncClient) -> None:
        created = await signed_in.post(
            "/api/v1/projects", json={"slug": "shop", "name": "Shop", "key_prefix": "SHOP"}
        )
        assert created.status_code == 201, created.text
        repo = await signed_in.post(
            "/api/v1/projects/shop/repositories",
            json={"name": "shop-web", "remote_url": "git@github.com:me/shop-web.git"},
        )
        assert repo.status_code == 201
        assert repo.json()["repositories"] == [
            {
                "name": "shop-web",
                "remote_url": "git@github.com:me/shop-web.git",
                "default_branch": "main",
            }
        ]
        task = await create(signed_in, project="shop", repository="shop-web")
        assert task["task"]["key"] == "SHOP-1"
        assert task["repository"] == "shop-web"
        slugs = [p["slug"] for p in (await signed_in.get("/api/v1/projects")).json()["items"]]
        assert slugs == ["acc", "shop"]

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"slug": "acc", "name": "Dup", "key_prefix": "DUP"}, 409),
            ({"slug": "other", "name": "Dup", "key_prefix": "ACC"}, 409),
            ({"slug": "Bad Slug", "name": "x", "key_prefix": "BAD"}, 422),
            ({"slug": "ok", "name": "x", "key_prefix": "lower"}, 422),
        ],
    )
    async def test_rejects(
        self, signed_in: httpx.AsyncClient, body: dict[str, str], status: int
    ) -> None:
        assert_problem(await signed_in.post("/api/v1/projects", json=body), status)

    async def test_duplicate_repository(self, signed_in: httpx.AsyncClient) -> None:
        body = {"name": "core", "remote_url": "https://example.com/core.git"}
        await signed_in.post("/api/v1/projects/acc/repositories", json=body)
        assert_problem(await signed_in.post("/api/v1/projects/acc/repositories", json=body), 409)
