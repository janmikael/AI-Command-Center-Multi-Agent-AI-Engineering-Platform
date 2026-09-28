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
    """"stub", not "claude_agent_sdk" — see test_launcher_docker.py's identical helper.
    Nothing in this file exercises the SDK path itself (PLAN-PHASE-2-M4.md §9)."""
    from acc.db.models import Agent

    async with app.state.session_factory() as s, s.begin():
        await s.execute(update(Agent).where(Agent.slug == slug).values(adapter="stub"))


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
        assert run["error"]["reason"] == "it broke"

    async def test_fail_details_are_merged_into_the_error_and_surfaced(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        """PLAN-PHASE-2-M4.2.md §6: structured failure classification, not just a flat
        string — and PLAN-PHASE-2-M4.2.md §2's agent_runs.error surfaced through the
        operator API for the first time (an existing column that was never exposed
        before this milestone)."""
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/fail",
            json={
                "reason": "max turns reached",
                "details": {"category": "max_turns", "stop_reason": "max_turns"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        run = (await signed_in.get(f"/api/v1/runs/{run_id}")).json()
        assert run["error"] == {
            "reason": "max turns reached", "category": "max_turns", "stop_reason": "max_turns",
        }

    async def test_a_details_reason_key_never_overrides_the_explicit_reason(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        await client.post(
            f"/internal/runs/{run_id}/fail",
            json={"reason": "the real reason", "details": {"reason": "a sneaky override"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        run = (await signed_in.get(f"/api/v1/runs/{run_id}")).json()
        assert run["error"]["reason"] == "the real reason"

    async def test_succeeded_run_has_no_error(
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
        run = (await signed_in.get(f"/api/v1/runs/{run_id}")).json()
        assert run["error"] is None

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


class TestFileChanges:
    """What the container's own /file-changes call would do — exercised directly here,
    not by waiting on a real container to reach a live api (PLAN-PHASE-2-M3.md;
    tests/integration/test_launcher_git.py's module docstring explains the split)."""

    async def test_records_the_reported_changes(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/file-changes",
            json={
                "changes": [
                    {
                        "path": "ACC_SMOKE_TEST_MARKER.txt",
                        "change_type": "added",
                        "additions": 1,
                        "deletions": 0,
                    }
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

        changes = (await signed_in.get(f"/api/v1/runs/{run_id}/file-changes")).json()["items"]
        assert len(changes) == 1
        assert changes[0]["path"] == "ACC_SMOKE_TEST_MARKER.txt"
        assert changes[0]["change_type"] == "added"
        assert changes[0]["additions"] == 1
        assert changes[0]["deletions"] == 0

    async def test_rejects_an_invalid_change_type(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/file-changes",
            json={"changes": [{"path": "x.py", "change_type": "obliterated"}]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_rejects_an_empty_batch(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/file-changes",
            json={"changes": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    async def test_requires_authentication(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, _ = await create_run(signed_in, app)
        response = await client.post(
            f"/internal/runs/{run_id}/file-changes",
            json={"changes": [{"path": "x.py", "change_type": "added"}]},
        )
        assert response.status_code == 401


class TestContext:
    """GET /internal/runs/{id}/context: PLAN-PHASE-2-M4.md §3's four-layer render,
    fetched by the Claude Agent SDK adapter (runner/agent_sdk.py) at run start."""

    async def test_returns_the_rendered_context(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        """create_run's own make_runnable helper sets adapter="stub" (M4 gave the literal
        "claude_agent_sdk" real dispatch meaning elsewhere — see test_launcher_docker.py),
        so this test sets the real value itself to exercise the actual per-adapter role
        prompt (acc.queries.context.AGENT_ROLE_PROMPTS), not the "stub"/DB-role fallback
        already covered by tests/unit/test_context_rendering.py."""
        from acc.db.models import Agent

        run_id, agent_id = await create_run(signed_in, app)
        async with app.state.session_factory() as s, s.begin():
            await s.execute(
                update(Agent).where(Agent.slug == "developer").values(adapter="claude_agent_sdk")
            )
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.get(
            f"/internal/runs/{run_id}/context", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "Developer agent" in body["system_prompt"]
        assert "Task: t" in body["task_prompt"]  # the task's title, "t" (see create_run above)
        assert isinstance(body["governance_rules"], list)
        assert len(body["governance_rules"]) > 0  # the Phase 1.5 seed rules are enabled
        assert {"match", "applies_to", "priority", "effect"} <= body["governance_rules"][0].keys()
        assert body["max_turns"] > 0
        assert float(body["max_cost_usd"]) > 0

    async def test_requires_authentication(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, _ = await create_run(signed_in, app)
        assert (await client.get(f"/internal/runs/{run_id}/context")).status_code == 401

    async def test_token_for_a_different_run_is_403(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        token = token_for(str(uuid.uuid4()), agent_id, RUNNER_SECRET)
        response = await client.get(
            f"/internal/runs/{run_id}/context", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


class TestMessages:
    async def test_records_an_agent_message_event(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/messages",
            json={"text": "Looking at the failing test now."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        events = (await signed_in.get("/api/v1/events", params={"type": "agent.message"})).json()
        assert events["items"][0]["payload"]["text"] == "Looking at the failing test now."
        assert events["items"][0]["actor"] == "agent:developer"


class TestToolCalls:
    async def test_records_an_allowed_call_with_no_event(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/tool-calls",
            json={"tool": "Read", "arguments": {"file_path": "a.py"}, "allowed": True},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        events = (await signed_in.get("/api/v1/events", params={"type": "tool_call.denied"})).json()
        assert events["items"] == []

    async def test_records_a_denied_call_with_an_event(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        # A real seeded rule's id: tool_calls.rule_id is a genuine foreign key
        # (PLAN-PHASE-2-M4.2.md §3), so a fabricated uuid correctly 500s here.
        rules = (await signed_in.get("/api/v1/governance-rules")).json()["items"]
        rule_id = rules[0]["id"]
        response = await client.post(
            f"/internal/runs/{run_id}/tool-calls",
            json={
                "tool": "Bash",
                "arguments": {"command": "rm -rf /"},
                "allowed": False,
                "denial_reason": "Denied by policy: no rm -rf",
                "rule_id": rule_id,
                "blocked_by_effect": "deny",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        events = (await signed_in.get("/api/v1/events", params={"type": "tool_call.denied"})).json()
        assert events["items"][0]["payload"]["tool"] == "Bash"
        assert events["items"][0]["payload"]["reason"] == "Denied by policy: no rm -rf"
        assert events["items"][0]["payload"]["rule_id"] == rule_id
        assert events["items"][0]["payload"]["effect"] == "deny"

        calls = (await signed_in.get(f"/api/v1/runs/{run_id}/tool-calls")).json()["items"]
        assert calls[0]["blocked_by_effect"] == "deny"
        assert calls[0]["rule_id"] == rule_id


class TestToolCallLifecycle:
    """POST .../tool-calls/{tool_use_id}/complete: the PostToolUse-hook half
    (PLAN-PHASE-2-M4.2.md §1)."""

    async def test_completes_an_allowed_call(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        await client.post(
            f"/internal/runs/{run_id}/tool-calls",
            json={
                "tool": "Bash", "arguments": {"command": "ls"}, "allowed": True,
                "tool_use_id": "tu_1",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        response = await client.post(
            f"/internal/runs/{run_id}/tool-calls/tu_1/complete",
            json={"duration_ms": 42, "exit_code": 0, "result_excerpt": "file1\nfile2"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

        calls = (await signed_in.get(f"/api/v1/runs/{run_id}/tool-calls")).json()["items"]
        assert calls[0]["duration_ms"] == 42
        assert calls[0]["exit_code"] == 0
        assert calls[0]["result_excerpt"] == "file1\nfile2"
        assert calls[0]["ended_at"] is not None

    async def test_unknown_tool_use_id_is_404(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/tool-calls/never-existed/complete",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert_problem(response, 404)

    async def test_requires_authentication(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, _ = await create_run(signed_in, app)
        response = await client.post(
            f"/internal/runs/{run_id}/tool-calls/tu_1/complete", json={}
        )
        assert response.status_code == 401


class TestUsage:
    async def test_records_usage_and_updates_the_run_totals(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/usage",
            json={
                "model": "claude-sonnet-5",
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_tokens": 50,
                "cache_write_tokens": 10,
                "cost_usd": "0.123456",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

        run = (await signed_in.get(f"/api/v1/runs/{run_id}")).json()
        assert run["input_tokens"] == 1000
        assert run["output_tokens"] == 200
        assert run["cache_read_tokens"] == 50
        assert run["cache_write_tokens"] == 10
        assert float(run["cost_usd"]) == 0.123456

        events = (await signed_in.get("/api/v1/events", params={"type": "usage.recorded"})).json()
        assert events["items"][0]["payload"]["model"] == "claude-sonnet-5"

    async def test_records_an_estimated_partial_usage_report(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        """PLAN-PHASE-2-M4.2.md §5: what a cancelled/crashed session reports — tokens
        without a provider-confirmed cost figure."""
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/usage",
            json={
                "model": "claude-sonnet-5", "input_tokens": 500, "output_tokens": 50,
                "cost_usd": "0", "cost_source": "estimated",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

        records = (await signed_in.get(f"/api/v1/runs/{run_id}/usage")).json()["items"]
        assert records[0]["cost_source"] == "estimated"
        assert records[0]["input_tokens"] == 500

    async def test_rejects_an_invalid_cost_source(
        self, client: httpx.AsyncClient, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        run_id, agent_id = await create_run(signed_in, app)
        await reach_running(client, app, run_id, agent_id)
        token = token_for(run_id, agent_id, RUNNER_SECRET)
        response = await client.post(
            f"/internal/runs/{run_id}/usage",
            json={
                "model": "claude-sonnet-5", "input_tokens": 1, "output_tokens": 1,
                "cost_usd": "0", "cost_source": "made_up",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422
