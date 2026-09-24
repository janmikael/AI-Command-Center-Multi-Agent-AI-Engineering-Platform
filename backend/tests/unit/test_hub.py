"""acc.realtime.hub: pure topic routing, no network, no DB."""

from typing import Any

import pytest

from acc.realtime.hub import Hub, topics_for


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.received: list[dict[str, Any]] = []
        self.fail = fail

    async def send_json(self, data: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("client gone")
        self.received.append(data)


class TestTopicsFor:
    def test_bare_event_matches_only_activity(self) -> None:
        assert topics_for({"type": "agent.registered"}) == {"activity"}

    def test_task_event_also_matches_its_task(self) -> None:
        assert topics_for({"task_key": "ACC-1"}) == {"activity", "task:ACC-1"}

    def test_agent_event_also_matches_its_agent(self) -> None:
        assert topics_for({"agent_slug": "developer"}) == {"activity", "agent:developer"}

    def test_both_task_and_agent(self) -> None:
        assert topics_for({"task_key": "ACC-1", "agent_slug": "developer"}) == {
            "activity",
            "task:ACC-1",
            "agent:developer",
        }

    def test_falsy_fields_are_ignored(self) -> None:
        assert topics_for({"task_key": None, "agent_slug": ""}) == {"activity"}


class TestHub:
    @pytest.fixture
    def hub(self) -> Hub:
        return Hub()

    async def test_client_count(self, hub: Hub) -> None:
        assert hub.client_count == 0
        a, b = FakeSender(), FakeSender()
        id_a = hub.register(a)
        hub.register(b)
        assert hub.client_count == 2
        hub.unregister(id_a)
        assert hub.client_count == 1

    async def test_dispatch_only_reaches_matching_subscribers(self, hub: Hub) -> None:
        activity_only, task_only, unrelated = FakeSender(), FakeSender(), FakeSender()
        hub.subscribe(hub.register(activity_only), ["activity"])
        hub.subscribe(hub.register(task_only), ["task:ACC-1"])
        hub.register(unrelated)  # subscribed to nothing

        await hub.dispatch({"type": "task.created", "task_key": "ACC-1"})

        assert [m["event"]["task_key"] for m in activity_only.received] == ["ACC-1"]
        assert [m["event"]["task_key"] for m in task_only.received] == ["ACC-1"]
        assert unrelated.received == []

    async def test_dispatch_to_a_different_task_does_not_leak(self, hub: Hub) -> None:
        watcher = FakeSender()
        hub.subscribe(hub.register(watcher), ["task:ACC-1"])
        await hub.dispatch({"type": "task.created", "task_key": "ACC-2"})
        assert watcher.received == []

    async def test_message_envelope(self, hub: Hub) -> None:
        sender = FakeSender()
        hub.subscribe(hub.register(sender), ["activity"])
        await hub.dispatch({"type": "task.created", "task_key": "ACC-1", "seq": 9})
        [message] = sender.received
        assert message["type"] == "event"
        assert message["topics"] == ["activity", "task:ACC-1"]
        assert message["event"]["seq"] == 9

    async def test_unsubscribe_stops_delivery(self, hub: Hub) -> None:
        sender = FakeSender()
        conn_id = hub.register(sender)
        hub.subscribe(conn_id, ["activity"])
        hub.unsubscribe(conn_id, ["activity"])
        await hub.dispatch({"type": "x"})
        assert sender.received == []

    async def test_unregister_is_idempotent(self, hub: Hub) -> None:
        conn_id = hub.register(FakeSender())
        hub.unregister(conn_id)
        hub.unregister(conn_id)  # must not raise
        assert hub.client_count == 0

    async def test_operations_on_an_unknown_connection_id_are_harmless(self, hub: Hub) -> None:
        hub.subscribe(999, ["activity"])  # must not raise
        hub.unsubscribe(999, ["activity"])

    async def test_a_failing_subscriber_is_dropped_but_others_still_receive(self, hub: Hub) -> None:
        broken, healthy = FakeSender(fail=True), FakeSender()
        broken_id = hub.register(broken)
        hub.subscribe(broken_id, ["activity"])
        hub.subscribe(hub.register(healthy), ["activity"])

        await hub.dispatch({"type": "x"})

        assert healthy.received  # unaffected by the other connection's failure
        assert hub.client_count == 1  # the broken one was dropped
