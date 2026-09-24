import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, Depends, FastAPI

from acc import __version__
from acc.api.deps import require_same_origin
from acc.api.errors import register_error_handlers
from acc.api.middleware import RequestContextMiddleware
from acc.api.routers import agents, auth, events, health, overview, projects, tasks, ws
from acc.commands.maintenance import ensure_event_partitions
from acc.config import Settings, get_settings
from acc.db.migrations import alembic_head
from acc.db.session import SessionFactory, create_engine, create_session_factory
from acc.kv import create_redis
from acc.logging import configure_logging
from acc.realtime.hub import Hub
from acc.realtime.publisher import RedisPublisher
from acc.realtime.relay import relay_events

log = structlog.get_logger("acc.api")

PARTITION_CHECK_INTERVAL_S = 6 * 3600


async def _maintain_event_partitions(sf: SessionFactory) -> None:
    """Keep monthly `events` partitions created ahead of time, for long-running processes."""
    while True:
        try:
            created = await ensure_event_partitions(sf)
            if created:
                log.info("event_partitions_created", count=created)
        except Exception:
            log.exception("event_partition_maintenance_failed")
        await asyncio.sleep(PARTITION_CHECK_INTERVAL_S)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.json_logs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url.get_secret_value())
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = create_redis(settings.redis_url.get_secret_value())
        app.state.alembic_head = alembic_head(settings.alembic_ini)
        # Realtime: one Hub per process, fed by a background relay from Redis pub/sub
        # (acc.realtime.relay), fed in turn by every process's RedisPublisher — the
        # EventSink every command publishes through after its transaction commits.
        app.state.hub = Hub()
        app.state.event_sink = RedisPublisher(app.state.redis)
        maintenance = asyncio.create_task(_maintain_event_partitions(app.state.session_factory))
        relay = asyncio.create_task(relay_events(app.state.hub, app.state.redis))
        log.info("api_started", env=settings.env, version=__version__)
        try:
            yield
        finally:
            for task in (maintenance, relay):
                task.cancel()
            for task in (maintenance, relay):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await app.state.redis.aclose()
            await engine.dispose()
            log.info("api_stopped")

    expose_docs = not settings.is_production
    app = FastAPI(
        title="AI Command Center API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/openapi.json" if expose_docs else None,
        docs_url="/api/docs" if expose_docs else None,
        redoc_url=None,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    # /ws carries its own accept/close-based origin and auth check (see acc.api.routers.ws)
    # rather than the REST-only require_same_origin dependency, since a WebSocket
    # handshake has to be accepted before a rejection reason can be sent to the client.
    app.include_router(ws.router)
    v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_same_origin)])
    for module in (auth, overview, agents, projects, tasks, events):
        v1.include_router(module.router)
    app.include_router(v1)
    return app
