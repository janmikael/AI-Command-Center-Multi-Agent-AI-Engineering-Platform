"""In-process fan-out: routes events to the WebSocket connections subscribed to them.

Deliberately transport-agnostic (no FastAPI/Starlette import) and dependency-free beyond
the standard library, so it is fully unit-testable with a fake `Sender`. One Hub lives
per API process (see acc.api.app); each process has its own Redis subscription (see
acc.realtime.relay) and its own set of locally-connected sockets — exactly the "N API
processes fan out via Redis" design in ARCHITECTURE.md §1.2/§3.3.
"""

import itertools
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

log = structlog.get_logger("acc.realtime.hub")


class Sender(Protocol):
    async def send_json(self, data: Any) -> None: ...


def topics_for(event: dict[str, Any]) -> set[str]:
    """Which topics an event matches. "activity" always matches (the global feed)."""
    topics = {"activity"}
    if event.get("task_key"):
        topics.add(f"task:{event['task_key']}")
    if event.get("agent_slug"):
        topics.add(f"agent:{event['agent_slug']}")
    return topics


@dataclass
class _Connection:
    sender: Sender
    topics: set[str] = field(default_factory=set)


class Hub:
    def __init__(self) -> None:
        self._connections: dict[int, _Connection] = {}
        self._ids = itertools.count()

    @property
    def client_count(self) -> int:
        return len(self._connections)

    def register(self, sender: Sender) -> int:
        conn_id = next(self._ids)
        self._connections[conn_id] = _Connection(sender=sender)
        return conn_id

    def unregister(self, conn_id: int) -> None:
        self._connections.pop(conn_id, None)

    def subscribe(self, conn_id: int, topics: list[str]) -> None:
        if conn := self._connections.get(conn_id):
            conn.topics.update(t for t in topics if isinstance(t, str))

    def unsubscribe(self, conn_id: int, topics: list[str]) -> None:
        if conn := self._connections.get(conn_id):
            conn.topics.difference_update(topics)

    async def dispatch(self, event: dict[str, Any]) -> None:
        """Send `event` to every connection subscribed to at least one of its topics."""
        matching = topics_for(event)
        message = {"type": "event", "topics": sorted(matching), "event": event}
        # Snapshot the connection list: a failed send below must not mutate _connections
        # (and so the dict) while this loop is iterating over it.
        for conn_id, conn in list(self._connections.items()):
            if not (conn.topics & matching):
                continue
            try:
                await conn.sender.send_json(message)
            except Exception:
                log.debug("realtime_send_failed", conn_id=conn_id)
                self.unregister(conn_id)
