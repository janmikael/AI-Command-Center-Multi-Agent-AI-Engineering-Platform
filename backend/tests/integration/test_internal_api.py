"""The runner-facing /internal/runs/{id} API: auth (RunClaimsDep) and the heartbeat/
complete/fail round trip. See acc/api/internal/."""

import time
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from acc.commands import runs as run_commands
from acc.security import mint_run_token
from tests.integration.test_auth import assert_problem

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

TASKS = "/api/v1/tasks"


async def make_runnable(app: FastAPI, slug: str = "developer") -> None:
    from acc.db.models import Agent

    async with app.state.session_factory() as s, s.begin():
        await s.execute(update(Agent).where(Agent.slug == slug).values(adapter="claude_agent_sdk"))


async def create_run(signed_in: httpx.AsyncClient, app: FastAPI) -> tuple[str, str]:
    """Returns (run_id, agent_id) for a freshly QUEUED run."""
    from acc.db.models import Agent, AgentRun

    await make_runnable(app)
    created = await signed_in.post(
        TASKS, json={"project": "acc", "title": "t", "assignee": "developer"}
    )
    task: dict[str, Any] = created.json()
    key, version = task["task"]["key"], task["task"]["version"]
    for to in ("planning", "working"):
        response = await signed_in.post(
            f"{TASKS}/{key}/transition", json={"to": to}, headers={"if-match": f'"{version}"'}
        )
        version = response.json()["task"]["version"]
    async with app.state.session_factory() as s:
        run = await s.scalar(select(AgentRun))
        agent_id = await s.scalar(select(Agent.id).where(Agent.slug == "developer"))
    assert run is not None
    assert agent_id is not None
    return str(run.id), str(agent_id)


async def start_run(app: FastAPI, run_id: str) -> None:
    """Simulates the orchestrator's launcher (not built yet in this milestone's test
    setup): QUEUED -> STARTING, as mark_run_starting would do right after creating the
    container. complete/fail both require the run to have passed through RUNNING first
    (RUN_TRANSITIONS has no direct STARTING/QUEUED -> SUCCEEDED edge), so this is the
    realistic precondition every complete/fail test below needs, not a shortcut."""
    await run_commands.mark_run_starting(
        app.state.session_factory,
        None,
        actor="system:orchestrator",
        run_id=uuid.UUID(run_id),
        container_id="c-test",
    )


async def reach_running(
    client: httpx.AsyncClient, app: FastAPI, run_id: str, agent_id: str
) -> None:
    """QUEUED -> STARTING (orchestrator, simulated) -> RUNNING (the runner's own first
    heartbeat, through the real endpoint under test)."""
    await start_run(app, run_id)
    token = token_for(run_id, agent_id, RUNNER_SECRET)
    response = await client.post(
        f"/internal/runs/{run_id}/heartbeat", json={}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text


def token_for(run_id: str, agent_id: str, secret: str, *, ttl: int = 3600) -> str:
    return mint_run_token(
        run_id=uuid.UUID(run_id),
        agent_id=uuid.UUID(agent_id),
        expires_at=int(time.time()) + ttl,
        secret=secret,
    )


RUNNER_SECRET = "test-runner-secret-" + "x" * 32  # matches tests/conftest.py's settings fixture


class TestAuth:
    async def test_missing_authorization_is_401(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, _ = await create_run(signed_in, app)
        assert_problem(await client.post(f"/internal/runs/{run_id}/heartbeat", json={}), 401)

    async def test_malformed_authorization_is_401(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, _ = await create_run(signed_in, app)
        response = await client.post(
            f"/internal/runs/{run_id}/heartbeat",
            json={},
            headers={"Authorization": "NotBearer x"},
        )
        assert_problem(response, 401)

    async def test_tampered_token_is_401(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        token = token_for(run_id, agent_id, RUNNER_SECRET)[:-4] + "0000"
        response = await client.post(
            f"/internal/runs/{run_id}/heartbeat",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert_problem(response, 401)

    async def test_expired_token_is_401(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        token = token_for(run_id, agent_id, RUNNER_SECRET, ttl=-1)
        response = await client.post(
            f"/internal/runs/{run_id}/heartbeat",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert_problem(response, 401)

    async def test_token_for_a_different_run_is_403(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        other_run_id = str(uuid.uuid4())
        token = token_for(other_run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/heartbeat",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert_problem(response, 403)

    async def test_valid_token_is_accepted(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/heartbeat",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text

    async def test_unreachable_through_the_public_origin_path_convention(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        """Defense in depth lives in the Caddyfile (infra/caddy/Caddyfile), which this
        in-process test client bypasses entirely — asserted separately as a live check
        (PLAN-PHASE-2-M2.md §5). This test instead confirms the FastAPI app itself still
        requires the token even though it's the only remaining boundary here."""
        run_id, _ = await create_run(signed_in, app)
        assert_problem(await client.get(f"/internal/runs/{run_id}/heartbeat"), 405)


class TestHeartbeat:
    async def test_first_heartbeat_flips_to_running(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        from acc.db.models import AgentRun

        run_id, agent_id = await create_run(signed_in, app)
        await start_run(app, run_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/heartbeat",
            json={"current_step": "starting up"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json() == {"cancel_requested": False}
        async with app.state.session_factory() as s:
            run = await s.get(AgentRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == "running"
        assert run.current_step == "starting up"
        assert run.last_heartbeat_at is not None

    async def test_reports_cancel_requested(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await signed_in.post(f"/api/v1/runs/{run_id}/cancel")
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/heartbeat",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.json() == {"cancel_requested": True}


class TestCompleteAndFail:
    async def test_complete_moves_the_run_and_task(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/complete",
            json={"result_summary": "did the thing"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        run = (await signed_in.get(f"/api/v1/runs/{run_id}")).json()
        assert run["status"] == "succeeded"
        assert run["result_summary"] == "did the thing"

    async def test_fail_moves_the_run_and_task(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/fail",
            json={"reason": "it broke"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        run = (await signed_in.get(f"/api/v1/runs/{run_id}")).json()
        assert run["status"] == "failed"

    async def test_events_are_attributed_to_the_agent_not_the_orchestrator(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        await client.post(
            f"/internal/runs/{run_id}/complete",
            json={"result_summary": "ok"},
            headers={"Authorization": f"Bearer {token}"},
        )
        events = (await signed_in.get("/api/v1/events", params={"type": "run.succeeded"})).json()
        assert events["items"][0]["actor"] == "agent:developer"
