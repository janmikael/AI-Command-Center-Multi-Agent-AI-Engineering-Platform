"""RedisPublisher: the EventSink (acc.commands._base) that fans events out over Redis.

A regular, already-connected `Redis` client can issue PUBLISH concurrently with any other
command (rate limiting, etc.) — no dedicated connection is needed here, unlike the
subscribing side (acc.realtime.relay), which does need one.
"""

import json
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from redis.asyncio import Redis

from acc.domain.events import EventRecord

CHANNEL = "acc:events"


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def as_wire_dict(event: EventRecord) -> dict[str, Any]:
    """The event shape sent over Redis and, unchanged, over the WebSocket to clients."""
    return {
        "id": event.id,
        "seq": event.seq,
        "ts": event.ts,
        "type": event.type,
        "severity": event.severity,
        "actor": event.actor,
        "project_id": event.project_id,
        "task_id": event.task_id,
        "agent_id": event.agent_id,
        "run_id": event.run_id,
        "task_key": event.task_key,
        "agent_slug": event.agent_slug,
        "payload": event.payload,
    }


def serialize(event: EventRecord) -> str:
    return json.dumps(as_wire_dict(event), default=_json_default)


class RedisPublisher:
    """Implements the acc.commands._base.EventSink protocol."""

    def __init__(self, redis: Redis, channel: str = CHANNEL) -> None:
        self._redis = redis
        self._channel = channel

    async def publish(self, events: Sequence[EventRecord]) -> None:
        for event in events:
            await self._redis.publish(self._channel, serialize(event))
