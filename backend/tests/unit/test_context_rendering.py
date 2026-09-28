"""acc.queries.context's render_system_prompt/render_task_prompt: pure string assembly,
no DB needed (PLAN-PHASE-2-M4.md §3)."""

from datetime import datetime

from acc.queries.context import RunContext, render_system_prompt, render_task_prompt
from acc.queries.governance import RuleView
from acc.queries.project_context import ContextEntryView

NOW = datetime(2026, 1, 1)


def make_context(**overrides: object) -> RunContext:
    base: dict[str, object] = {
        "agent_adapter": "claude_agent_sdk",
        "agent_model": "claude-sonnet-5",
        "agent_role": "Developer",
        "task_title": "Fix the thing",
        "task_description": "Do the fix.",
        "acceptance_criteria": [],
        "project_context": [],
        "governance_rules": [],
    }
    base.update(overrides)
    return RunContext(**base)  # type: ignore[arg-type]


def rule(**overrides: object) -> RuleView:
    base: dict[str, object] = {
        "id": None, "category": "security", "title": "Never expose secrets",
        "statement": "Never expose secrets.", "effect": "deny", "match": {},
        "applies_to": [], "priority": 100, "enabled": True, "created_at": NOW, "updated_at": NOW,
    }
    base.update(overrides)
    return RuleView(**base)  # type: ignore[arg-type]


class TestRenderSystemPrompt:
    def test_uses_the_known_adapter_role_not_the_raw_db_role(self) -> None:
        prompt = render_system_prompt(make_context(agent_role="whatever's in the DB"))
        assert "Developer agent" in prompt
        assert "whatever's in the DB" not in prompt

    def test_falls_back_to_the_db_role_for_an_unknown_adapter(self) -> None:
        context = make_context(agent_adapter="some_future_adapter", agent_role="Designer")
        assert render_system_prompt(context) == "Designer"

    def test_includes_every_enabled_rules_statement_and_effect(self) -> None:
        rules = [rule(statement="Never expose secrets.", effect="deny"),
                 rule(statement="Paid tools require approval.", effect="require_approval")]
        prompt = render_system_prompt(make_context(governance_rules=rules))
        assert "[deny] Never expose secrets." in prompt
        assert "[require_approval] Paid tools require approval." in prompt

    def test_no_rules_section_when_there_are_none(self) -> None:
        prompt = render_system_prompt(make_context(governance_rules=[]))
        assert "Global AI Rules" not in prompt


class TestRenderTaskPrompt:
    def test_includes_title_and_description(self) -> None:
        context = make_context(task_title="ACC-1: fix it", task_description="details")
        prompt = render_task_prompt(context)
        assert "ACC-1: fix it" in prompt
        assert "details" in prompt

    def test_empty_description_gets_a_placeholder(self) -> None:
        prompt = render_task_prompt(make_context(task_description=""))
        assert "(no description)" in prompt

    def test_includes_acceptance_criteria(self) -> None:
        prompt = render_task_prompt(make_context(acceptance_criteria=[{"text": "must pass CI"}]))
        assert "must pass CI" in prompt

    def test_includes_project_context_entries(self) -> None:
        entry = ContextEntryView(
            id=None, kind="gotcha", title="Watch out", body="It's tricky.",  # type: ignore[arg-type]
            priority=100, created_at=NOW, updated_at=NOW,
        )
        prompt = render_task_prompt(make_context(project_context=[entry]))
        assert "Watch out" in prompt
        assert "It's tricky." in prompt

    def test_no_sections_for_empty_criteria_or_context(self) -> None:
        prompt = render_task_prompt(make_context())
        assert "Acceptance criteria" not in prompt
        assert "Project context" not in prompt
