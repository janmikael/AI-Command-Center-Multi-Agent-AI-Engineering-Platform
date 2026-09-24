"""Global AI Rules: the only write path for governance_rules.

Governance permissions (Phase 1.5): open to any authenticated operator — no role check
yet. See acc.api.routers.governance for the reasoning and the future note on admin-only
access once multi-user access and real AI agents exist.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acc.commands._base import EventSink, unit_of_work
from acc.db.models import GovernanceRule
from acc.db.session import SessionFactory
from acc.domain.errors import Conflict, NotFound
from acc.domain.events import (
    EventType,
    FieldChange,
    GovernanceRuleCreated,
    GovernanceRuleDisabled,
    GovernanceRuleEnabled,
    GovernanceRuleUpdated,
)

EDITABLE_FIELDS = frozenset({"title", "statement", "effect"})


async def _lock_rule(session: AsyncSession, rule_id: uuid.UUID) -> GovernanceRule:
    rule = await session.scalar(
        select(GovernanceRule).where(GovernanceRule.id == rule_id).with_for_update()
    )
    if rule is None:
        raise NotFound(f"Governance rule {rule_id} not found", rule_id=str(rule_id))
    return rule


async def create_rule(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    created_by: uuid.UUID | None,
    category: str,
    title: str,
    statement: str,
    effect: str,
) -> uuid.UUID:
    async with unit_of_work(sf, sink) as uow:
        clash = await uow.session.scalar(
            select(GovernanceRule.id).where(GovernanceRule.title == title)
        )
        if clash is not None:
            raise Conflict("A governance rule with this title already exists", title=title)
        rule = GovernanceRule(
            category=category,
            title=title,
            statement=statement,
            effect=effect,
            created_by=created_by,
        )
        uow.session.add(rule)
        await uow.session.flush()
        await uow.emit(
            EventType.GOVERNANCE_RULE_CREATED,
            GovernanceRuleCreated(
                id=rule.id, category=category, title=title, statement=statement, effect=effect
            ),
            actor=actor,
        )
        return rule.id


async def update_rule(
    sf: SessionFactory,
    sink: EventSink | None,
    *,
    actor: str,
    rule_id: uuid.UUID,
    changes: dict[str, Any],
) -> None:
    """Edits title/statement/effect only — category is fixed at creation, and
    match/applies_to/priority stay API-only for this phase (see PLAN-PHASE-1.5.md §1)."""
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable through update_rule: {sorted(unknown)}")

    async with unit_of_work(sf, sink) as uow:
        rule = await _lock_rule(uow.session, rule_id)
        diff = {
            field: FieldChange(before=getattr(rule, field), after=value)
            for field, value in changes.items()
            if getattr(rule, field) != value
        }
        if not diff:
            return  # no-op edit, no event
        if "title" in diff:
            clash = await uow.session.scalar(
                select(GovernanceRule.id).where(
                    GovernanceRule.title == diff["title"].after, GovernanceRule.id != rule.id
                )
            )
            if clash is not None:
                raise Conflict(
                    "A governance rule with this title already exists",
                    title=diff["title"].after,
                )
        for field, change in diff.items():
            setattr(rule, field, change.after)
        await uow.emit(
            EventType.GOVERNANCE_RULE_UPDATED,
            GovernanceRuleUpdated(id=rule.id, changes=diff),
            actor=actor,
        )


async def set_rule_enabled(
    sf: SessionFactory, sink: EventSink | None, *, actor: str, rule_id: uuid.UUID, enabled: bool
) -> None:
    async with unit_of_work(sf, sink) as uow:
        rule = await _lock_rule(uow.session, rule_id)
        if rule.enabled == enabled:
            return  # already in the requested state, no-op
        rule.enabled = enabled
        event_type = (
            EventType.GOVERNANCE_RULE_ENABLED if enabled else EventType.GOVERNANCE_RULE_DISABLED
        )
        payload_cls = GovernanceRuleEnabled if enabled else GovernanceRuleDisabled
        await uow.emit(event_type, payload_cls(id=rule.id, title=rule.title), actor=actor)
