"""Operator-facing /api/v1/runs: reads over acc.queries.runs, and /cancel over
acc.commands.runs.request_cancel. See acc/api/routers/runs.py."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from tests.integration.test_auth import assert_problem

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

RUNS = "/api/v1/runs"
TASKS = "/api/v1/tasks"


async def make_runnable(app: FastAPI, slug: str = "developer") -> None:
    """"stub", not "claude_agent_sdk" — see test_launcher_docker.py's identical helper.
    Nothing in this file exercises the SDK path itself (PLAN-PHASE-2-M4.md §9)."""
    from acc.db.models import Agent

    async with app.state.session_factory() as s, s.begin():
        await s.execute(update(Agent).where(Agent.slug == slug).values(adapter="stub"))


async def create_working_task(client: httpx.AsyncClient, app: FastAPI) -> dict[str, Any]:
    await make_runnable(app)
    created = await client.post(
        TASKS, json={"project": "acc", "title": "t", "assignee": "developer"}
    )
    task: dict[str, Any] = created.json()
    key, version = task["task"]["key"], task["task"]["version"]
    for to in ("planning", "working"):
        response = await client.post(
            f"{TASKS}/{key}/transition", json={"to": to}, headers={"if-match": f'"{version}"'}
        )
        assert response.status_code == 200, response.text
        version = response.json()["task"]["version"]
    result: dict[str, Any] = task["task"]
    return result


async def get_run_for_task(client: httpx.AsyncClient, task_key: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = (await client.get(RUNS)).json()["items"]
    (run,) = [r for r in items if r["task_key"] == task_key]
    return run


class TestList:
    async def test_lists_the_enqueued_run(self, signed_in: httpx.AsyncClient, app: FastAPI) -> None:
        task = await create_working_task(signed_in, app)
        run = await get_run_for_task(signed_in, task["key"])
        assert run["status"] == "queued"
        assert run["agent_slug"] == "developer"
        assert run["task_key"] == task["key"]

    async def test_filters_by_status(self, signed_in: httpx.AsyncClient, app: FastAPI) -> None:
        await create_working_task(signed_in, app)
        queued = (await signed_in.get(RUNS, params={"status": "queued"})).json()["items"]
        assert len(queued) == 1
        succeeded = (await signed_in.get(RUNS, params={"status": "succeeded"})).json()["items"]
        assert succeeded == []

    async def test_empty_when_no_runs(self, signed_in: httpx.AsyncClient) -> None:
        assert (await signed_in.get(RUNS)).json()["items"] == []


class TestGet:
    async def test_returns_the_run(self, signed_in: httpx.AsyncClient, app: FastAPI) -> None:
        task = await create_working_task(signed_in, app)
        run = await get_run_for_task(signed_in, task["key"])
        response = await signed_in.get(f"{RUNS}/{run['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == run["id"]

    async def test_unknown_run_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(
            await signed_in.get(f"{RUNS}/00000000-0000-0000-0000-000000000000"), 404
        )


class TestCancel:
    async def test_sets_cancel_requested(self, signed_in: httpx.AsyncClient, app: FastAPI) -> None:
        task = await create_working_task(signed_in, app)
        run = await get_run_for_task(signed_in, task["key"])
        response = await signed_in.post(f"{RUNS}/{run['id']}/cancel")
        assert response.status_code == 202, response.text
        assert response.json()["cancel_requested_at"] is not None

    async def test_unknown_run_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(
            await signed_in.post(f"{RUNS}/00000000-0000-0000-0000-000000000000/cancel"), 404
        )

    async def test_mutations_require_same_origin(
        self, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        task = await create_working_task(signed_in, app)
        run = await get_run_for_task(signed_in, task["key"])
        response = await signed_in.post(
            f"{RUNS}/{run['id']}/cancel", headers={"origin": "https://evil.example"}
        )
        assert_problem(response, 403)


class TestFileChanges:
    async def test_empty_when_none_reported(
        self, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        task = await create_working_task(signed_in, app)
        run = await get_run_for_task(signed_in, task["key"])
        response = await signed_in.get(f"{RUNS}/{run['id']}/file-changes")
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_unknown_run_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(
            await signed_in.get(f"{RUNS}/00000000-0000-0000-0000-000000000000/file-changes"), 404
        )


class TestToolCalls:
    async def test_empty_when_none_reported(
        self, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        task = await create_working_task(signed_in, app)
        run = await get_run_for_task(signed_in, task["key"])
        response = await signed_in.get(f"{RUNS}/{run['id']}/tool-calls")
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_unknown_run_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(
            await signed_in.get(f"{RUNS}/00000000-0000-0000-0000-000000000000/tool-calls"), 404
        )


class TestUsage:
    async def test_empty_when_none_reported(
        self, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        task = await create_working_task(signed_in, app)
        run = await get_run_for_task(signed_in, task["key"])
        response = await signed_in.get(f"{RUNS}/{run['id']}/usage")
        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_unknown_run_is_404(self, signed_in: httpx.AsyncClient) -> None:
        assert_problem(
            await signed_in.get(f"{RUNS}/00000000-0000-0000-0000-000000000000/usage"), 404
        )


class TestAccessControl:
    async def test_requires_a_session(self, client: httpx.AsyncClient) -> None:
        assert_problem(await client.get(RUNS), 401)
