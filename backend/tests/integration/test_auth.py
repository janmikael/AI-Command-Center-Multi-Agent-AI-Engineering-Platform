from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from acc.config import Settings
from acc.ids import utcnow
from tests.conftest import TEST_ORIGIN, Credentials

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


async def login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post(LOGIN, json={"email": email, "password": password})


def assert_problem(response: httpx.Response, status: int) -> dict[str, object]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, object] = response.json()
    assert body["status"] == status
    assert str(body["type"]).startswith("urn:acc:problem:")
    assert "request_id" in body
    return body


async def owner_sql(settings: Settings, sql: str, **params: object) -> None:
    assert settings.migrations_database_url is not None
    engine = create_async_engine(settings.migrations_database_url.get_secret_value())
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)
    await engine.dispose()


class TestLogin:
    async def test_success_sets_hardened_cookie(
        self, client: httpx.AsyncClient, operator: Credentials
    ) -> None:
        response = await login(client, operator.email, operator.password)
        assert response.status_code == 200
        assert response.json()["email"] == operator.email
        cookie = response.headers["set-cookie"].lower()
        assert "acc_session=" in cookie
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "path=/" in cookie

    async def test_email_is_case_insensitive(
        self, client: httpx.AsyncClient, operator: Credentials
    ) -> None:
        response = await login(client, operator.email.upper(), operator.password)
        assert response.status_code == 200

    async def test_wrong_password_and_unknown_email_look_identical(
        self, client: httpx.AsyncClient, operator: Credentials
    ) -> None:
        wrong = assert_problem(await login(client, operator.email, "not the password"), 401)
        unknown = assert_problem(await login(client, "nobody@example.com", "whatever-pass"), 401)
        assert wrong["title"] == unknown["title"] == "Invalid email or password"

    async def test_rate_limited_after_repeated_failures(
        self, client: httpx.AsyncClient, operator: Credentials, settings: Settings
    ) -> None:
        for _ in range(settings.login_max_attempts):
            assert (await login(client, operator.email, "wrong-password")).status_code == 401
        blocked = await login(client, operator.email, operator.password)  # even correct pw
        assert_problem(blocked, 429)
        assert int(blocked.headers["retry-after"]) > 0

    async def test_requires_same_origin(
        self, client: httpx.AsyncClient, operator: Credentials
    ) -> None:
        for headers in ({"origin": "https://evil.example"}, {"origin": ""}):
            response = await client.post(
                LOGIN,
                json={"email": operator.email, "password": operator.password},
                headers=headers,
            )
            assert_problem(response, 403)

    async def test_malformed_body_is_422_problem(self, client: httpx.AsyncClient) -> None:
        body = assert_problem(await client.post(LOGIN, json={"email": "not-an-email"}), 422)
        assert body["errors"]

    async def test_disabled_operator_cannot_log_in(
        self, client: httpx.AsyncClient, operator: Credentials, settings: Settings
    ) -> None:
        await owner_sql(settings, "UPDATE users SET disabled_at = now()")
        assert_problem(await login(client, operator.email, operator.password), 401)


class TestSession:
    async def test_me_requires_session(self, client: httpx.AsyncClient) -> None:
        assert_problem(await client.get(ME), 401)

    async def test_me_returns_operator(self, signed_in: httpx.AsyncClient) -> None:
        response = await signed_in.get(ME)
        assert response.status_code == 200
        assert response.json()["display_name"] == "Test Operator"
        assert response.headers["cache-control"] == "no-store"

    async def test_logout_revokes_session(self, signed_in: httpx.AsyncClient) -> None:
        stolen_cookie = signed_in.cookies.get("acc_session")
        assert (await signed_in.post("/api/v1/auth/logout")).status_code == 204
        signed_in.cookies.set("acc_session", stolen_cookie or "")  # replaying the old cookie
        assert_problem(await signed_in.get(ME), 401)

    async def test_expired_session_rejected(
        self, signed_in: httpx.AsyncClient, settings: Settings
    ) -> None:
        await owner_sql(settings, "UPDATE sessions SET expires_at = now() - interval '1 second'")
        assert_problem(await signed_in.get(ME), 401)

    async def test_absolute_expiry_rejected(
        self, signed_in: httpx.AsyncClient, settings: Settings
    ) -> None:
        await owner_sql(
            settings, "UPDATE sessions SET absolute_expires_at = now() - interval '1 second'"
        )
        assert_problem(await signed_in.get(ME), 401)

    async def test_stale_session_is_touched(
        self, signed_in: httpx.AsyncClient, settings: Settings, app: FastAPI
    ) -> None:
        stale = utcnow() - timedelta(minutes=30)
        await owner_sql(settings, "UPDATE sessions SET last_seen_at = :stale", stale=stale)
        assert (await signed_in.get(ME)).status_code == 200
        async with app.state.session_factory() as session:
            last_seen = await session.scalar(text("SELECT last_seen_at FROM sessions"))
        assert last_seen > stale

    async def test_raw_token_is_never_stored(
        self, signed_in: httpx.AsyncClient, app: FastAPI
    ) -> None:
        token = signed_in.cookies.get("acc_session")
        async with app.state.session_factory() as session:
            stored = await session.scalar(text("SELECT id FROM sessions"))
        assert token
        assert stored
        assert token != stored


class TestChangePassword:
    NEW = "a brand new passphrase 42"

    async def test_changes_password_and_revokes_other_sessions(
        self, app: FastAPI, signed_in: httpx.AsyncClient, operator: Credentials
    ) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=TEST_ORIGIN,
            headers={"origin": TEST_ORIGIN},
        ) as other_device:
            assert (await login(other_device, operator.email, operator.password)).status_code == 200

            response = await signed_in.post(
                "/api/v1/auth/change-password",
                json={"current_password": operator.password, "new_password": self.NEW},
            )
            assert response.status_code == 204

            assert (await signed_in.get(ME)).status_code == 200  # this session survives
            assert_problem(await other_device.get(ME), 401)  # the other one is revoked

        assert_problem(await login(signed_in, operator.email, operator.password), 401)
        assert (await login(signed_in, operator.email, self.NEW)).status_code == 200

    async def test_wrong_current_password(
        self, signed_in: httpx.AsyncClient, operator: Credentials
    ) -> None:
        response = await signed_in.post(
            "/api/v1/auth/change-password",
            json={"current_password": "not it at all", "new_password": self.NEW},
        )
        assert_problem(response, 401)

    async def test_weak_new_password(
        self, signed_in: httpx.AsyncClient, operator: Credentials
    ) -> None:
        response = await signed_in.post(
            "/api/v1/auth/change-password",
            json={"current_password": operator.password, "new_password": "short"},
        )
        assert_problem(response, 422)


class TestCreateOperator:
    async def test_duplicate_email_conflicts(self, app: FastAPI, operator: Credentials) -> None:
        from acc.commands.auth import create_operator
        from acc.domain.errors import Conflict

        with pytest.raises(Conflict):
            await create_operator(
                app.state.session_factory,
                email=operator.email.upper(),
                display_name="Dup",
                password="another long passphrase",
            )
