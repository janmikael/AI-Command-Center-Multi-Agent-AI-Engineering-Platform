"""Task API: create, edit, the single status-change path, assignment, optimistic locking,
and the events each mutation leaves behind."""

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.integration.test_auth import assert_problem

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

TASKS = "/api/v1/tasks"


async def create(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    body = {"project": "acc", "title": "Add health endpoint", **overrides}
    response = await client.post(TASKS, json=body)
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


async def move(
    client: httpx.AsyncClient, key: str, to: str, version: int, reason: str | None = None
) -> httpx.Response:
    return await client.post(
        f"{TASKS}/{key}/transition",
        json={"to": to, "reason": reason},
        headers={"if-match": f'"{version}"'},
    )


async def task_event_types(client: httpx.AsyncClient, key: str) -> list[str]:
    page = (await client.get(f"{TASKS}/{key}/events", params={"limit": 200})).json()
    return [e["type"] for e in reversed(page["items"])]  # oldest first


class TestCreate:
    async def test_creates_in_backlog_with_sequential_keys(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        first = await create(signed_in)
        second = await create(signed_in, title="Second")
        assert first["task"]["key"] == "ACC-1"
        assert second["task"]["key"] == "ACC-2"
        assert first["task"]["status"] == "backlog"
        assert first["task"]["version"] == 0
        assert first["task"]["progress_pct"] == 0
        assert first["task"]["allowed_transitions"] == ["planning", "cancelled"]
        assert first["created_by"] == "Test Operator"
        assert [h["to_status"] for h in first["status_history"]] == ["backlog"]

    async def test_criteria_get_ids(self, signed_in: httpx.AsyncClient) -> None:
        task = await create(
            signed_in, acceptance_criteria=[{"text": "returns 200"}, {"text": "documented"}]
        )
        criteria = task["acceptance_criteria"]
        assert [c["text"] for c in criteria] == ["returns 200", "documented"]
        assert all(c["id"] and c["done"] is False for c in criteria)

    async def test_with_assignee_emits_created_then_assigned(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        task = await create(signed_in, assignee="developer")
        assert task["task"]["assignee_slug"] == "developer"
        assert await task_event_types(signed_in, task["task"]["key"]) == [
            "task.created",
            "task.assigned",
        ]

    @pytest.mark.parametrize(
        ("overrides", "status"),
        [
            ({"title": "   "}, 422),
            ({"title": "x" * 201}, 422),
            ({"priority": 6}, 422),
            ({"status": "working"}, 422),  # can't create a task in an arbitrary status
            ({"progress_pct": 50}, 422),
            ({"project": "nope"}, 404),
            ({"assignee": "ghost"}, 404),
            ({"repository": "missing"}, 404),
        ],
    )
    async def test_rejects(
        self, signed_in: httpx.AsyncClient, overrides: dict[str, Any], status: int
    ) -> None:
        response = await signed_in.post(TASKS, json={"project": "acc", "title": "t", **overrides})
        assert_problem(response, status)

    async def test_concurrent_creates_get_unique_keys(self, signed_in: httpx.AsyncClient) -> None:
        responses = await asyncio.gather(
            *(signed_in.post(TASKS, json={"project": "acc", "title": f"t{i}"}) for i in range(10))
        )
        keys = {r.json()["task"]["key"] for r in responses}
        assert len(keys) == 10
        assert keys == {f"ACC-{n}" for n in range(1, 11)}


class TestTransition:
    async def test_full_board_walk_records_history_and_events(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        task = await create(signed_in)
        key, version = task["task"]["key"], task["task"]["version"]
        for to in ["planning", "working", "review", "completed"]:
            response = await move(signed_in, key, to, version, reason=f"to {to}")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["task"]["status"] == to
            assert body["task"]["version"] == version + 1
            assert response.headers["etag"] == f'"{version + 1}"'
            version += 1

        final = (await signed_in.get(f"{TASKS}/{key}")).json()
        assert final["completed_at"] is not None
        assert final["task"]["allowed_transitions"] == []
        history = [(h["from_status"], h["to_status"]) for h in final["status_history"]]
        assert history == [
            (None, "backlog"),
            ("backlog", "planning"),
            ("planning", "working"),
            ("working", "review"),
            ("review", "completed"),
        ]
        assert final["status_history"][1]["reason"] == "to planning"
        assert all(h["actor_label"] == "Test Operator" for h in final["status_history"])
        assert (
            await task_event_types(signed_in, key) == ["task.created"] + ["task.status_changed"] * 4
        )

    async def test_illegal_move_is_409_with_allowed_targets(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        task = await create(signed_in)
        body = assert_problem(await move(signed_in, task["task"]["key"], "completed", 0), 409)
        assert body["type"] == "urn:acc:problem:invalid_transition"
        assert body["allowed"] == ["planning", "cancelled"]

    async def test_awaiting_approval_not_reachable_manually(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        task = await create(signed_in)
        key = task["task"]["key"]
        await move(signed_in, key, "planning", 0)
        await move(signed_in, key, "working", 1)
        await move(signed_in, key, "review", 2)
        assert_problem(await move(signed_in, key, "awaiting_approval", 3), 409)

    async def test_requires_if_match(self, signed_in: httpx.AsyncClient) -> None:
        task = await create(signed_in)
        response = await signed_in.post(
            f"{TASKS}/{task['task']['key']}/transition", json={"to": "planning"}
        )
        assert_problem(response, 428)

    async def test_stale_version_is_409_and_changes_nothing(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        task = await create(signed_in)
        key = task["task"]["key"]
        assert (await move(signed_in, key, "planning", 0)).status_code == 200
        body = assert_problem(await move(signed_in, key, "working", 0), 409)  # stale
        assert body["type"] == "urn:acc:problem:stale_version"
        assert body["current_version"] == 1
        assert (await signed_in.get(f"{TASKS}/{key}")).json()["task"]["status"] == "planning"

    async def test_concurrent_moves_exactly_one_wins(self, signed_in: httpx.AsyncClient) -> None:
        task = await create(signed_in)
        key = task["task"]["key"]
        results = await asyncio.gather(
            move(signed_in, key, "planning", 0), move(signed_in, key, "cancelled", 0)
        )
        assert sorted(r.status_code for r in results) == [200, 409]
        assert await task_event_types(signed_in, key) == ["task.created", "task.status_changed"]

    async def test_unknown_task_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(await move(signed_in, "ACC-999", "planning", 0), 404)


class TestPatch:
    async def test_edits_fields_and_records_a_diff(self, signed_in: httpx.AsyncClient) -> None:
        task = await create(signed_in, priority=3)
        key = task["task"]["key"]
        response = await signed_in.patch(
            f"{TASKS}/{key}",
            json={"title": "Renamed", "priority": 1},
            headers={"if-match": '"0"'},
        )
        assert response.status_code == 200, response.text
        assert response.json()["task"]["title"] == "Renamed"
        assert response.json()["task"]["version"] == 1
        events = (await signed_in.get(f"{TASKS}/{key}/events")).json()["items"]
        assert events[0]["type"] == "task.updated"
        assert events[0]["payload"]["changes"] == {
            "title": {"before": "Add health endpoint", "after": "Renamed"},
            "priority": {"before": 3, "after": 1},
        }

    async def test_noop_edit_emits_nothing(self, signed_in: httpx.AsyncClient) -> None:
        task = await create(signed_in)
        key = task["task"]["key"]
        response = await signed_in.patch(
            f"{TASKS}/{key}", json={"title": "Add health endpoint"}, headers={"if-match": "0"}
        )
        assert response.json()["task"]["version"] == 0
        assert await task_event_types(signed_in, key) == ["task.created"]

    @pytest.mark.parametrize(
        "body",
        [
            {"status": "completed"},
            {"assigned_agent_id": "x"},
            {"progress_pct": 100},
            {"key": "ACC-77"},
            {},
            {"title": None},
        ],
        ids=["status", "assignee", "progress", "key", "empty", "null"],
    )
    async def test_cannot_touch_protected_fields(
        self, signed_in: httpx.AsyncClient, body: dict[str, Any]
    ) -> None:
        task = await create(signed_in)
        response = await signed_in.patch(
            f"{TASKS}/{task['task']['key']}", json=body, headers={"if-match": "0"}
        )
        assert_problem(response, 422)

    async def test_requires_if_match(self, signed_in: httpx.AsyncClient) -> None:
        task = await create(signed_in)
        assert_problem(
            await signed_in.patch(f"{TASKS}/{task['task']['key']}", json={"priority": 1}), 428
        )

    async def test_bad_if_match_is_400(self, signed_in: httpx.AsyncClient) -> None:
        task = await create(signed_in)
        response = await signed_in.patch(
            f"{TASKS}/{task['task']['key']}", json={"priority": 1}, headers={"if-match": "abc"}
        )
        assert_problem(response, 400)


class TestAssign:
    async def assign(
        self, client: httpx.AsyncClient, key: str, agent: str | None
    ) -> httpx.Response:
        return await client.post(f"{TASKS}/{key}/assign", json={"agent_slug": agent})

    async def test_assign_reassign_unassign(self, signed_in: httpx.AsyncClient) -> None:
        key = (await create(signed_in))["task"]["key"]
        assert (await self.assign(signed_in, key, "architect")).json()["task"]["assignee_slug"] == (
            "architect"
        )
        assert (await self.assign(signed_in, key, "developer")).json()["task"]["assignee_name"] == (
            "Developer"
        )
        assert (await self.assign(signed_in, key, None)).json()["task"]["assignee_slug"] is None
        events = (await signed_in.get(f"{TASKS}/{key}/events")).json()["items"]
        payloads = [e["payload"] for e in reversed(events) if e["type"] == "task.assigned"]
        assert payloads == [
            {"from_agent": None, "to_agent": "architect"},
            {"from_agent": "architect", "to_agent": "developer"},
            {"from_agent": "developer", "to_agent": None},
        ]

    async def test_same_agent_is_a_noop(self, signed_in: httpx.AsyncClient) -> None:
        key = (await create(signed_in, assignee="developer"))["task"]["key"]
        response = await self.assign(signed_in, key, "developer")
        assert response.json()["task"]["version"] == 0
        assert (await task_event_types(signed_in, key)).count("task.assigned") == 1

    async def test_disabled_agent_rejected(
        self, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        from sqlalchemy import update

        from acc.db.models import Agent

        async with app.state.session_factory() as s, s.begin():
            await s.execute(update(Agent).where(Agent.slug == "designer").values(enabled=False))
        key = (await create(signed_in))["task"]["key"]
        assert_problem(await self.assign(signed_in, key, "designer"), 409)

    async def test_unknown_agent_404(self, signed_in: httpx.AsyncClient) -> None:
        key = (await create(signed_in))["task"]["key"]
        assert_problem(await self.assign(signed_in, key, "ghost"), 404)

    async def test_terminal_task_cannot_be_reassigned(self, signed_in: httpx.AsyncClient) -> None:
        key = (await create(signed_in))["task"]["key"]
        await move(signed_in, key, "cancelled", 0)
        assert_problem(await self.assign(signed_in, key, "developer"), 409)

    async def test_optional_if_match_is_enforced_when_given(
        self, signed_in: httpx.AsyncClient
    ) -> None:
        key = (await create(signed_in))["task"]["key"]
        response = await signed_in.post(
            f"{TASKS}/{key}/assign", json={"agent_slug": "developer"}, headers={"if-match": "7"}
        )
        assert_problem(response, 409)


class TestListAndAccess:
    async def test_filters(self, signed_in: httpx.AsyncClient) -> None:
        a = (await create(signed_in, title="Login page", assignee="designer"))["task"]["key"]
        b = (await create(signed_in, title="Rate limiter", priority=1))["task"]["key"]
        await move(signed_in, b, "planning", 0)

        async def keys(**params: Any) -> list[str]:
            return [t["key"] for t in (await signed_in.get(TASKS, params=params)).json()["items"]]

        assert await keys() == [b, a]  # priority 1 first
        assert await keys(status="planning") == [b]
        assert await keys(status=["planning", "backlog"]) == [b, a]
        assert await keys(agent="designer") == [a]
        assert await keys(q="login") == [a]
        assert await keys(q=b.lower()) == [b]

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/tasks"),
            ("post", "/api/v1/tasks"),
            ("get", "/api/v1/tasks/ACC-1"),
            ("get", "/api/v1/agents"),
            ("get", "/api/v1/events"),
            ("get", "/api/v1/overview"),
            ("get", "/api/v1/projects"),
        ],
    )
    async def test_every_endpoint_requires_a_session(
        self, client: httpx.AsyncClient, method: str, path: str
    ) -> None:
        assert_problem(await client.request(method, path, json={}), 401)

    async def test_mutations_require_same_origin(self, signed_in: httpx.AsyncClient) -> None:
        response = await signed_in.post(
            TASKS,
            json={"project": "acc", "title": "csrf"},
            headers={"origin": "https://evil.example"},
        )
        assert_problem(response, 403)
