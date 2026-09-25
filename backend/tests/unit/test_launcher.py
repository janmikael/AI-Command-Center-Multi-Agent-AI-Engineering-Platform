"""acc.orchestrator.launcher.build_container_config: pure, no Docker needed. The
security-relevant HostConfig fields (PLAN-PHASE-2-M2.md §2.3) are asserted directly so a
future edit that accidentally loosens one of them fails a test, not a live check."""

import sys
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from acc.config import Settings
from acc.orchestrator.launcher import RUN_ID_LABEL, build_container_config, compute_branch_name
from acc.queries.runs import RunRepository

RUN_ID = uuid.UUID(int=1)
AGENT_ID = uuid.UUID(int=2)
REPO = RunRepository(remote_url="git://git-test-server:9418/test.git", default_branch="main")

_RUNNER_DIR = Path(__file__).resolve().parents[3] / "runner"
if str(_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNER_DIR))
import git_ops  # type: ignore[import-not-found]  # noqa: E402


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://u:p@db/acc"),
        "redis_url": SecretStr("redis://redis:6379/0"),
        "session_secret": SecretStr("s" * 32),
        "runner_token_secret": SecretStr("r" * 32),
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_uses_the_configured_image_not_anything_caller_supplied() -> None:
    settings = make_settings(runner_image="acc-runner:pinned")
    config = build_container_config(
        run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=settings
    )
    assert config["Image"] == "acc-runner:pinned"


def test_env_carries_run_identity_and_token_only() -> None:
    settings = make_settings()
    config = build_container_config(
        run_token="secret-token-value", run_id=RUN_ID, agent_id=AGENT_ID, settings=settings
    )
    env = dict(item.split("=", 1) for item in config["Env"])
    assert env["RUN_ID"] == str(RUN_ID)
    assert env["AGENT_ID"] == str(AGENT_ID)
    assert env["RUN_TOKEN"] == "secret-token-value"
    # Nothing beyond run identity, the token, and where to call back — no DB URL, no
    # Redis URL, no session secret, no provider credential (PLAN-PHASE-2-M2.md §2.7).
    assert set(env) == {"RUN_ID", "AGENT_ID", "RUN_TOKEN", "API_BASE_URL", "HEARTBEAT_INTERVAL_S"}


def test_labeled_with_its_run_id() -> None:
    config = build_container_config(
        run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=make_settings()
    )
    assert config["Labels"] == {RUN_ID_LABEL: str(RUN_ID)}


def test_joins_only_the_configured_egress_network() -> None:
    settings = make_settings(runner_network="acc_runner-egress")
    config = build_container_config(
        run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=settings
    )
    network = config["HostConfig"]["NetworkMode"]
    assert network == "acc_runner-egress"
    assert network != "internal"  # the DB/Redis network, must never be reachable here
    assert "Networks" not in config  # a single network, not a multi-network attach list


