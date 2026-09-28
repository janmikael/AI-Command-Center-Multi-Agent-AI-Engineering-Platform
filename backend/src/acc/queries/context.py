"""The four-layer agent context (ARCHITECTURE.md §10.2), assembled for the first time in
Phase 2 M4: Global AI Rules + the agent's role are stable and cacheable (system_prompt);
project context + this task's own instructions are volatile, per-run (task_prompt). See
PLAN-PHASE-2-M4.md §3 for why the split follows exactly this line — it's the same split
the SDK's own prompt caching wants.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from acc.db.models import Agent, AgentRun, Project, Task
from acc.db.session import SessionFactory
from acc.queries import governance as governance_queries
from acc.queries import project_context as project_context_queries
from acc.queries.governance import RuleView
from acc.queries.project_context import ContextEntryView

# Keyed by acc.db.models.Agent.adapter. Static per-adapter role description (ARCHITECTURE
# §10.2 layer 2) — only one adapter is real so far; a future adapter gets its own entry
# here, not a generic template, since each one's constraints genuinely differ.
AGENT_ROLE_PROMPTS: dict[str, str] = {
    "claude_agent_sdk": (
        "You are the Developer agent in the AI Command Center. You implement, test, and "
        "prepare a small, reviewable change for exactly one task, working inside a "
        "repository workspace that has already been cloned and branched for you.\n\n"
        "Hard constraints, not suggestions:\n"
        "- Stay inside the current working directory. Never merge, deploy, or push to any "
        "branch other than the one you were given.\n"
        "- Never touch production credentials, databases, or infrastructure.\n"
        "- Some tool calls will be refused automatically by policy before they run. If one "
        "is refused, do not try to work around it — treat it as a hard stop for that "
        "approach and explain what you were unable to do.\n"
        "- If the task cannot be completed with the tools available to you, say so "
        "plainly and stop rather than guessing."
    ),
}


@dataclass(frozen=True)
class RunContext:
    agent_adapter: str
    agent_model: str | None
    agent_role: str
    task_title: str
    task_description: str
    acceptance_criteria: list[dict[str, Any]]
    project_context: list[ContextEntryView]
    governance_rules: list[RuleView]


async def get_run_context(sf: SessionFactory, run_id: uuid.UUID) -> RunContext | None:
    async with sf() as s:
        row = (
            await s.execute(
                select(Task, Agent, Project.slug)
                .join(AgentRun, AgentRun.task_id == Task.id)
                .join(Agent, Agent.id == AgentRun.agent_id)
                .join(Project, Project.id == Task.project_id)
                .where(AgentRun.id == run_id)
            )
        ).one_or_none()
    if row is None:
        return None
    task, agent, project_slug = row

    project_context = await project_context_queries.list_entries(sf, project_slug) or []
    governance_rules = await governance_queries.list_rules(sf, enabled=True)
    return RunContext(
        agent_adapter=agent.adapter,
        agent_model=agent.model,
        agent_role=agent.role,
        task_title=task.title,
        task_description=task.description,
        acceptance_criteria=task.acceptance_criteria,
        project_context=project_context,
        governance_rules=governance_rules,
    )


def render_system_prompt(context: RunContext) -> str:
    """Layers 1+2: the agent's role, plus every currently-enabled Global AI Rule as plain
    English guidance. Deliberately advisory-in-prompt for every rule here, even
    deny/require_approval ones — the *mechanical* backstop is can_use_tool matching a
    rule's `match` pattern at the point of use (PLAN-PHASE-2-M4.md §4.2), not this text;
    most seeded rules have an empty `match` (nothing yet has authored one), so this prompt
    is the only place their statement reaches the model at all."""
    role = AGENT_ROLE_PROMPTS.get(context.agent_adapter, context.agent_role)
    lines = [role]
    if context.governance_rules:
        lines += [
            "",
            "Global AI Rules. Some tool calls that violate a deny or require_approval "
            "rule below will be refused automatically, regardless of what you decide:",
        ]
        lines += [f"- [{rule.effect}] {rule.statement}" for rule in context.governance_rules]
    return "\n".join(lines)


def render_task_prompt(context: RunContext) -> str:
    """Layers 3+4: what this project wants you to know, then what this task actually is."""
    lines = [f"Task: {context.task_title}", "", context.task_description or "(no description)"]
    if context.acceptance_criteria:
        lines += ["", "Acceptance criteria:"]
        lines += [f"- {c.get('text', '')}" for c in context.acceptance_criteria]
    if context.project_context:
        lines += ["", "Project context:"]
        for entry in context.project_context:
            lines += [f"### {entry.title} ({entry.kind})", entry.body]
    return "\n".join(lines)
