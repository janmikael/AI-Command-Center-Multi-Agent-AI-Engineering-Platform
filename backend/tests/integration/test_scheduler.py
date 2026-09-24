"""acc.orchestrator.scheduler: the SKIP LOCKED job queue, exercised under real
concurrency — the whole point of ADR-2 is safe multi-worker claiming, so this has to be
tested against real Postgres locking, not a mock."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from acc.db.models import Job
from acc.db.session import SessionFactory, transaction
from acc.orchestrator.scheduler import ClaimedJob, claim_next_job, complete_job, fail_job, run_once

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("seeded")]


async def add_job(
    sf: SessionFactory, kind: str = "start_run", payload: dict[str, Any] | None = None
) -> uuid.UUID:
    job_id = uuid.uuid4()
    async with transaction(sf) as s:
        s.add(Job(id=job_id, kind=kind, payload=payload or {}))
    return job_id


async def get_job(sf: SessionFactory, job_id: uuid.UUID) -> Job:
    async with sf() as s:
        job = await s.get(Job, job_id)
    assert job is not None
    return job


class TestClaiming:
    async def test_claims_a_pending_due_job(self, sf: SessionFactory) -> None:
        job_id = await add_job(sf)
        claimed = await claim_next_job(sf, worker_id="w1")
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.attempts == 1
        job = await get_job(sf, job_id)
        assert job.status == "running"
        assert job.locked_by == "w1"

    async def test_empty_queue_returns_none(self, sf: SessionFactory) -> None:
        assert await claim_next_job(sf, worker_id="w1") is None

    async def test_does_not_reclaim_an_already_running_job(self, sf: SessionFactory) -> None:
        await add_job(sf)
        first = await claim_next_job(sf, worker_id="w1")
        assert first is not None
        assert await claim_next_job(sf, worker_id="w2") is None

    async def test_oldest_due_job_claimed_first(self, sf: SessionFactory) -> None:
        older = await add_job(sf)
        await add_job(sf)
        claimed = await claim_next_job(sf, worker_id="w1")
        assert claimed is not None
        assert claimed.id == older

    async def test_concurrent_claims_never_get_the_same_job(self, sf: SessionFactory) -> None:
        job_ids = {await add_job(sf) for _ in range(10)}
        results = await asyncio.gather(
            *(claim_next_job(sf, worker_id=f"w{i}") for i in range(10))
        )
        claimed_ids = [r.id for r in results if r is not None]
        assert len(claimed_ids) == 10  # every job claimed exactly once
        assert set(claimed_ids) == job_ids  # no duplicates, nothing missed

    async def test_a_second_wave_of_claims_leaves_nothing_unclaimed(
        self, sf: SessionFactory
    ) -> None:
        for _ in range(6):
            await add_job(sf)
        results = await asyncio.gather(*(claim_next_job(sf, worker_id=f"w{i}") for i in range(6)))
        assert all(r is not None for r in results)
        assert await claim_next_job(sf, worker_id="w-extra") is None


class TestCompleteAndFail:
    async def test_complete_marks_done(self, sf: SessionFactory) -> None:
        job_id = await add_job(sf)
        await claim_next_job(sf, worker_id="w1")
        await complete_job(sf, job_id)
        job = await get_job(sf, job_id)
        assert job.status == "done"

    async def test_fail_retries_when_under_max_attempts(self, sf: SessionFactory) -> None:
        job_id = await add_job(sf)
        await claim_next_job(sf, worker_id="w1")
        await fail_job(sf, job_id, error="boom", attempts=1)
        job = await get_job(sf, job_id)
        assert job.status == "pending"
        assert job.last_error == "boom"

    async def test_fail_dead_letters_at_max_attempts(self, sf: SessionFactory) -> None:
        job_id = await add_job(sf)
        await claim_next_job(sf, worker_id="w1")
        await fail_job(sf, job_id, error="boom", attempts=5)
        job = await get_job(sf, job_id)
        assert job.status == "dead"

    async def test_retried_job_is_claimable_again(self, sf: SessionFactory) -> None:
        job_id = await add_job(sf)
        await claim_next_job(sf, worker_id="w1")
        await fail_job(sf, job_id, error="transient", attempts=1)
        claimed = await claim_next_job(sf, worker_id="w2")
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.attempts == 2


class TestRunOnce:
    async def test_returns_false_on_an_empty_queue(self, sf: SessionFactory) -> None:
        assert await run_once(sf, {}, worker_id="w1") is False

    async def test_dispatches_to_the_matching_handler_and_completes(
        self, sf: SessionFactory
    ) -> None:
        seen: list[ClaimedJob] = []

        async def handler(job: ClaimedJob) -> None:
            seen.append(job)

        job_id = await add_job(sf, kind="start_run", payload={"run_id": "x"})
        assert await run_once(sf, {"start_run": handler}, worker_id="w1") is True
        assert [j.id for j in seen] == [job_id]
        job = await get_job(sf, job_id)
        assert job.status == "done"

    async def test_unregistered_kind_is_dead_lettered_not_crashed(
        self, sf: SessionFactory
    ) -> None:
        job_id = await add_job(sf, kind="cancel_run")
        assert await run_once(sf, {}, worker_id="w1") is True
        job = await get_job(sf, job_id)
        assert job.status == "pending"  # attempt 1 of MAX_ATTEMPTS: retried, not dead yet
        assert "no handler" in (job.last_error or "")

    async def test_a_raising_handler_fails_the_job_instead_of_propagating(
        self, sf: SessionFactory
    ) -> None:
        async def boom(job: ClaimedJob) -> None:
            raise RuntimeError("adapter exploded")

        job_id = await add_job(sf)
        assert await run_once(sf, {"start_run": boom}, worker_id="w1") is True
        job = await get_job(sf, job_id)
        assert job.status == "pending"
        assert job.last_error == "adapter exploded"


async def test_check_constraint_rejects_an_unknown_job_kind(sf: SessionFactory) -> None:
    with pytest.raises(DBAPIError, match="ck_jobs_kind"):
        async with transaction(sf) as s:
            s.add(Job(kind="not-a-kind"))
