"""Idempotent seed: the default project, the three Phase 1 agents, and the Global AI
Governance rules from the original brief.

Safe to run on every deploy. Existing rows are never modified (an operator may have
edited or disabled an agent, or edited a governance rule); only missing rows are added,
and an event is emitted only for what was actually created.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from acc.commands._base import EventSink, unit_of_work
from acc.db.models import Agent, AgentCapability, GovernanceRule, Project
from acc.db.session import SessionFactory
from acc.domain.events import (
    SYSTEM,
    AgentRegistered,
    EventType,
    GovernanceRuleCreated,
    ProjectCreated,
)


@dataclass(frozen=True)
class AgentSeed:
    slug: str
    display_name: str
    role: str
    capabilities: dict[str, int]  # capability → priority
    intended_adapter: str


DEFAULT_PROJECT = {
    "slug": "acc",
    "name": "AI Command Center",
    "key_prefix": "ACC",
    "description": "Default project",
}

AGENTS: tuple[AgentSeed, ...] = (
    AgentSeed(
        slug="architect",
        display_name="Architect",
        role="Architect · Planner · Code reviewer",
        capabilities={"plan": 10, "review_code": 10},
        intended_adapter="openai_responses",
    ),
    AgentSeed(
        slug="developer",
        display_name="Developer",
        role="Developer · Implementation · Tests",
        capabilities={"implement": 10, "test": 10},
        intended_adapter="claude_agent_sdk",
    ),
    AgentSeed(
        slug="designer",
        display_name="Designer",
        role="UI/UX designer · Design review",
        capabilities={"design_ui": 10, "review_ui": 10},
        intended_adapter="claude_messages",
    ),
)


# (category, title, statement, effect, match) — statement/effect/category verbatim from
# the original governance brief. match (PLAN-PHASE-2-M4.md, "governance enforcement
# data"): the minimum structure the existing can_use_tool gate (runner/agent_sdk.py's
# rule_matches) can mechanically evaluate today — {"tool": ..., "command_regex": ...},
# checked against Bash's own `command` argument, since that's the only allowed tool with
# a free-text argument a regex can meaningfully match. Deliberately not exhaustive: a
# real attacker has many ways to reference a secret file or run a paid API call that no
# fixed regex list catches completely — the statement text still reaches the model as
# prompt guidance regardless (acc.queries.context.render_system_prompt), same as before.
# This is real, minimum, mechanical coverage added to existing rules, not a governance
# redesign: no new rules, no new effects, no approval-execution mechanism.
# advisory/allow rules keep match={} — the gate only ever evaluates deny/require_approval
# effects (runner/agent_sdk.py's make_can_use_tool), so a match here would be inert.
GOVERNANCE_RULES: tuple[tuple[str, str, str, str, dict[str, str]], ...] = (
    (
        "security", "Never expose secrets", "Never expose secrets.", "deny",
        {
            "tool": "Bash",
            "command_regex": r"\.env\b|\.pem\b|id_rsa|credentials\.json|\bprintenv\b",
        },
    ),
    (
        "security",
        "Never access protected credentials",
        "Never access protected credentials.",
        "deny",
        {
            "tool": "Bash",
            "command_regex": r"\.aws/credentials|\.ssh/id_|/etc/shadow|\.netrc\b|\.pgpass\b"
            r"|\.git-credentials\b",
        },
    ),
    (
        "security",
        "Never bypass approval requirements",
        "Never bypass approval requirements.",
        "deny",
        {"tool": "Bash", "command_regex": r"--no-verify\b|\bsudo\b"},
    ),
    (
        "tools",
        "Free tools can be used automatically",
        "Free tools can be used automatically.",
        "allow",
        {},
    ),
    (
        "tools", "Paid tools require approval", "Paid tools require approval.",
        "require_approval",
        {"tool": "Bash", "command_regex": r"\b(aws|gcloud|az|terraform|stripe|openai)\b"},
    ),
    (
        "tools",
        "Third-party services/APIs require approval",
        "Third-party services/APIs require approval before connection.",
        "require_approval",
        {"tool": "Bash", "command_regex": r"\b(curl|wget|nc|ssh)\b"},
    ),
    (
        "tools",
        "Unknown external tools require approval",
        "Unknown external tools require approval.",
        "require_approval",
        {
            "tool": "Bash",
            "command_regex": r"\b(pip3?\s+install|npm\s+install|apt(-get)?\s+install"
            r"|brew\s+install|gem\s+install)\b",
        },
    ),
    (
        "code",
        "Run tests before completing tasks",
        "Run tests before completing tasks.",
        "advisory",
        {},
    ),
    ("code", "Review changes before merge", "Review changes before merge.", "advisory", {}),
    (
        "code",
        "Never delete important files without approval",
        "Never delete important files without approval.",
        "require_approval",
        {"tool": "Bash", "command_regex": r"\brm\b|\brmdir\b|\bunlink\b|\bgit\s+rm\b"},
    ),
    (
        "infrastructure",
        "Production deployment requires approval",
        "Production deployment requires approval.",
        "require_approval",
        {
            "tool": "Bash",
            "command_regex": r"\bkubectl\b|\bdocker\s+push\b|\bhelm\s+(install|upgrade)\b"
            r"|\bterraform\s+apply\b",
        },
    ),
    (
        "infrastructure",
        "Database migrations require approval",
        "Database migrations require approval.",
        "require_approval",
        {"tool": "Bash", "command_regex": r"\balembic\s+(upgrade|downgrade)\b|\bmigrate\b"},
    ),
    (
        "infrastructure",
        "Destructive commands require approval",
        "Destructive commands require approval.",
        "require_approval",
        {
            "tool": "Bash",
            "command_regex": r"\brm\s+-[a-zA-Z]*f\b|\bdd\s+if=|\bmkfs\b|\bshutdown\b|\breboot\b"
            r"|drop\s+table|drop\s+database|push\s+--force|--force\b.*push",
        },
    ),
)


@dataclass
class SeedResult:
    created_projects: list[str] = field(default_factory=list)
    created_agents: list[str] = field(default_factory=list)
    created_governance_rules: list[str] = field(default_factory=list)


async def seed(sf: SessionFactory, sink: EventSink | None = None) -> SeedResult:
    result = SeedResult()
    async with unit_of_work(sf, sink) as uow:
        s = uow.session

        project_id = await s.scalar(
            insert(Project)
            .values(**DEFAULT_PROJECT)
            .on_conflict_do_nothing(index_elements=["slug"])
            .returning(Project.id)
        )
        if project_id is not None:
            result.created_projects.append(DEFAULT_PROJECT["slug"])
            await uow.emit(
                EventType.PROJECT_CREATED,
                ProjectCreated(
                    slug=DEFAULT_PROJECT["slug"],
                    name=DEFAULT_PROJECT["name"],
                    key_prefix=DEFAULT_PROJECT["key_prefix"],
                ),
                actor=SYSTEM,
                project_id=project_id,
            )

        for spec in AGENTS:
            agent_id = await s.scalar(
                insert(Agent)
                .values(
                    slug=spec.slug,
                    display_name=spec.display_name,
                    role=spec.role,
                    adapter="none",
                    config={"intended_adapter": spec.intended_adapter},
                )
                .on_conflict_do_nothing(index_elements=["slug"])
                .returning(Agent.id)
            )
            created = agent_id is not None
            if agent_id is None:
                agent_id = await s.scalar(select(Agent.id).where(Agent.slug == spec.slug))
            assert agent_id is not None

            await s.execute(
                insert(AgentCapability)
                .values(
                    [
                        {"agent_id": agent_id, "capability": cap, "priority": prio}
                        for cap, prio in spec.capabilities.items()
                    ]
                )
                .on_conflict_do_nothing()
            )
            if created:
                result.created_agents.append(spec.slug)
                await uow.emit(
                    EventType.AGENT_REGISTERED,
                    AgentRegistered(
                        slug=spec.slug,
                        display_name=spec.display_name,
                        role=spec.role,
                        capabilities=sorted(spec.capabilities),
                    ),
                    actor=SYSTEM,
                    agent_id=agent_id,
                    agent_slug=spec.slug,
                )

        for category, title, statement, effect, match in GOVERNANCE_RULES:
            rule_id = await s.scalar(
                insert(GovernanceRule)
                .values(
                    category=category, title=title, statement=statement, effect=effect,
                    match=match,
                )
                .on_conflict_do_nothing(index_elements=["title"])
                .returning(GovernanceRule.id)
            )
            if rule_id is not None:
                result.created_governance_rules.append(title)
                await uow.emit(
                    EventType.GOVERNANCE_RULE_CREATED,
                    GovernanceRuleCreated(
                        id=rule_id,
                        category=category,
                        title=title,
                        statement=statement,
                        effect=effect,
                    ),
                    actor=SYSTEM,
                )
    return result
