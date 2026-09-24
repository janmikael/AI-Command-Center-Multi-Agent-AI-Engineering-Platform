"""WebSocket realtime: auth, origin check, topic filtering, resume, resync, ping/pong.

Uses two independent FastAPI instances against the same Postgres/Redis — `app`/`client`
(via `signed_in`) and `ws_app`/`ws_client` (see conftest.py) — to exercise the real
multi-process fan-out path end to end: a REST write on one process publishes to Redis,
and the *other* process's own Hub, fed by its own relay task, is what delivers it to a
WebSocket connected there. Not an in-process shortcut.
"""

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import TEST_ORIGIN, Credentials, login_ws_client

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]

TASKS = "/api/v1/tasks"


async def create_task(client: httpx.AsyncClient, title: str) -> dict[str, object]:
    response = await client.post(TASKS, json={"project": "acc", "title": title})
    assert response.status_code == 201, response.text
    result: dict[str, object] = response.json()
    return result


class TestAuthAndOrigin:
    def test_unauthenticated_connection_is_closed(self, ws_client: TestClient) -> None:
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws,
        ):
            ws.receive_text()
        assert excinfo.value.code == 4401

    def test_wrong_origin_is_closed(self, ws_client: TestClient, operator: Credentials) -> None:
        login_ws_client(ws_client, operator)
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            ws_client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws,
        ):
            ws.receive_text()
        assert excinfo.value.code == 4403

    def test_missing_origin_is_closed(self, ws_client: TestClient, operator: Credentials) -> None:
        login_ws_client(ws_client, operator)
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            ws_client.websocket_connect("/ws", headers={}) as ws,
        ):
            ws.receive_text()
        assert excinfo.value.code == 4403

    def test_authenticated_connection_gets_hello(
        self, ws_client: TestClient, operator: Credentials
    ) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert isinstance(hello["server_seq"], int)

    def test_revoked_session_cannot_connect(
        self, ws_client: TestClient, operator: Credentials
    ) -> None:
        login_ws_client(ws_client, operator)
        logout = ws_client.post("/api/v1/auth/logout", headers={"origin": TEST_ORIGIN})
        assert logout.status_code == 204
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws,
        ):
            ws.receive_text()
        assert excinfo.value.code == 4401


class TestPingAndErrors:
    def test_ping_gets_pong(self, ws_client: TestClient, operator: Credentials) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_malformed_message_gets_an_error_but_connection_stays_open(
        self, ws_client: TestClient, operator: Credentials
    ) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_text("not json")
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"op": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_unknown_op_gets_an_error(self, ws_client: TestClient, operator: Credentials) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "not-a-real-op"})
            assert ws.receive_json()["type"] == "error"


class TestFanOut:
    async def test_activity_subscriber_receives_a_real_commit_from_the_other_process(
        self, ws_client: TestClient, operator: Credentials, signed_in: httpx.AsyncClient
    ) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "subscribe", "topics": ["activity"]})

            task = await create_task(signed_in, "Realtime check")

            message = ws.receive_json()
        assert message["type"] == "event"
        assert message["topics"] == ["activity", f"task:{task['task']['key']}"]  # type: ignore[index]
        assert message["event"]["type"] == "task.created"
        assert message["event"]["task_key"] == task["task"]["key"]  # type: ignore[index]
        assert message["event"]["actor"].startswith("user:")
        assert message["event"]["payload"]["title"] == "Realtime check"

    async def test_task_topic_only_delivers_that_task(
        self, ws_client: TestClient, operator: Credentials, signed_in: httpx.AsyncClient
    ) -> None:
        login_ws_client(ws_client, operator)
        first = await create_task(signed_in, "First")
        first_key = first["task"]["key"]  # type: ignore[index]

        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "subscribe", "topics": [f"task:{first_key}"]})

            await create_task(signed_in, "Second (unrelated)")
            await signed_in.patch(
                f"{TASKS}/{first_key}",
                json={"title": "First, renamed"},
                headers={"if-match": '"0"'},
            )

            message = ws.receive_json()
        # Only the event for `first_key` arrives — "Second"'s creation is never delivered.
        assert message["event"]["task_key"] == first_key
        assert message["event"]["type"] == "task.updated"

    async def test_agent_topic_filters_by_assigned_agent(
        self, ws_client: TestClient, operator: Credentials, signed_in: httpx.AsyncClient
    ) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "subscribe", "topics": ["agent:developer"]})

            await signed_in.post(
                TASKS, json={"project": "acc", "title": "For designer", "assignee": "designer"}
            )
            await signed_in.post(
                TASKS, json={"project": "acc", "title": "For developer", "assignee": "developer"}
            )

            message = ws.receive_json()
        assert message["event"]["agent_slug"] == "developer"
        assert message["event"]["payload"]["to_agent"] == "developer"

    async def test_unsubscribe_stops_delivery(
        self, ws_client: TestClient, operator: Credentials, signed_in: httpx.AsyncClient
    ) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "subscribe", "topics": ["activity"]})
            ws.send_json({"op": "unsubscribe", "topics": ["activity"]})

            await create_task(signed_in, "Should not arrive")
            ws.send_json({"op": "ping"})

            # If unsubscribe had failed, the task.created event would arrive before this
            # pong (it was published first); it must not.
            assert ws.receive_json() == {"type": "pong"}


