"""Typed runtime configuration. Every setting comes from an ACC_* environment variable."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that must never reach production (placeholders from .env.example).
_WEAK_MARKERS = ("change-me", "changeme", "dev-only", "insecure")


class Settings(BaseSettings):
    # hide_input_in_errors: a validation error must never echo configuration values
    # (connection strings, secrets) into logs or crash output.
    model_config = SettingsConfigDict(env_prefix="ACC_", extra="ignore", hide_input_in_errors=True)

    env: Literal["development", "test", "production"] = "development"

    # Runtime connections use the least-privileged role (acc_app).
    database_url: SecretStr
    # Migrations use the schema owner role (acc_owner). Falls back to database_url.
    migrations_database_url: SecretStr | None = None
    redis_url: SecretStr

    # HMAC key for hashing session tokens before they are stored.
    session_secret: SecretStr = Field(min_length=32)
    # Separate from session_secret on purpose (PLAN-PHASE-2-M2.md §2.4): signs the
    # per-run token a sandboxed runner container uses to call the internal API. A leak
    # of one secret must not compromise the other.
    runner_token_secret: SecretStr = Field(min_length=32)
    run_token_ttl_seconds: int = Field(default=3600, gt=0)
    runner_image: str = "acc-runner:local"
    runner_network: str = "acc_runner-egress"
    docker_proxy_url: str = "tcp://docker-socket-proxy:2375"
    # M3 (PLAN-PHASE-2-M3.md §2.2): one credential for one throwaway test repository —
    # deliberately not a per-repository column in `repositories`, see that section for
    # why. Optional: a task with no repository never needs it. Injected into the runner
    # via GIT_CREDENTIAL and consumed only through GIT_ASKPASS (§2.3) — never written to
    # a git remote URL or config file.
    git_credential: SecretStr | None = None
    # Exact origin the browser uses (scheme://host[:port]). Used for the CSRF origin
    # check and to decide whether cookies are marked Secure.
    public_origin: str = "http://localhost"
    session_idle_hours: int = Field(default=12, gt=0)
    session_absolute_days: int = Field(default=7, gt=0)

    login_max_attempts: int = Field(default=5, gt=0)
    login_window_seconds: int = Field(default=900, gt=0)

    log_level: str = "INFO"
    log_json: bool | None = None  # default: JSON in production, console otherwise

    alembic_ini: str = "alembic.ini"

    @property
    def cookie_secure(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def json_logs(self) -> bool:
        return self.log_json if self.log_json is not None else self.env == "production"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @model_validator(mode="after")
    def _production_guards(self) -> Self:
        if self.env != "production":
            return self
        problems: list[str] = []
        secrets: dict[str, SecretStr] = {
            "ACC_SESSION_SECRET": self.session_secret,
            "ACC_RUNNER_TOKEN_SECRET": self.runner_token_secret,
            "ACC_DATABASE_URL": self.database_url,
            "ACC_REDIS_URL": self.redis_url,
        }
        if self.git_credential is not None:
            secrets["ACC_GIT_CREDENTIAL"] = self.git_credential
        for name, value in secrets.items():
            if any(marker in value.get_secret_value().lower() for marker in _WEAK_MARKERS):
                problems.append(f"{name} still contains a placeholder value")
        if not self.cookie_secure:
            problems.append("ACC_PUBLIC_ORIGIN must be https:// in production")
        if problems:
            raise ValueError("Refusing to start: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
