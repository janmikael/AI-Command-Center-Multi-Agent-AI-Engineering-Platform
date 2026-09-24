"""Operator authentication: create operator, login, session touch/revoke, password change."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from anyio import to_thread
from pydantic import EmailStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from redis.asyncio import Redis
from sqlalchemy import select, update

from acc.config import Settings
from acc.db.models import AuthSession, User
from acc.db.session import SessionFactory, transaction
from acc.domain.auth import Operator, Role, check_password_policy, session_expiry
from acc.domain.errors import AuthenticationFailed, Conflict, RateLimited, ValidationFailed
from acc.ids import utcnow
from acc.security import (
    burn_verify_time,
    hash_password,
    needs_rehash,
    new_session_token,
    session_id_for,
    verify_password,
)

_email_adapter: TypeAdapter[str] = TypeAdapter(EmailStr)
_INVALID_CREDENTIALS = "Invalid email or password"


@dataclass(frozen=True)
class LoginResult:
    token: str
    operator: Operator
    absolute_expires_at: datetime


def _operator(user: User) -> Operator:
    return Operator(
        id=user.id, email=user.email, display_name=user.display_name, role=Role(user.role)
    )


def _normalize_email(email: str) -> str:
    try:
        return _email_adapter.validate_python(email.strip()).lower()
    except PydanticValidationError as exc:
        raise ValidationFailed("Invalid email address") from exc


async def create_operator(
    sf: SessionFactory, *, email: str, display_name: str, password: str, role: Role = Role.OPERATOR
) -> uuid.UUID:
    email = _normalize_email(email)
    check_password_policy(password)
    password_hash = await to_thread.run_sync(hash_password, password)
    async with transaction(sf) as session:
        if await session.scalar(select(User.id).where(User.email == email)):
            raise Conflict("A user with this email already exists")
        user = User(
            email=email,
            display_name=display_name.strip() or email,
            password_hash=password_hash,
            role=role,
        )
        session.add(user)
        await session.flush()
        return user.id


async def login(
    sf: SessionFactory,
    redis: Redis,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> LoginResult:
    email = email.strip().lower()
    limit_key = f"acc:login-attempts:{ip or 'unknown'}:{email}"

    attempts = int(await redis.get(limit_key) or 0)
    if attempts >= settings.login_max_attempts:
        ttl = await redis.ttl(limit_key)
        raise RateLimited(
            "Too many failed login attempts. Try again later.", retry_after=max(ttl, 1)
        )

    async with sf() as session:
        user = await session.scalar(
            select(User).where(User.email == email, User.disabled_at.is_(None))
        )

    if user is None:
        await to_thread.run_sync(burn_verify_time, password)
        valid = False
    else:
        valid = await to_thread.run_sync(verify_password, user.password_hash, password)

    if not valid or user is None:
        if await redis.incr(limit_key) == 1:
            await redis.expire(limit_key, settings.login_window_seconds)
        raise AuthenticationFailed(_INVALID_CREDENTIALS)

    await redis.delete(limit_key)

    now = utcnow()
    absolute = now + timedelta(days=settings.session_absolute_days)
    token = new_session_token()
    user_changes: dict[str, object] = {"last_login_at": now}
    if needs_rehash(user.password_hash):
        user_changes["password_hash"] = await to_thread.run_sync(hash_password, password)

    async with transaction(sf) as session:
        session.add(
            AuthSession(
                id=session_id_for(token, settings.session_secret.get_secret_value()),
                user_id=user.id,
                created_at=now,
                last_seen_at=now,
                expires_at=session_expiry(
                    now=now,
                    absolute_expires_at=absolute,
                    idle=timedelta(hours=settings.session_idle_hours),
                ),
                absolute_expires_at=absolute,
                ip=ip,
                user_agent=(user_agent or "")[:512] or None,
            )
        )
        await session.execute(update(User).where(User.id == user.id).values(**user_changes))

    return LoginResult(token=token, operator=_operator(user), absolute_expires_at=absolute)


async def touch_session(
    sf: SessionFactory, *, session_id: str, absolute_expires_at: datetime, idle: timedelta
) -> None:
    """Slide the idle expiry forward. Called at most every few minutes per session."""
    now = utcnow()
    async with transaction(sf) as session:
        await session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(
                last_seen_at=now,
                expires_at=session_expiry(
                    now=now, absolute_expires_at=absolute_expires_at, idle=idle
                ),
            )
        )


async def logout(sf: SessionFactory, *, session_id: str) -> None:
    async with transaction(sf) as session:
        await session.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )


async def change_password(
    sf: SessionFactory,
    *,
    user_id: uuid.UUID,
    current_session_id: str,
    current_password: str,
    new_password: str,
) -> None:
    async with sf() as session:
        current_hash = await session.scalar(select(User.password_hash).where(User.id == user_id))
    if current_hash is None or not await to_thread.run_sync(
        verify_password, current_hash, current_password
    ):
        raise AuthenticationFailed("Current password is incorrect")
    if new_password == current_password:
        raise ValidationFailed("New password must differ from the current password")
    check_password_policy(new_password)
    new_hash = await to_thread.run_sync(hash_password, new_password)

    async with transaction(sf) as session:
        await session.execute(update(User).where(User.id == user_id).values(password_hash=new_hash))
        # Every other session is signed out; the one that made the change stays valid.
        await session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.id != current_session_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
