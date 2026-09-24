"""Redis client factory. Redis holds only transient data (rate limits, pub/sub), never state."""

from redis.asyncio import Redis


def create_redis(url: str) -> "Redis":
    return Redis.from_url(url, decode_responses=True, health_check_interval=30)
