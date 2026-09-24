"""Idempotent seed: the default project and the three Phase 1 agents.

Safe to run on every deploy. Existing rows are never modified (an operator may have
edited or disabled an agent); only missing rows and capabilities are added, and an
event is emitted only for what was actually created.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from acc.commands._base import EventSink, unit_of_work
from acc.db.models import Agent, AgentCapability, Project
from acc.db.session import SessionFactory
from acc.domain.events import SYSTEM, AgentRegistered, EventType, ProjectCreated


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


@dataclass
class SeedResult:
    created_projects: list[str] = field(default_factory=list)
    created_agents: list[str] = field(default_factory=list)


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
    return result
