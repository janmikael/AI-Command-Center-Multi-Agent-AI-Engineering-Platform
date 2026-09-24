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
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from starlette.testclient import TestClient

from acc.api.app import create_app
from acc.config import Settings
from acc.db.session import SessionFactory, create_engine, create_session_factory
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


ALL_TABLES = (
    "events, tool_calls, file_changes, usage_records, agent_runs, jobs, "
    "governance_rules, project_context_entries, "
    "tasks, agent_capabilities, agents, repositories, projects, sessions, users"
)


@pytest.fixture
async def clean_state(settings: Settings) -> None:
    """Empty all tables and the Redis test db before each test.

    Runs as the schema owner: TRUNCATE is deliberately not granted to the runtime role
    (and is the only way to clear the append-only events table, which is test-only).
    """
    assert settings.migrations_database_url is not None
    engine = create_async_engine(settings.migrations_database_url.get_secret_value())
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    redis = create_redis(settings.redis_url.get_secret_value())
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
async def sf(settings: Settings, clean_state: None) -> AsyncIterator[SessionFactory]:
    """Session factory on the runtime role (acc_app), without starting the API."""
    engine = create_engine(settings.database_url.get_secret_value())
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def owner_conn(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Autocommit connection as the schema owner (acc_owner), for DB-level assertions."""
    assert settings.migrations_database_url is not None
    engine = create_async_engine(
        settings.migrations_database_url.get_secret_value(), isolation_level="AUTOCOMMIT"
    )
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


@pytest.fixture
async def app_conn(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Autocommit connection as the runtime role (acc_app), for privilege assertions."""
    engine = create_async_engine(
        settings.database_url.get_secret_value(), isolation_level="AUTOCOMMIT"
    )
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


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
async def seeded(app: FastAPI) -> None:
    """Default project 'acc' (key ACC) and the architect/developer/designer agents."""
    from acc.commands.seed import seed

    await seed(app.state.session_factory)


@pytest.fixture
async def signed_in(client: httpx.AsyncClient, operator: Credentials) -> httpx.AsyncClient:
    response = await client.post(
        "/api/v1/auth/login", json={"email": operator.email, "password": operator.password}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
async def ws_app(settings: Settings) -> FastAPI:
    """A second, independent FastAPI instance on the same Postgres/Redis as `app`.

    Realtime tests use this to exercise the real multi-process design (ARCHITECTURE.md
    §1.2/§3.3): a write via `client`/`signed_in` (simulating one API process) publishes to
    Redis, and *this* process's own Hub — fed by its own relay task — is what delivers it
    to a WebSocket connected here. Deliberately not the same FastAPI object as `app`, and
    deliberately depends on `clean_state` only through `settings` (not the `app` fixture),
    so it doesn't matter which of `app` / `ws_app` a test builds first.
    """
    return create_app(settings)


@pytest.fixture
async def ws_client(ws_app: FastAPI) -> AsyncIterator[TestClient]:
    with TestClient(ws_app, base_url=TEST_ORIGIN) as test_client:
        yield test_client


def login_ws_client(ws_client: TestClient, operator: Credentials) -> None:
    """Logs `operator` into `ws_client` directly (not via `client`/`signed_in`, which are
    bound to the other FastAPI instance and so hold their own, separate session cookie)."""
    response = ws_client.post(
        "/api/v1/auth/login",
        json={"email": operator.email, "password": operator.password},
        headers={"origin": TEST_ORIGIN},
    )
    assert response.status_code == 200, response.text
