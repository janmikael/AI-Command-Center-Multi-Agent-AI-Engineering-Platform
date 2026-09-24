import pytest
from pydantic import SecretStr, ValidationError

from acc.config import Settings
from acc.logging import REDACTED, redact_sensitive


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://u:strong-pw@db/acc"),
        "redis_url": SecretStr("redis://:strong-pw@redis:6379/0"),
        "session_secret": SecretStr("a" * 16 + "b" * 16 + "c" * 8),
        "public_origin": "https://acc.example.com",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_development_accepts_http_origin() -> None:
    settings = make(env="development", public_origin="http://localhost")
    assert not settings.cookie_secure
    assert not settings.json_logs


def test_production_defaults_to_json_logs_and_secure_cookies() -> None:
    settings = make(env="production")
    assert settings.cookie_secure
    assert settings.json_logs


def test_production_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="ACC_SESSION_SECRET"):
        make(env="production", session_secret=SecretStr("change-me-to-at-least-32-random-chars"))


def test_production_rejects_http_origin() -> None:
    with pytest.raises(ValidationError, match="https"):
        make(env="production", public_origin="http://acc.example.com")


def test_short_session_secret_rejected() -> None:
    with pytest.raises(ValidationError):
        make(session_secret=SecretStr("too-short"))


@pytest.mark.parametrize(
    ("overrides", "leaked"),
    [
        # Field-level error: the rejected value itself is sensitive.
        ({"session_secret": "tiny-secret-value"}, "tiny-secret-value"),
        # Model-level error: the whole input (including the DB password) is sensitive.
        (
            {
                "database_url": "postgresql+asyncpg://u:hunter2-db-pw@db/acc",
                "env": "production",
                "public_origin": "http://plain.example",
            },
            "hunter2-db-pw",
        ),
    ],
)
def test_validation_errors_never_echo_config_values(
    overrides: dict[str, object], leaked: str
) -> None:
    # The rendered message is what reaches crash output and logs. (The structured
    # .errors() list keeps raw input by pydantic design; nothing in acc prints it.)
    with pytest.raises(ValidationError) as excinfo:
        make(**overrides)
    assert leaked not in str(excinfo.value)


def test_redaction_masks_credential_keys() -> None:
    event = {
        "event": "login",
        "password": "hunter2",
        "session_token": "abc",
        "Authorization": "Bearer x",
        "email": "op@example.com",
    }
    result = redact_sensitive(None, "info", dict(event))
    assert result["password"] == REDACTED
    assert result["session_token"] == REDACTED
    assert result["Authorization"] == REDACTED
    assert result["email"] == "op@example.com"
    assert result["event"] == "login"
