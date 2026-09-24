import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column

from acc.db.base import Base
from acc.ids import new_id


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('operator', 'viewer', 'admin')", name="role"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="operator")
    totp_secret: Mapped[str | None] = mapped_column(Text)  # reserved for optional TOTP
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_login_at: Mapped[datetime | None]
    disabled_at: Mapped[datetime | None]


class AuthSession(Base):
    __tablename__ = "sessions"

    # HMAC-SHA256 of the cookie token. The raw token is never stored.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(index=True)  # idle (sliding) expiry
    absolute_expires_at: Mapped[datetime]
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None]
