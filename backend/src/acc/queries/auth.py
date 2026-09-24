from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from acc.db.models import AuthSession, User
from acc.db.session import SessionFactory
from acc.domain.auth import Operator, Role


@dataclass(frozen=True)
class ActiveSession:
    session_id: str
    last_seen_at: datetime
    absolute_expires_at: datetime
    operator: Operator


async def get_active_session(
    sf: SessionFactory, *, session_id: str, now: datetime
) -> ActiveSession | None:
    stmt = (
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.id == session_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
            AuthSession.absolute_expires_at > now,
            User.disabled_at.is_(None),
        )
    )
    async with sf() as session:
        row = (await session.execute(stmt)).first()
    if row is None:
        return None
    auth_session, user = row
    return ActiveSession(
        session_id=auth_session.id,
        last_seen_at=auth_session.last_seen_at,
        absolute_expires_at=auth_session.absolute_expires_at,
        operator=Operator(
            id=user.id, email=user.email, display_name=user.display_name, role=Role(user.role)
        ),
    )
