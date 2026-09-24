"""FastAPI dependencies: app resources, authentication, CSRF origin check."""

from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import Depends, Request
from redis.asyncio import Redis

from acc.commands import auth as auth_commands
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