class TestResume:
    async def test_resume_replays_in_order_from_postgres(
        self, ws_client: TestClient, operator: Credentials, signed_in: httpx.AsyncClient
    ) -> None:
        """A small after_seq means `after_seq - 50` clamps to 0 (the overlap window), so
        this necessarily replays from the very start of history, not just our two tasks —
        that's the intended dedup-safety behavior (see RESUME_OVERLAP), not a bug. What
        must hold regardless is: everything replayed is in ascending seq order, and our
        two new events appear, in the order they were created."""
        login_ws_client(ws_client, operator)
        await create_task(signed_in, "Resume-R1")
        await create_task(signed_in, "Resume-R2")

        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "resume", "after_seq": 0})
            seqs: list[int] = []
            titles: dict[str, int] = {}
            for _ in range(50):  # generous bound: seed (4 events) + 2 tasks is well under this
                message = ws.receive_json()
                seqs.append(message["event"]["seq"])
                title = message["event"]["payload"].get("title")
                if title:
                    titles[title] = message["event"]["seq"]
                if title == "Resume-R2":
                    break
        assert seqs == sorted(seqs)
        assert titles["Resume-R1"] < titles["Resume-R2"]

    async def test_resume_overlap_replays_the_last_50_events_before_the_requested_point(
        self, ws_client: TestClient, operator: Credentials, signed_in: httpx.AsyncClient
    ) -> None:
        import asyncio

        login_ws_client(ws_client, operator)
        # Comfortably more than RESUME_OVERLAP (50), so the window's effect is observable.
        await asyncio.gather(*(create_task(signed_in, f"filler{i}") for i in range(60)))
        latest = (await signed_in.get("/api/v1/events", params={"limit": 1})).json()["items"][0]
        after_seq = latest["seq"]

        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "resume", "after_seq": after_seq})
            first = ws.receive_json()
        assert first["event"]["seq"] == after_seq - 50 + 1

    async def test_resume_needs_no_live_events_to_work(
        self, ws_client: TestClient, operator: Credentials
    ) -> None:
        """Resume must work purely from Postgres — not depend on the Redis/Hub live path."""
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "resume", "after_seq": 999_999_999})
            ws.send_json({"op": "ping"})
            assert ws.receive_json() == {"type": "pong"}  # no events; nothing else arrives

    async def test_resync_when_the_gap_is_too_large(
        self,
        ws_client: TestClient,
        operator: Credentials,
        signed_in: httpx.AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import acc.api.routers.ws as ws_module

        monkeypatch.setattr(ws_module, "RESUME_GAP_LIMIT", 2)
        login_ws_client(ws_client, operator)
        for i in range(3):
            await create_task(signed_in, f"gap{i}")

        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "resume", "after_seq": 0})
            assert ws.receive_json() == {"type": "resync"}

    async def test_bad_after_seq_gets_an_error(
        self, ws_client: TestClient, operator: Credentials
    ) -> None:
        login_ws_client(ws_client, operator)
        with ws_client.websocket_connect("/ws", headers={"origin": TEST_ORIGIN}) as ws:
            ws.receive_json()  # hello
            ws.send_json({"op": "resume"})  # missing after_seq
            assert ws.receive_json()["type"] == "error"
