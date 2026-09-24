import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from acc.db.models import GovernanceRule
from acc.db.session import SessionFactory


@dataclass(frozen=True)
class RuleView:
    id: uuid.UUID
    category: str
    title: str
    statement: str
    effect: str
    applies_to: list[str]
    priority: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


def _view(rule: GovernanceRule) -> RuleView:
    return RuleView(
        id=rule.id,
        category=rule.category,
        title=rule.title,
        statement=rule.statement,
        effect=rule.effect,
        applies_to=rule.applies_to,
        priority=rule.priority,
        enabled=rule.enabled,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


async def list_rules(
    sf: SessionFactory, *, category: str | None = None, enabled: bool | None = None
) -> list[RuleView]:
    stmt = select(GovernanceRule)
    if category is not None:
        stmt = stmt.where(GovernanceRule.category == category)
    if enabled is not None:
        stmt = stmt.where(GovernanceRule.enabled == enabled)
    stmt = stmt.order_by(GovernanceRule.category, GovernanceRule.priority, GovernanceRule.title)
    async with sf() as s:
        rules = (await s.scalars(stmt)).all()
    return [_view(r) for r in rules]


async def get_rule(sf: SessionFactory, rule_id: uuid.UUID) -> RuleView | None:
    async with sf() as s:
        rule = await s.get(GovernanceRule, rule_id)
    return _view(rule) if rule else None
