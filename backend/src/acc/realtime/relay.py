"""Bridges the Redis pub/sub channel (acc.realtime.publisher) to this process's Hub.

Runs as a background task for the lifetime of the API process (see acc.api.app). If
Redis is briefly unavailable, live push pauses — already-connected sockets keep their
connection and simply catch up via REST or the WebSocket `resume` op once it recovers.
"""

import asyncio
import json

import structlog
from redis.asyncio import Redis

from acc.realtime.hub import Hub
from acc.realtime.publisher import CHANNEL

log = structlog.get_logger("acc.realtime.relay")

RETRY_DELAY_S = 2.0


async def relay_events(hub: Hub, redis: Redis, channel: str = CHANNEL) -> None:
    """Runs until cancelled: forwards every published event on `channel` to `hub.dispatch`."""
    while True:
        try:
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe(channel)
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue  # (un)subscribe acks etc.
                    try:
                        event = json.loads(message["data"])
                    except ValueError:
                        log.warning("realtime_relay_bad_json")
                        continue
                    await hub.dispatch(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("realtime_relay_error")
            await asyncio.sleep(RETRY_DELAY_S)
