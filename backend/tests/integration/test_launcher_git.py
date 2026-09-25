"""The full repository/workspace round trip (PLAN-PHASE-2-M3.md) against a real,
hermetic git remote and a real launched container — not mocks, matching M2's "real
infra" bar exactly.

Like test_launcher_docker.py before it, these containers can't reach a live api server
in this test environment (no `docker compose up api` here), so their own heartbeat/
complete/file-changes calls fail harmlessly and are not what's asserted on here. What
*is* verified with real infrastructure: the container actually clones, branches,
commits and pushes to a real (throwaway, unauthenticated) git remote, and the launcher's
own direct effects (branch_name/workspace_ref, decided and recorded *before* the
container even starts — see acc.commands.runs.mark_run_starting) are correct regardless
of whether the container ever calls back. The internal API's own handling of
/file-changes and /complete — i.e. what the container's calls would have done had a live
api been reachable — is covered separately in tests/integration/test_internal_api.py,
the same split M2 already established.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import aiodocker
import pytest
from sqlalchemy import select, update

from acc.commands import projects as project_commands
from acc.commands.tasks import create_task, transition_task
from acc.config import Settings
from acc.db.models import Agent, AgentRun, Task
from acc.db.session import SessionFactory
from acc.domain.task_status import TaskStatus
from acc.orchestrator import launcher
from acc.orchestrator.scheduler import ClaimedJob
from acc.queries import runs as run_queries

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.usefixtures("seeded")]

ACTOR = "user:seed"
POLL_INTERVAL_S = 0.5
POLL_TIMEOUT_S = 30


async def wait_until(predicate: Callable[[], Awaitable[bool]], *, timeout_s: float) -> None:
    elapsed = 0.0
    while elapsed < timeout_s:
        if await predicate():
            return
        await asyncio.sleep(POLL_INTERVAL_S)
        elapsed += POLL_INTERVAL_S
    raise AssertionError(f"condition not met within {timeout_s}s")


async def make_runnable(sf: SessionFactory, slug: str = "developer") -> None:
    async with sf() as s, s.begin():
        await s.execute(update(Agent).where(Agent.slug == slug).values(adapter="claude_agent_sdk"))


async def enqueue_run_with_repository(sf: SessionFactory, remote_url: str) -> uuid.UUID:
    await make_runnable(sf)
    await project_commands.add_repository(
        sf, None, actor=ACTOR, project_slug="acc", name="test-repo",
        remote_url=remote_url, default_branch="main",
    )
    ref = await create_task(
        sf, None, actor=ACTOR, created_by=None, project_slug="acc", title="t",
        assignee="developer", repository="test-repo",
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


async def enqueue_run_without_repository(sf: SessionFactory) -> uuid.UUID:
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


async def launch(
    sf: SessionFactory, settings: Settings, docker: aiodocker.Docker, run_id: uuid.UUID
) -> str:
    """handle_start_run only — returns the container_id. Does not wait for the
    container's own workflow to finish; callers that care about that poll Docker
    directly (wait_for_container_exit), never the run's DB status (see module
    docstring)."""
    job = ClaimedJob(id=uuid.uuid4(), kind="start_run", payload={"run_id": str(run_id)}, attempts=1)
    await launcher.handle_start_run(sf, None, settings, docker, job)
    run = await run_queries.get_run(sf, run_id)
    assert run is not None
    assert run.container_id is not None
    return run.container_id


async def wait_for_container_exit(
    docker: aiodocker.Docker, container_id: str, *, timeout_s: float = POLL_TIMEOUT_S
) -> int:
    async def has_exited() -> bool:
        info = await docker.containers.container(container_id).show()
        return not info["State"]["Running"]

    await wait_until(has_exited, timeout_s=timeout_s)
    info = await docker.containers.container(container_id).show()
    exit_code: int = info["State"]["ExitCode"]
    return exit_code


async def ls_remote_branches(docker: aiodocker.Docker, remote_url: str) -> set[str]:
    """A throwaway inspection container — real git, same network, independent evidence
    that a branch actually exists on the remote (not just that our own DB says so)."""
    container = await docker.containers.create(
        {
            "Image": "alpine/git:latest",
            "Entrypoint": ["sh", "-c"],
            "Cmd": [f"git ls-remote --heads {remote_url}"],
            "HostConfig": {"NetworkMode": "acc_runner-egress", "AutoRemove": False},
        },
        name=f"acc-test-lsremote-{uuid.uuid4().hex[:8]}",
    )
    await container.start()
    await wait_for_container_exit(docker, container.id)
    output = await container.log(stdout=True, stderr=True)
    await container.delete(force=True)
    refs = {
        line.split("refs/heads/")[-1].strip() for line in output if "refs/heads/" in line
    }
    return refs


class TestFullGitRoundTrip:
    async def test_the_container_clones_branches_commits_and_pushes_successfully(
        self,
        sf: SessionFactory,
        settings: Settings,
        docker_client: aiodocker.Docker,
        git_test_remote_url: str,
    ) -> None:
        """Exit code 0 from the real runner entrypoint is the ground truth here: it only
        happens if clone, branch, commit and push (git_ops.py, real subprocesses, real
        network) all actually succeeded inside the sandbox."""
        run_id = await enqueue_run_with_repository(sf, git_test_remote_url)
        container_id = await launch(sf, settings, docker_client, run_id)
        exit_code = await wait_for_container_exit(docker_client, container_id)
        assert exit_code == 0
        await launcher._stop_and_remove(docker_client, container_id)

    async def test_the_branch_actually_exists_on_the_remote(
        self,
        sf: SessionFactory,
        settings: Settings,
        docker_client: aiodocker.Docker,
        git_test_remote_url: str,
    ) -> None:
        """Branch isolation, verified independently of our own bookkeeping: a real git
        client, talking to the real remote, sees exactly the branch the launcher decided
        on — proof the push actually landed, not just that the process exited 0."""
        run_id = await enqueue_run_with_repository(sf, git_test_remote_url)
        container_id = await launch(sf, settings, docker_client, run_id)
        run = await run_queries.get_run(sf, run_id)
        assert run is not None
        assert run.branch_name is not None
        expected_branch = run.branch_name

        exit_code = await wait_for_container_exit(docker_client, container_id)
        assert exit_code == 0

        branches = await ls_remote_branches(docker_client, git_test_remote_url)
        assert expected_branch in branches
        await launcher._stop_and_remove(docker_client, container_id)

    async def test_branch_name_and_workspace_ref_are_recorded_before_the_container_starts(
        self,
        sf: SessionFactory,
        settings: Settings,
        docker_client: aiodocker.Docker,
        git_test_remote_url: str,
    ) -> None:
        """acc.commands.runs.mark_run_starting sets these directly, called by the
        launcher itself — no dependency on the container ever calling back
        (PLAN-PHASE-2-M3.md §3), verified here by checking immediately after launch,
        before waiting for the container to do anything at all."""
        run_id = await enqueue_run_with_repository(sf, git_test_remote_url)
        container_id = await launch(sf, settings, docker_client, run_id)

        run = await run_queries.get_run(sf, run_id)
        assert run is not None
        assert run.branch_name is not None
        assert run.branch_name.startswith("acc/")
        assert run.workspace_ref == "/workspace/repo"

        async with sf() as s:
            task = await s.get(Task, run.task_id)
        assert task is not None
        assert task.branch_name == run.branch_name

        await wait_for_container_exit(docker_client, container_id)
        await launcher._stop_and_remove(docker_client, container_id)


class TestNoRepositoryRegression:
    async def test_a_task_with_no_repository_behaves_exactly_like_m2(
        self, sf: SessionFactory, settings: Settings, docker_client: aiodocker.Docker
    ) -> None:
        """PLAN-PHASE-2-M3.md §0.4's "additive, not required" claim, checked against a
        real launched container, not just the config dict (tests/unit/test_launcher.py
        already covers the config-only version of this claim)."""
        run_id = await enqueue_run_without_repository(sf)
        container_id = await launch(sf, settings, docker_client, run_id)
        run = await run_queries.get_run(sf, run_id)
        assert run is not None
        assert run.branch_name is None
        assert run.workspace_ref is None
        await launcher._stop_and_remove(docker_client, container_id)


class TestCredentialHygiene:
    async def test_no_credential_configured_the_git_credential_env_var_is_absent(
        self,
        sf: SessionFactory,
        settings: Settings,
        docker_client: aiodocker.Docker,
        git_test_remote_url: str,
    ) -> None:
        """The hermetic test server needs no auth at all — confirms a repository-linked
        run works with zero credential configured, and that nothing invents one."""
        assert settings.git_credential is None
        run_id = await enqueue_run_with_repository(sf, git_test_remote_url)
        container_id = await launch(sf, settings, docker_client, run_id)

        info = await docker_client.containers.container(container_id).show()
        env = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
        assert "GIT_CREDENTIAL" not in env

        exit_code = await wait_for_container_exit(docker_client, container_id)
        assert exit_code == 0
        await launcher._stop_and_remove(docker_client, container_id)

    async def test_a_configured_credential_never_appears_in_container_logs(
        self,
        sf: SessionFactory,
        settings: Settings,
        docker_client: aiodocker.Docker,
        git_test_remote_url: str,
    ) -> None:
        """A fake, distinctive credential is configured (the hermetic remote doesn't
        check it — git:// has no auth), and we confirm it never reaches the container's
        own stdout/stderr, which is the only per-container state inspectable at all with
        EXEC disabled on docker-socket-proxy (PLAN-PHASE-2-M2.md §2.1). Complements
        tests/unit/test_runner_git_ops.py's mocked-subprocess proof that no git command
        argument ever carries it and TestGitAskpass's proof of the only consumer."""
        from pydantic import SecretStr

        fake_credential = "fake-credential-xyz789-should-never-be-logged"
        settings_with_credential = settings.model_copy(
            update={"git_credential": SecretStr(fake_credential)}
        )
        run_id = await enqueue_run_with_repository(sf, git_test_remote_url)
        container_id = await launch(sf, settings_with_credential, docker_client, run_id)

        exit_code = await wait_for_container_exit(docker_client, container_id)
        assert exit_code == 0

        log_lines = await docker_client.containers.container(container_id).log(
            stdout=True, stderr=True
        )
        log_text = "".join(log_lines)
        assert fake_credential not in log_text

        await launcher._stop_and_remove(docker_client, container_id)
