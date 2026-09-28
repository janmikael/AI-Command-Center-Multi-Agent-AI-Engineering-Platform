"""The orchestrator process entrypoint: `python -m acc.orchestrator.main`. Runs two
independent loops — the job scheduler (M1) with M2's start_run/cancel_run handlers
registered, and the container-exit reconciliation pass (§0.6, not a job) — until
SIGTERM/SIGINT.
"""

import asyncio
import contextlib
import os
import signal
import socket
from datetime import timedelta

import aiodocker
import structlog

from acc.commands._base import EventSink
from acc.config import Settings, get_settings
from acc.db.session import SessionFactory, create_engine, create_session_factory
from acc.kv import create_redis
from acc.logging import configure_logging
from acc.orchestrator import launcher
from acc.orchestrator.scheduler import ClaimedJob, Handler, run_once
from acc.realtime.publisher import RedisPublisher

log = structlog.get_logger("acc.orchestrator")

IDLE_POLL_INTERVAL_S = 2.0
RECONCILE_INTERVAL_S = 15.0


def _handlers(
    sf: SessionFactory, sink: EventSink | None, settings: Settings, docker: aiodocker.Docker
) -> dict[str, Handler]:
    async def start_run_handler(job: ClaimedJob) -> None:
        await launcher.handle_start_run(sf, sink, settings, docker, job)

    async def cancel_run_handler(job: ClaimedJob) -> None:
        await launcher.handle_cancel_run(sf, sink, docker, job)

    return {"start_run": start_run_handler, "cancel_run": cancel_run_handler}


async def _job_loop(
    sf: SessionFactory, handlers: dict[str, Handler], *, worker_id: str
) -> None:
    while True:
        try:
            found = await run_once(sf, handlers, worker_id=worker_id)
        except Exception:
            log.exception("job_loop_tick_failed")
            found = False
        await asyncio.sleep(0 if found else IDLE_POLL_INTERVAL_S)


async def _reconcile_loop(
    sf: SessionFactory,
    sink: EventSink | None,
    docker: aiodocker.Docker,
    *,
    max_wall_clock: timedelta,
) -> None:
    while True:
        try:
            await launcher.reconcile_active_runs(sf, sink, docker, max_wall_clock=max_wall_clock)
            await launcher.cleanup_finished_containers(sf, docker)
        except Exception:
            log.exception("reconcile_loop_tick_failed")
        await asyncio.sleep(RECONCILE_INTERVAL_S)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.json_logs)
    engine = create_engine(settings.database_url.get_secret_value())
    sf = create_session_factory(engine)
    redis = create_redis(settings.redis_url.get_secret_value())
    sink: EventSink = RedisPublisher(redis)
    docker = aiodocker.Docker(url=settings.docker_proxy_url)
    worker_id = f"orchestrator-{socket.gethostname()}-{os.getpid()}"
    handlers = _handlers(sf, sink, settings, docker)

    log.info("orchestrator_started", worker_id=worker_id)
    job_task = asyncio.create_task(_job_loop(sf, handlers, worker_id=worker_id))
    max_wall_clock = timedelta(minutes=settings.run_max_wall_clock_minutes)
    reconcile_task = asyncio.create_task(
        _reconcile_loop(sf, sink, docker, max_wall_clock=max_wall_clock)
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("orchestrator_stopping")
    for task in (job_task, reconcile_task):
        task.cancel()
    for task in (job_task, reconcile_task):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await docker.close()
    await redis.aclose()
    await engine.dispose()
    log.info("orchestrator_stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
