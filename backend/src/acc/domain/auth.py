"""Operator identity rules: roles, password policy, session lifetime."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from acc.domain.errors import ValidationFailed

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256


class Role(StrEnum):
    OPERATOR = "operator"
    VIEWER = "viewer"
    ADMIN = "admin"


@dataclass(frozen=True)
class Operator:
    id: uuid.UUID
    email: str
    display_name: str
    role: Role


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationFailed(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")
    if len(set(password)) < 4:
        raise ValidationFailed("Password is too repetitive")


def session_expiry(*, now: datetime, absolute_expires_at: datetime, idle: timedelta) -> datetime:
    """Sliding idle expiry that can never extend past the absolute limit."""
    return min(now + idle, absolute_expires_at)
