"""Shared fixtures.

Integration tests run against real Postgres + Redis (never SQLite: enums, triggers and
partitions must behave exactly as in production). They need the ACC_TEST_* URLs that
scripts/test-backend.sh exports. Without them, integration tests are skipped locally and
fail in CI (ACC_REQUIRE_INTEGRATION=1).
"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from acc.api.app import create_app
from acc.config import Settings
from acc.kv import create_redis

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_ORIGIN = "http://testserver"


@dataclass(frozen=True)
class DbUrls:
    app_db: str
    owner_db: str
    redis: str


def alembic_config(owner_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def test_urls() -> DbUrls:
    urls = (
        os.environ.get("ACC_TEST_DATABASE_URL"),
        os.environ.get("ACC_TEST_MIGRATIONS_DATABASE_URL"),
        os.environ.get("ACC_TEST_REDIS_URL"),
    )
    if not all(urls):
        message = "ACC_TEST_* URLs not set; run scripts/test-backend.sh"
        if os.environ.get("ACC_REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)
        pytest.skip(message)
    app_db, owner_db, redis = (url or "" for url in urls)
    return DbUrls(app_db=app_db, owner_db=owner_db, redis=redis)


@pytest.fixture(scope="session")
def migrated(test_urls: DbUrls) -> Config:
    """Bring the test database to head once per session (from a clean slate)."""
    cfg = alembic_config(test_urls.owner_db)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return cfg


@pytest.fixture
def settings(test_urls: DbUrls, migrated: Config) -> Settings:
    return Settings(
        env="test",
        database_url=SecretStr(test_urls.app_db),
        migrations_database_url=SecretStr(test_urls.owner_db),
        redis_url=SecretStr(test_urls.redis),
        session_secret=SecretStr("test-secret-" + "x" * 32),
        public_origin=TEST_ORIGIN,
        alembic_ini=str(BACKEND_DIR / "alembic.ini"),
        log_level="WARNING",
    )


@pytest.fixture
async def clean_state(settings: Settings) -> None:
    """Empty mutable tables and the Redis test db before each test (as the schema owner)."""
    assert settings.migrations_database_url is not None
    engine = create_async_engine(settings.migrations_database_url.get_secret_value())
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, sessions RESTART IDENTITY CASCADE"))
    await engine.dispose()
    redis = create_redis(settings.redis_url.get_secret_value())
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
async def app(settings: Settings, clean_state: None) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=TEST_ORIGIN,
        headers={"origin": TEST_ORIGIN},
    ) as http:
        yield http


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str


@pytest.fixture
async def operator(app: FastAPI) -> Credentials:
    from acc.commands.auth import create_operator

    creds = Credentials(email="operator@example.com", password="correct horse battery staple")
    await create_operator(
        app.state.session_factory,
        email=creds.email,
        display_name="Test Operator",
        password=creds.password,
    )
    return creds


@pytest.fixture
async def signed_in(client: httpx.AsyncClient, operator: Credentials) -> httpx.AsyncClient:
    response = await client.post(
        "/api/v1/auth/login", json={"email": operator.email, "password": operator.password}
    )
    assert response.status_code == 200, response.text
    return client
