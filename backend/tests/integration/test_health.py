import httpx
import pytest

pytestmark = pytest.mark.integration


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_every_dependency(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == {"database", "migrations", "redis"}
    assert all(check["ok"] for check in body["checks"].values())


async def test_every_response_carries_request_id_and_security_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/healthz", headers={"x-request-id": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


async def test_invalid_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"x-request-id": "bad id with spaces"})
    assert response.headers["x-request-id"] != "bad id with spaces"
    assert len(response.headers["x-request-id"]) == 32
