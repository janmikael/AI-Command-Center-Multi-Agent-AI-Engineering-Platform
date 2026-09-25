"""acc.orchestrator.launcher.build_container_config: pure, no Docker needed. The
security-relevant HostConfig fields (PLAN-PHASE-2-M2.md §2.3) are asserted directly so a
future edit that accidentally loosens one of them fails a test, not a live check."""

import uuid

from pydantic import SecretStr

from acc.config import Settings
from acc.orchestrator.launcher import RUN_ID_LABEL, build_container_config

RUN_ID = uuid.UUID(int=1)
AGENT_ID = uuid.UUID(int=2)


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
