"""Global AI Rules. See ARCHITECTURE.md §10 for the design and acc.domain.governance
for the vocabulary.

Access control (Phase 1.5): open to any authenticated operator, deliberately — see
acc.api.deps.OperatorDep. No endpoint in this codebase checks `role` yet, and the one
operator account that exists today has role=operator with no way to become admin, so
gating this now would just lock the only user out of the only UI that manages it.

    FUTURE NOTE: governance administration should become admin-only once multi-user
    access and real AI agents are introduced — at that point, add a `require_role`
    dependency here (Role.ADMIN) and a way to promote an operator (see
    docs/PLAN-PHASE-1.5.md §5 for the two options considered).
"""

import uuid

from fastapi import APIRouter, Query

from acc.api.deps import EventSinkDep, OperatorDep, SessionFactoryDep
from acc.api.schemas import (
    GovernanceRuleCreateIn,
    GovernanceRuleList,
    GovernanceRuleOut,
    GovernanceRulePatchIn,
)
from acc.commands import governance as governance_commands
from acc.db.session import SessionFactory
from acc.domain.errors import NotFound
from acc.domain.events import user_actor
from acc.domain.governance import RuleCategory
from acc.queries import governance as governance_queries

router = APIRouter(prefix="/governance-rules", tags=["governance"])


async def _rule(sf: SessionFactory, rule_id: uuid.UUID) -> GovernanceRuleOut:
    rule = await governance_queries.get_rule(sf, rule_id)
    if rule is None:
        raise NotFound(f"Governance rule {rule_id} not found", rule_id=str(rule_id))
    return GovernanceRuleOut.model_validate(rule)


@router.get("")
async def list_rules(
    _: OperatorDep,
    sf: SessionFactoryDep,
    category: RuleCategory | None = None,
    enabled: bool | None = Query(default=None),
) -> GovernanceRuleList:
    rules = await governance_queries.list_rules(sf, category=category, enabled=enabled)
    return GovernanceRuleList(items=[GovernanceRuleOut.model_validate(r) for r in rules])


@router.post("", status_code=201)
async def create_rule(
    body: GovernanceRuleCreateIn, operator: OperatorDep, sf: SessionFactoryDep, sink: EventSinkDep
) -> GovernanceRuleOut:
    rule_id = await governance_commands.create_rule(
        sf,
        sink,
        actor=user_actor(operator.id),
        created_by=operator.id,
        category=body.category.value,
        title=body.title,
        statement=body.statement,
        effect=body.effect.value,
    )
    return await _rule(sf, rule_id)


@router.get("/{rule_id}")
async def get_rule(rule_id: uuid.UUID, _: OperatorDep, sf: SessionFactoryDep) -> GovernanceRuleOut:
    return await _rule(sf, rule_id)


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID,
    body: GovernanceRulePatchIn,
    operator: OperatorDep,
    sf: SessionFactoryDep,
    sink: EventSinkDep,
) -> GovernanceRuleOut:
    changes = body.model_dump(exclude_unset=True, mode="json")
    await governance_commands.update_rule(
        sf, sink, actor=user_actor(operator.id), rule_id=rule_id, changes=changes
    )
    return await _rule(sf, rule_id)


@router.post("/{rule_id}/enable")
async def enable_rule(
    rule_id: uuid.UUID, operator: OperatorDep, sf: SessionFactoryDep, sink: EventSinkDep
) -> GovernanceRuleOut:
    await governance_commands.set_rule_enabled(
        sf, sink, actor=user_actor(operator.id), rule_id=rule_id, enabled=True
    )
    return await _rule(sf, rule_id)


@router.post("/{rule_id}/disable")
async def disable_rule(
    rule_id: uuid.UUID, operator: OperatorDep, sf: SessionFactoryDep, sink: EventSinkDep
) -> GovernanceRuleOut:
    await governance_commands.set_rule_enabled(
        sf, sink, actor=user_actor(operator.id), rule_id=rule_id, enabled=False
    )
    return await _rule(sf, rule_id)