def test_host_config_hardening() -> None:
    host_config = build_container_config(
        run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=make_settings()
    )["HostConfig"]
    assert host_config["ReadonlyRootfs"] is True
    assert host_config["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in host_config["SecurityOpt"]
    assert host_config["PidsLimit"] > 0
    assert host_config["Memory"] > 0
    assert host_config["NanoCpus"] > 0
    # No docker socket, no host bind mounts of any kind — only the two Tmpfs entries.
    assert "Binds" not in host_config
    assert "Mounts" not in host_config
    assert set(host_config["Tmpfs"]) == {"/tmp", "/workspace"}  # noqa: S108


def test_does_not_autoremove_before_we_can_inspect_exit_status() -> None:
    """Cleanup is explicit (acc.orchestrator.launcher._stop_and_remove /
    reconcile_active_runs), specifically so exit status can be read first — see
    PLAN-PHASE-2-M2.md §3.5."""
    host_config = build_container_config(
        run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=make_settings()
    )["HostConfig"]
    assert host_config["AutoRemove"] is False


class TestComputeBranchName:
    def test_is_namespaced_under_acc(self) -> None:
        assert compute_branch_name("ACC-21", RUN_ID).startswith("acc/")

    def test_is_lowercase(self) -> None:
        assert compute_branch_name("ACC-21", RUN_ID) == compute_branch_name(
            "ACC-21", RUN_ID
        ).lower()

    def test_different_runs_of_the_same_task_get_different_branches(self) -> None:
        other_run_id = uuid.uuid4()
        assert compute_branch_name("ACC-21", RUN_ID) != compute_branch_name(
            "ACC-21", other_run_id
        )

    def test_different_branches_even_for_uuid7s_minted_moments_apart(self) -> None:
        """uuid.uuid4() above is fully random and would never have caught this: real run
        ids are UUID7 (acc.ids.new_id), whose *leading* bits are a millisecond timestamp
        (RFC 9562) — two runs created within the same test session share almost all of
        their leading hex digits. Caught live as a real branch-name collision between
        consecutive test runs; compute_branch_name now takes its suffix from the
        UUID7's trailing (random) bits instead, and this test pins that choice down."""
        from acc.ids import new_id

        first, second = new_id(), new_id()
        assert compute_branch_name("ACC-21", first) != compute_branch_name("ACC-21", second)

    def test_matches_the_runner_wrapper_s_own_allowlist(self) -> None:
        """The launcher computes the name; runner/git_ops.py's BRANCH_PATTERN decides
        whether to honor it — they must agree, or every repository-linked run would
        fail at the push step (PLAN-PHASE-2-M3.md §2.4/§4)."""
        branch = compute_branch_name("ACC-21", RUN_ID)
        assert git_ops.BRANCH_PATTERN.match(branch)


class TestRepositoryAwareConfig:
    def test_no_repository_produces_byte_identical_env_to_m2(self) -> None:
        """A task with no linked repository must be unaffected by M3 (PLAN-PHASE-2-M3.md
        §0.4) — this is the exact assertion test_env_carries_run_identity_and_token_only
        already makes above; repeated here to anchor the "additive, not required" claim
        directly against the repository-aware code path."""
        config = build_container_config(
            run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=make_settings(),
            repository=None, branch_name=None,
        )
        env = dict(item.split("=", 1) for item in config["Env"])
        assert set(env) == {
            "RUN_ID", "AGENT_ID", "RUN_TOKEN", "API_BASE_URL", "HEARTBEAT_INTERVAL_S",
        }

    def test_repository_adds_git_env_vars(self) -> None:
        config = build_container_config(
            run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=make_settings(),
            repository=REPO, branch_name="acc/acc-21-abcd1234",
        )
        env = dict(item.split("=", 1) for item in config["Env"])
        assert env["GIT_REMOTE_URL"] == REPO.remote_url
        assert env["GIT_DEFAULT_BRANCH"] == REPO.default_branch
        assert env["GIT_BRANCH_NAME"] == "acc/acc-21-abcd1234"
        assert env["WORKSPACE_DIR"]

    def test_no_credential_configured_means_no_git_credential_env_var(self) -> None:
        settings = make_settings()
        assert settings.git_credential is None
        config = build_container_config(
            run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=settings,
            repository=REPO, branch_name="acc/acc-21-abcd1234",
        )
        env = dict(item.split("=", 1) for item in config["Env"])
        assert "GIT_CREDENTIAL" not in env

    def test_configured_credential_is_injected_only_when_repository_is_set(self) -> None:
        settings = make_settings(git_credential=SecretStr("super-secret-token"))
        with_repo = build_container_config(
            run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=settings,
            repository=REPO, branch_name="acc/acc-21-abcd1234",
        )
        env = dict(item.split("=", 1) for item in with_repo["Env"])
        assert env["GIT_CREDENTIAL"] == "super-secret-token"

        without_repo = build_container_config(
            run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=settings,
        )
        env_no_repo = dict(item.split("=", 1) for item in without_repo["Env"])
        # No repository to push to: the credential has nothing to authenticate against,
        # so it's never handed to the container at all, even though one is configured.
        assert "GIT_CREDENTIAL" not in env_no_repo

    def test_repository_without_branch_name_is_a_programming_error(self) -> None:
        with pytest.raises(AssertionError):
            build_container_config(
                run_token="tok", run_id=RUN_ID, agent_id=AGENT_ID, settings=make_settings(),
                repository=REPO, branch_name=None,
            )
