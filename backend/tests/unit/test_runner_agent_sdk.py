"""runner/agent_sdk.py's own policy logic — pure, no SDK session needed (PLAN-PHASE-2-M4.
md §4.2/§11). Imported the same way tests/unit/test_runner_git_ops.py imports git_ops:
runner/ is deliberately not part of the acc package (PLAN-PHASE-2-M2.md's own reasoning
for keeping it outside the import-linter contract)."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

_RUNNER_DIR = Path(__file__).resolve().parents[3] / "runner"
if str(_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNER_DIR))
import agent_sdk  # type: ignore[import-not-found]  # noqa: E402


class FakeApi:
    """Records every reported call instead of making a real HTTP call."""

    def __init__(self, *, heartbeat_replies: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages: list[str] = []
        self.usage_reports: list[dict[str, Any]] = []
        self.terminal_calls: list[str] = []
        self._heartbeat_replies = list(heartbeat_replies or [])

    def context(self) -> dict[str, Any]:
        return {
            "system_prompt": "role", "task_prompt": "task", "governance_rules": [],
            "model": "claude-sonnet-5",
        }

    def heartbeat(self) -> dict[str, Any]:
        return self._heartbeat_replies.pop(0) if self._heartbeat_replies else {}

    def report_message(self, text: str) -> None:
        self.messages.append(text)

    def report_usage(self, model: str, usage: dict[str, Any], cost_usd: float) -> None:
        self.usage_reports.append({"model": model, "usage": usage, "cost_usd": cost_usd})

    def report_tool_call(
        self, tool: str, arguments: dict[str, Any], *, allowed: bool, denial_reason: str | None
    ) -> None:
        self.calls.append(
            {"tool": tool, "arguments": arguments, "allowed": allowed, "reason": denial_reason}
        )


def deny_rule(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "r1", "category": "infrastructure", "title": "t", "statement": "no rm -rf",
        "effect": "deny", "match": {"tool": "Bash", "command_regex": r"rm\s+-rf"}, "priority": 100,
    }
    base.update(overrides)
    return base


class TestRuleMatches:
    def test_empty_match_never_matches(self) -> None:
        assert agent_sdk.rule_matches({}, "Bash", {"command": "rm -rf /"}) is False

    def test_tool_mismatch_does_not_match(self) -> None:
        rule = {"tool": "Read"}
        assert agent_sdk.rule_matches(rule, "Bash", {"command": "rm -rf /"}) is False

    def test_command_regex_match(self) -> None:
        rule = {"tool": "Bash", "command_regex": r"rm\s+-rf"}
        assert agent_sdk.rule_matches(rule, "Bash", {"command": "rm -rf /tmp/x"}) is True

    def test_command_regex_no_match(self) -> None:
        rule = {"tool": "Bash", "command_regex": r"rm\s+-rf"}
        assert agent_sdk.rule_matches(rule, "Bash", {"command": "ls -la"}) is False


class TestCanUseTool:
    async def test_disallowed_tool_is_denied(self) -> None:
        api = FakeApi()
        can_use_tool = agent_sdk.make_can_use_tool([], api)
        result = await can_use_tool("WebFetch", {}, None)
        assert isinstance(result, agent_sdk.PermissionResultDeny)
        expected = {"tool": "WebFetch", "arguments": {}, "allowed": False, "reason": result.message}
        assert api.calls == [expected]

    async def test_allowed_tool_with_no_matching_rule_is_allowed(self) -> None:
        api = FakeApi()
        can_use_tool = agent_sdk.make_can_use_tool([deny_rule()], api)
        result = await can_use_tool("Bash", {"command": "ls -la"}, None)
        assert isinstance(result, agent_sdk.PermissionResultAllow)
        assert api.calls[0]["allowed"] is True

    async def test_matching_deny_rule_denies(self) -> None:
        api = FakeApi()
        can_use_tool = agent_sdk.make_can_use_tool([deny_rule()], api)
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, agent_sdk.PermissionResultDeny)
        assert "no rm -rf" in result.message
        assert api.calls[0]["allowed"] is False

    async def test_matching_require_approval_rule_also_fails_closed(self) -> None:
        """No approval mechanism exists (PLAN-PHASE-2-M2.md §4.3's precedent, restated
        with real stakes in PLAN-PHASE-2-M4.md §4.2): require_approval denies, exactly
        like deny."""
        api = FakeApi()
        rule = deny_rule(effect="require_approval")
        can_use_tool = agent_sdk.make_can_use_tool([rule], api)
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, agent_sdk.PermissionResultDeny)

    async def test_an_evaluation_error_fails_closed(self) -> None:
        """A rule with a garbage regex must never crash the loop into an unhandled
        exception that could plausibly leave a tool call unevaluated — it must deny."""
        api = FakeApi()
        broken_rule = deny_rule(match={"tool": "Bash", "command_regex": "(unterminated"})
        can_use_tool = agent_sdk.make_can_use_tool([broken_rule], api)
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, agent_sdk.PermissionResultDeny)
        assert api.calls[0]["allowed"] is False

    async def test_advisory_and_allow_rules_never_deny(self) -> None:
        api = FakeApi()
        rules = [deny_rule(effect="advisory"), deny_rule(effect="allow", id="r2")]
        can_use_tool = agent_sdk.make_can_use_tool(rules, api)
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, None)
        assert isinstance(result, agent_sdk.PermissionResultAllow)


class TestRunAgentCancellation:
    """A regression test for a real bug caught live during M4.1's own verification: the
    runner cooperatively noticing cancel_requested self-reported through /fail, racing
    the orchestrator's authoritative mark_cancelled and winning — a cancelled run showed
    up as failed. run_agent must return a distinct "cancelled" outcome that main.py's
    run_claude_agent_sdk never turns into a /complete or /fail call at all."""

    async def test_cancel_requested_mid_run_returns_the_cancelled_outcome(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        message = agent_sdk.AssistantMessage(
            content=[agent_sdk.TextBlock(text="working on it")], model="claude-sonnet-5"
        )

        async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
            yield message

        monkeypatch.setattr(agent_sdk, "query", fake_query)
        api = FakeApi(heartbeat_replies=[{}, {"cancel_requested": True}])

        outcome, text = await agent_sdk.run_agent(
            api=api, workspace_dir="/workspace/repo", max_turns=10, max_cost_usd=1.0
        )

        assert outcome == "cancelled"
        assert text == "cancelled"
        assert api.usage_reports == []  # no usage/cost recorded for a session that never finished


if __name__ == "__main__":
    pytest.main()
