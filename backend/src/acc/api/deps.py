"""FastAPI dependencies: app resources, authentication, CSRF origin check."""

import re
from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import Depends, Header, HTTPException, Request
from redis.asyncio import Redis

from acc.commands import auth as auth_commands
from acc.commands._base import EventSink
from acc.config import Settings
from acc.db.session import SessionFactory
from acc.domain.auth import Operator
from acc.domain.errors import AuthenticationFailed, PermissionDenied
from acc.ids import utcnow
from acc.queries import auth as auth_queries
from acc.security import session_id_for

SESSION_COOKIE = "acc_session"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Refresh the sliding expiry at most this often, to avoid a write on every request.
_TOUCH_INTERVAL = timedelta(minutes=5)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> SessionFactory:
    sf: SessionFactory = request.app.state.session_factory
    return sf


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
RedisDep = Annotated[Redis, Depends(get_redis)]


async def require_same_origin(request: Request, settings: SettingsDep) -> None:
    """CSRF defense for state-changing requests (together with SameSite=Strict cookies)."""
    if request.method in _SAFE_METHODS:
        return
    if request.headers.get("origin") != settings.public_origin:
        raise PermissionDenied("Cross-origin request rejected")


async def current_operator(
    request: Request, sf: SessionFactoryDep, settings: SettingsDep
) -> Operator:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AuthenticationFailed("Not signed in")
    session_id = session_id_for(token, settings.session_secret.get_secret_value())
    now = utcnow()
    active = await auth_queries.get_active_session(sf, session_id=session_id, now=now)
    if active is None:
        raise AuthenticationFailed("Session expired or revoked")
    if now - active.last_seen_at > _TOUCH_INTERVAL:
        await auth_commands.touch_session(
            sf,
            session_id=session_id,
            absolute_expires_at=active.absolute_expires_at,
            idle=timedelta(hours=settings.session_idle_hours),
        )
    request.state.session_id = session_id
    structlog.contextvars.bind_contextvars(operator_id=str(active.operator.id))
    return active.operator


OperatorDep = Annotated[Operator, Depends(current_operator)]


def get_event_sink(request: Request) -> EventSink | None:
    """Realtime publisher for committed events (None when realtime isn't configured)."""
    sink: EventSink | None = getattr(request.app.state, "event_sink", None)
    return sink


EventSinkDep = Annotated[EventSink | None, Depends(get_event_sink)]

_VERSION_TAG = re.compile(r'^(?:W/)?"?(\d{1,9})"?$')


def _parse_version(raw: str) -> int:
    match = _VERSION_TAG.match(raw.strip())
    if match is None:
        raise HTTPException(400, 'If-Match must be the resource version, e.g. If-Match: "3"')
    return int(match.group(1))


def expected_version(if_match: Annotated[str | None, Header()] = None) -> int:
    """Optimistic locking: mutations must say which version they were based on."""
    if if_match is None:
        raise HTTPException(428, "If-Match header with the current task version is required")
    return _parse_version(if_match)


def optional_version(if_match: Annotated[str | None, Header()] = None) -> int | None:
    return None if if_match is None else _parse_version(if_match)


ExpectedVersion = Annotated[int, Depends(expected_version)]
OptionalVersion = Annotated[int | None, Depends(optional_version)]


def etag(version: int) -> str:
    return f'"{version}"'
