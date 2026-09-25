"""acc.orchestrator.launcher against a *real* docker-socket-proxy and *real* launched
containers — the same standard Phase 1 M3's cross-process WebSocket test and Phase 2
M1's scheduler concurrency test held themselves to (PLAN-PHASE-2-M2.md §5).

These containers run the real runner image but can't reach a live api server (no
`docker compose up api` in this test environment), so their own heartbeat/complete calls
fail harmlessly and are not what's under test here. The full round trip against a live
api is covered by live verification instead. What real Docker *does* prove, and mocks
can't: the container actually gets created, actually starts, actually stops when asked,
actually exits when killed, and actually lands on the network we configured — not just
that we asked correctly.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import aiodocker
import pytest
from sqlalchemy import select, update

from acc.commands import runs as run_commands
from acc.commands.tasks import create_task, transition_task
from acc.config import Settings
from acc.db.models import Agent, AgentRun, Task
from acc.db.session import SessionFactory
from acc.domain.agent_run import RunStatus
from acc.domain.task_status import TaskStatus
from acc.orchestrator import launcher
from acc.orchestrator.scheduler import ClaimedJob

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.usefixtures("seeded")]

ACTOR = "user:seed"
STOP_WAIT_S = 15
POLL_INTERVAL_S = 0.5


def start_job(run_id: uuid.UUID) -> ClaimedJob:
    return ClaimedJob(
        id=uuid.uuid4(), kind="start_run", payload={"run_id": str(run_id)}, attempts=1
    )


def cancel_job(run_id: uuid.UUID) -> ClaimedJob:
    return ClaimedJob(
        id=uuid.uuid4(), kind="cancel_run", payload={"run_id": str(run_id)}, attempts=1
    )


async def make_runnable(sf: SessionFactory, slug: str = "developer") -> None:
    async with sf() as s, s.begin():
        await s.execute(update(Agent).where(Agent.slug == slug).values(adapter="claude_agent_sdk"))


async def enqueue_run(sf: SessionFactory) -> uuid.UUID:
    """A real, launchable run: task -> planning -> working (M1's transition_task hook
    enqueues it), exactly the path a real assignment takes."""
    await make_runnable(sf)
    ref = await create_task(
        sf, None, actor=ACTOR, created_by=None, project_slug="acc", title="t",
        assignee="developer",
    )
    version = ref.version
    for to in (TaskStatus.PLANNING, TaskStatus.WORKING):
        updated = await transition_task(
            sf, None, actor=ACTOR, key=ref.key, expected_version=version, to=to
        )
        version = updated.version
    async with sf() as s:
        run = await s.scalar(select(AgentRun).where(AgentRun.task_id == ref.id))
    assert run is not None
    return run.id


async def get_run(sf: SessionFactory, run_id: uuid.UUID) -> AgentRun:
    async with sf() as s:
        run = await s.get(AgentRun, run_id)
    assert run is not None
    return run


async def wait_until(
    predicate: Callable[[], Awaitable[bool]], *, timeout_s: float = STOP_WAIT_S
) -> None:
    elapsed = 0.0
    while elapsed < timeout_s:
        if await predicate():
            return
        await asyncio.sleep(POLL_INTERVAL_S)
        elapsed += POLL_INTERVAL_S
    raise AssertionError(f"condition not met within {timeout_s}s")


class TestHandleStartRun:
    async def test_creates_and_starts_a_real_container(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))

        run = await get_run(sf, run_id)
        assert run.status == RunStatus.STARTING.value
        assert run.container_id is not None

        info = await docker_client.containers.container(run.container_id).show()
        assert info["State"]["Status"] in ("running", "created")
        assert info["Config"]["Image"] in ("acc-runner:local", "docker.io/library/acc-runner:local")

        await launcher._stop_and_remove(docker_client, run.container_id)  # test cleanup

    async def test_container_is_attached_only_to_the_egress_network(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))

        run = await get_run(sf, run_id)
        assert run.container_id is not None
        info = await docker_client.containers.container(run.container_id).show()
        networks = set(info["NetworkSettings"]["Networks"])
        assert networks == {"acc_runner-egress"}
        assert "internal" not in networks
        assert "acc_internal" not in networks

        await launcher._stop_and_remove(docker_client, run.container_id)

    async def test_idempotent_on_a_retried_job(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        job = start_job(run_id)

        await launcher.handle_start_run(sf, None, settings, docker_client, job)
        first_container_id = (await get_run(sf, run_id)).container_id
        assert first_container_id is not None

        # A retry of the same job (e.g. after a transient DB error mid-handler).
        await launcher.handle_start_run(sf, None, settings, docker_client, job)
        second_container_id = (await get_run(sf, run_id)).container_id
        assert second_container_id == first_container_id  # no second container created

        await launcher._stop_and_remove(docker_client, first_container_id)

    async def test_cancel_before_start_launches_nothing(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await run_commands.request_cancel(sf, None, run_id=run_id)

        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))

        run = await get_run(sf, run_id)
        assert run.status == RunStatus.CANCELLED.value
        assert run.container_id is None


class TestHandleCancelRun:
    async def test_stops_a_real_running_container(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))
        container_id = (await get_run(sf, run_id)).container_id
        assert container_id is not None

        await launcher.handle_cancel_run(sf, None, docker_client, cancel_job(run_id))

        run = await get_run(sf, run_id)
        assert run.status == RunStatus.CANCELLED.value

        with pytest.raises(aiodocker.DockerError) as excinfo:
            await docker_client.containers.container(container_id).show()
        assert excinfo.value.status == 404  # actually gone, not just marked cancelled in the DB

    async def test_cancelling_a_run_that_never_started_is_a_clean_noop(
        self, sf: SessionFactory, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await run_commands.request_cancel(sf, None, run_id=run_id)

        await launcher.handle_cancel_run(sf, None, docker_client, cancel_job(run_id))

        run = await get_run(sf, run_id)
        assert run.status == RunStatus.CANCELLED.value
        assert run.container_id is None


class TestReconcileActiveRuns:
    """The scenario added to M2's verification: a runner container exits unexpectedly
    (simulated here with a direct kill, standing in for a crash or an OOM kill — the
    detection mechanism doesn't know or care which), and the orchestrator notices and
    transitions the run on its own, without an operator cancelling anything."""

    async def test_detects_an_unexpectedly_killed_container_and_fails_the_run(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))
        run_before = await get_run(sf, run_id)
        assert run_before.container_id is not None
        assert run_before.status == RunStatus.STARTING.value  # still active, no heartbeat yet
        container_id = run_before.container_id

        # Simulate the container crashing / being OOM-killed — out of band, exactly as
        # the orchestrator would discover it: it did NOT go through handle_cancel_run.
        await docker_client.containers.container(container_id).kill()

        async def container_is_gone() -> bool:
            try:
                info = await docker_client.containers.container(container_id).show()
            except aiodocker.DockerError:
                return True
            return not info["State"]["Running"]

        await wait_until(container_is_gone)

        await launcher.reconcile_active_runs(sf, None, docker_client)

        run_after = await get_run(sf, run_id)
        assert run_after.status == RunStatus.FAILED.value
        assert run_after.error is not None
        assert "exited unexpectedly" in run_after.error.get("reason", "")

        async with sf() as s:
            task = await s.get(Task, run_before.task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED  # ORCHESTRATOR_ALLOWED: working -> failed

    async def test_leaves_a_genuinely_running_container_alone(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))
        container_id = (await get_run(sf, run_id)).container_id
        assert container_id is not None

        await launcher.reconcile_active_runs(sf, None, docker_client)

        run = await get_run(sf, run_id)
        assert run.status == RunStatus.STARTING.value  # unchanged: no false positive

        await launcher._stop_and_remove(docker_client, container_id)

    async def test_skips_runs_with_a_pending_cancellation(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        """That path belongs to handle_cancel_run; reconciliation must not race it by
        also trying to fail the same run."""
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))
        container_id = (await get_run(sf, run_id)).container_id
        assert container_id is not None

        await run_commands.request_cancel(sf, None, run_id=run_id)
        await docker_client.containers.container(container_id).kill()

        await launcher.reconcile_active_runs(sf, None, docker_client)

        run = await get_run(sf, run_id)
        assert run.status == RunStatus.STARTING.value  # reconcile left it alone

        await launcher._stop_and_remove(docker_client, container_id)


class TestCleanupFinishedContainers:
    """The success-path counterpart to TestReconcileActiveRuns: a run that finished
    normally (through /complete, not a crash) still leaves its container behind, since
    the internal API process itself never touches Docker (§1.6). Nothing but this pass
    removes it."""

    async def test_removes_a_container_for_a_run_that_finished_normally(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))
        container_id = (await get_run(sf, run_id)).container_id
        assert container_id is not None

        # Simulates what the internal API's /heartbeat then /complete would do, without
        # depending on the container reaching a live api in this test environment.
        await run_commands.record_heartbeat(sf, None, run_id=run_id)
        await run_commands.complete_run(
            sf, None, actor="agent:developer", run_id=run_id, result_summary="ok"
        )
        assert (await get_run(sf, run_id)).status == RunStatus.SUCCEEDED.value

        await launcher.cleanup_finished_containers(sf, docker_client)

        with pytest.raises(aiodocker.DockerError) as excinfo:
            await docker_client.containers.container(container_id).show()
        assert excinfo.value.status == 404

    async def test_leaves_an_active_runs_container_alone(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))
        container_id = (await get_run(sf, run_id)).container_id
        assert container_id is not None

        await launcher.cleanup_finished_containers(sf, docker_client)

        info = await docker_client.containers.container(container_id).show()
        assert info["State"]["Status"] in ("running", "created")  # still there

        await launcher._stop_and_remove(docker_client, container_id)

    async def test_tolerates_a_container_already_removed(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        """cleanup_finished_containers is idempotent: re-running it after a container is
        already gone must not raise."""
        run_id = await enqueue_run(sf)
        await launcher.handle_start_run(sf, None, settings, docker_client, start_job(run_id))
        container_id = (await get_run(sf, run_id)).container_id
        assert container_id is not None
        await run_commands.record_heartbeat(sf, None, run_id=run_id)
        await run_commands.complete_run(
            sf, None, actor="agent:developer", run_id=run_id, result_summary="ok"
        )

        await launcher.cleanup_finished_containers(sf, docker_client)
        await launcher.cleanup_finished_containers(sf, docker_client)  # must not raise
