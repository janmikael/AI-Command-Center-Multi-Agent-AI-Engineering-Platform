"""Identifier generation. All primary keys are UUIDv7 (time-sortable)."""

import uuid
from datetime import UTC, datetime

from uuid_utils.compat import uuid7


def new_id() -> uuid.UUID:
    return uuid7()


def utcnow() -> datetime:
    return datetime.now(UTC)
