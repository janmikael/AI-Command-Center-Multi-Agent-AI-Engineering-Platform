"""The job scheduler (ADR-2): claims acc.db.models.Job rows with SELECT ... FOR UPDATE
SKIP LOCKED, so more than one scheduler process can run safely with no leader election —
not needed yet at Phase 2's scale (one operator, low concurrency); the SKIP LOCKED design
just means adding workers later is a deployment change, not a redesign.

M1 scope: the generic claim/complete/fail loop only. Which handler runs for which job
kind is supplied by the caller (acc.orchestrator.main, added in M2) — this module has no
opinion about containers, launching, or Docker, and imports none of it.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update

from acc.db.models import Job
from acc.db.session import SessionFactory, transaction
from acc.ids import utcnow

MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int


Handler = Callable[[ClaimedJob], Awaitable[None]]


async def claim_next_job(sf: SessionFactory, *, worker_id: str) -> ClaimedJob | None:
    """Claims and locks exactly one due, pending job, or None if there isn't one. Two
    concurrent callers racing this never get the same row — SKIP LOCKED gives the second
    one either a different row or None, verified under real concurrency in tests."""
    async with transaction(sf) as session:
        row = await session.scalar(
            select(Job)
            .where(Job.status == "pending", Job.run_after <= utcnow())
            .order_by(Job.run_after)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.status = "running"
        row.locked_by = worker_id
        row.locked_at = utcnow()
        row.attempts += 1
        return ClaimedJob(id=row.id, kind=row.kind, payload=row.payload, attempts=row.attempts)


async def complete_job(sf: SessionFactory, job_id: uuid.UUID) -> None:
    async with transaction(sf) as session:
        await session.execute(update(Job).where(Job.id == job_id).values(status="done"))


async def fail_job(sf: SessionFactory, job_id: uuid.UUID, *, error: str, attempts: int) -> None:
    """Retries (back to `pending`, so claim_next_job can pick it up again later) unless
    `attempts` has reached MAX_ATTEMPTS, in which case the job is dead-lettered."""
    status = "dead" if attempts >= MAX_ATTEMPTS else "pending"
    async with transaction(sf) as session:
        await session.execute(
            update(Job).where(Job.id == job_id).values(status=status, last_error=error)
        )


async def run_once(sf: SessionFactory, handlers: dict[str, Handler], *, worker_id: str) -> bool:
    """Claims and processes at most one job.

    Returns True if a job was found (whether or not it succeeded), False if the queue was
    empty — callers use this to poll tightly while busy and back off while idle.
    """
    job = await claim_next_job(sf, worker_id=worker_id)
    if job is None:
        return False
    handler = handlers.get(job.kind)
    if handler is None:
        await fail_job(
            sf, job.id, error=f"no handler registered for job kind {job.kind!r}",
            attempts=job.attempts,
        )
        return True
    try:
        await handler(job)
    except Exception as exc:
        await fail_job(sf, job.id, error=str(exc), attempts=job.attempts)
        return True
    await complete_job(sf, job.id)
    return True
