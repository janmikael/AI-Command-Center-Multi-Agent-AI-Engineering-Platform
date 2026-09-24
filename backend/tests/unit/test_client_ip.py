"""acc.api.routers.auth._client_ip: the session's `ip` column is `inet`, so anything that
isn't a real address must become null rather than crash the request. Caught by the
WebSocket integration tests, whose TestClient sets a non-IP client host ("testclient")."""

from types import SimpleNamespace
from typing import Any

from acc.api.routers.auth import _client_ip


def fake_request(client_host: str | None) -> Any:
    client = SimpleNamespace(host=client_host) if client_host is not None else None
    return SimpleNamespace(client=client)


def test_valid_ipv4_passes_through() -> None:
    assert _client_ip(fake_request("203.0.113.7")) == "203.0.113.7"


def test_valid_ipv6_passes_through() -> None:
    assert _client_ip(fake_request("::1")) == "::1"


def test_no_client_on_the_request_is_none() -> None:
    assert _client_ip(fake_request(None)) is None


def test_non_ip_client_host_becomes_none_instead_of_crashing() -> None:
    assert _client_ip(fake_request("testclient")) is None
