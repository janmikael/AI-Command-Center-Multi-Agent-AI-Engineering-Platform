"""Request/response models: the public API contract (exported to the web client via OpenAPI)."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from acc.domain.auth import Operator, Role


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─── Health ──────────────────────────────────────────────────────────────
class HealthOut(ApiModel):
    status: Literal["ok"]


class CheckOut(ApiModel):
    ok: bool
    detail: str | None = None


class ReadinessOut(ApiModel):
    status: Literal["ready", "not_ready"]
    version: str
    checks: dict[str, CheckOut]


# ─── Auth ────────────────────────────────────────────────────────────────
class LoginIn(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(ApiModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class OperatorOut(ApiModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: Role

    @classmethod
    def from_domain(cls, operator: Operator) -> "OperatorOut":
        return cls(
            id=operator.id,
            email=operator.email,
            display_name=operator.display_name,
            role=operator.role,
        )
