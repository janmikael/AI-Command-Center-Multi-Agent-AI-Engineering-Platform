"""runner/agent_sdk.py's own policy/lifecycle/redaction logic — pure, no real SDK
session needed (PLAN-PHASE-2-M4.md §4.2/§11, hardened by PLAN-PHASE-2-M4.2.md). Imported
the same way tests/unit/test_runner_git_ops.py imports git_ops: runner/ is deliberately
not part of the acc package (PLAN-PHASE-2-M2.md's own reasoning for keeping it outside
the import-linter contract)."""

import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_RUNNER_DIR = Path(__file__).resolve().parents[3] / "runner"
if str(_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNER_DIR))
import agent_sdk  # type: ignore[import-not-found]  # noqa: E402


@dataclass
class FakeContext:
    tool_use_id: str | None = "tu_1"


class FakeApi:
    """Records every reported call instead of making a real HTTP call."""

    def __init__(self, *, heartbeat_replies: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.completions: list[dict[str, Any]] = []
        self.messages: list[str] = []
        self.usage_reports: list[dict[str, Any]] = []
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

    def report_usage(
        self, model: str, usage: dict[str, Any], cost_usd: float,
        *, cost_source: str = "provider_reported",
    ) -> None:
        self.usage_reports.append(
            {"model": model, "usage": usage, "cost_usd": cost_usd, "cost_source": cost_source}
        )

    def report_tool_call(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        allowed: bool,
        denial_reason: str | None,
        tool_use_id: str | None = None,
        rule_id: str | None = None,
        blocked_by_effect: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "tool": tool, "arguments": arguments, "allowed": allowed,
                "reason": denial_reason, "tool_use_id": tool_use_id,
                "rule_id": rule_id, "blocked_by_effect": blocked_by_effect,
            }
        )

    def report_tool_call_complete(
        self, tool_use_id: str, *, duration_ms: int | None, exit_code: int | None,
        result_excerpt: str | None,
    ) -> None:
        self.completions.append(
            {
                "tool_use_id": tool_use_id, "duration_ms": duration_ms,
                "exit_code": exit_code, "result_excerpt": result_excerpt,
            }
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


class TestRuleMatchesPathRegex:
    """PLAN-PHASE-2-M4.2.md decision 1: "file/path patterns" — checked against
    Read/Write/Edit's own file_path argument, the third and last supported match key."""

    def test_path_regex_match(self) -> None:
        rule = {"tool": "Read", "path_regex": r"\.env$|credentials\.json$"}
        assert agent_sdk.rule_matches(rule, "Read", {"file_path": "/repo/.env"}) is True

    def test_path_regex_no_match(self) -> None:
        rule = {"tool": "Read", "path_regex": r"\.env$"}
        assert agent_sdk.rule_matches(rule, "Read", {"file_path": "/repo/main.py"}) is False

    def test_path_regex_with_no_file_path_argument_does_not_match(self) -> None:
        """A Bash call has no file_path — a path_regex rule must not match it by
        accident against an empty string standing in for "no path"."""
        rule = {"path_regex": r".*"}
        assert agent_sdk.rule_matches(rule, "Bash", {"command": "ls"}) is False

    def test_command_regex_and_path_regex_are_anded(self) -> None:
        """Deliberately simple (PLAN-PHASE-2-M4.2.md decision 1: no complex policy
        language) — every present key must match. A rule needing "this OR that" across
        tools is two separate rows, not supported here."""
        rule = {"command_regex": r"rm", "path_regex": r"\.env$"}
        # Read has no `command` key at all -> command_regex can never match -> denied
        # overall requires both, so this must fail even though path_regex alone would
        # have matched.
        assert agent_sdk.rule_matches(rule, "Read", {"file_path": "/repo/.env"}) is False


class TestRedaction:
    """PLAN-PHASE-2-M4.2.md §4/decision 3: layered, not exhaustive."""

    def test_redacts_known_secret_env_var_assignment(self) -> None:
        text = agent_sdk.redact("export AWS_SECRET_ACCESS_KEY=abc123xyz")
        assert "abc123xyz" not in text
        assert "AWS_SECRET_ACCESS_KEY=" in text
        assert agent_sdk.REDACTED in text

    def test_leaves_a_plain_env_var_alone(self) -> None:
        text = agent_sdk.redact("export PATH=/usr/bin:/bin")
        assert text == "export PATH=/usr/bin:/bin"

    def test_redacts_common_token_formats(self) -> None:
        for token in [
            "sk-abcdefghijklmnopqrstuvwx",
            "ghp_" + "a" * 36,
            "AKIAABCDEFGHIJKLMNOP",
        ]:
            text = agent_sdk.redact(f"curl -H 'Authorization: Bearer {token}' https://x")
            assert token not in text

    def test_redacts_password_and_key_fields(self) -> None:
        text = agent_sdk.redact('psql --password=hunter2')
        assert "hunter2" not in text
        text2 = agent_sdk.redact('api_key: "sk-live-abc123"')
        assert "sk-live-abc123" not in text2

    def test_leaves_unrelated_text_alone(self) -> None:
        text = agent_sdk.redact("ls -la /tmp && echo done")
        assert text == "ls -la /tmp && echo done"

    def test_redact_value_walks_nested_structures(self) -> None:
        value = {
            "command": "export API_KEY=secretvalue123",
            "nested": ["ok", {"note": "password=hunter2"}],
        }
        redacted = agent_sdk.redact_value(value)
        assert "secretvalue123" not in str(redacted)
        assert "hunter2" not in str(redacted)
        assert redacted["nested"][0] == "ok"

    def test_bounded_excerpt_truncates(self) -> None:
        text = "x" * (agent_sdk.MAX_RESULT_EXCERPT + 500)
        excerpt = agent_sdk.bounded_excerpt(text)
        assert len(excerpt) <= agent_sdk.MAX_RESULT_EXCERPT + len("... [truncated]")
        assert excerpt.endswith("... [truncated]")

    def test_bounded_excerpt_also_redacts(self) -> None:
        excerpt = agent_sdk.bounded_excerpt("token: sk-abcdefghijklmnopqrstuvwx")
        assert "sk-abcdefghijklmnopqrstuvwx" not in excerpt


class TestCanUseTool:
    async def test_disallowed_tool_is_denied(self) -> None:
        api = FakeApi()
        can_use_tool = agent_sdk.make_can_use_tool([], api, agent_sdk._ToolCallTracker())
        result = await can_use_tool("WebFetch", {}, FakeContext())
        assert isinstance(result, agent_sdk.PermissionResultDeny)
        assert api.calls[0]["allowed"] is False
        assert api.calls[0]["tool"] == "WebFetch"

    async def test_allowed_tool_with_no_matching_rule_is_allowed(self) -> None:
        api = FakeApi()
        tracker = agent_sdk._ToolCallTracker()
        can_use_tool = agent_sdk.make_can_use_tool([deny_rule()], api, tracker)
        result = await can_use_tool("Bash", {"command": "ls -la"}, FakeContext(tool_use_id="tu_1"))
        assert isinstance(result, agent_sdk.PermissionResultAllow)
        assert api.calls[0]["allowed"] is True
        assert api.calls[0]["tool_use_id"] == "tu_1"
        assert tracker.duration_ms("tu_1") is not None  # start() was called

    async def test_matching_deny_rule_denies_and_reports_rule_id(self) -> None:
        api = FakeApi()
        can_use_tool = agent_sdk.make_can_use_tool(
            [deny_rule()], api, agent_sdk._ToolCallTracker()
        )
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, FakeContext())
        assert isinstance(result, agent_sdk.PermissionResultDeny)
        assert "no rm -rf" in result.message
        assert api.calls[0]["allowed"] is False
        assert api.calls[0]["rule_id"] == "r1"
        assert api.calls[0]["blocked_by_effect"] == "deny"
        assert api.calls[0]["tool_use_id"] is None  # denied calls never execute

    async def test_matching_require_approval_rule_also_fails_closed(self) -> None:
        """No approval mechanism exists (PLAN-PHASE-2-M2.md §4.3's precedent, restated
        with real stakes in PLAN-PHASE-2-M4.md §4.2): require_approval denies, exactly
        like deny — but is tagged distinctly for approval-state preparation
        (PLAN-PHASE-2-M4.2.md §3)."""
        api = FakeApi()
        rule = deny_rule(effect="require_approval")
        can_use_tool = agent_sdk.make_can_use_tool([rule], api, agent_sdk._ToolCallTracker())
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, FakeContext())
        assert isinstance(result, agent_sdk.PermissionResultDeny)
        assert api.calls[0]["blocked_by_effect"] == "require_approval"

    async def test_an_evaluation_error_fails_closed(self) -> None:
        """A rule with a garbage regex must never crash the loop into an unhandled
        exception that could plausibly leave a tool call unevaluated — it must deny."""
        api = FakeApi()
        broken_rule = deny_rule(match={"tool": "Bash", "command_regex": "(unterminated"})
        can_use_tool = agent_sdk.make_can_use_tool(
            [broken_rule], api, agent_sdk._ToolCallTracker()
        )
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, FakeContext())
        assert isinstance(result, agent_sdk.PermissionResultDeny)
        assert api.calls[0]["allowed"] is False

    async def test_advisory_and_allow_rules_never_deny(self) -> None:
        api = FakeApi()
        rules = [deny_rule(effect="advisory"), deny_rule(effect="allow", id="r2")]
        can_use_tool = agent_sdk.make_can_use_tool(rules, api, agent_sdk._ToolCallTracker())
        result = await can_use_tool("Bash", {"command": "rm -rf /"}, FakeContext())
        assert isinstance(result, agent_sdk.PermissionResultAllow)



class TestRunApiRedaction:
    """RunApi.report_tool_call/report_tool_call_complete are the actual layer that
    applies redaction (PLAN-PHASE-2-M4.2.md §4) — can_use_tool just calls them with raw
    arguments, matching every other reporting call in this module."""

    def test_report_tool_call_redacts_arguments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: dict[str, Any] = {}

        def fake_post(self: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
            posted.update(body)
            return {}

        monkeypatch.setattr(agent_sdk.RunApi, "post", fake_post)
        api = agent_sdk.RunApi("http://x", "run-1", "token")

        api.report_tool_call(
            "Bash", {"command": "export API_KEY=verysecretvalue"},
            allowed=True, denial_reason=None,
        )

        assert "verysecretvalue" not in str(posted["arguments"])

    def test_report_tool_call_complete_redacts_and_bounds_result_excerpt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        posted: dict[str, Any] = {}

        def fake_post(self: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
            posted.update(body)
            return {}

        monkeypatch.setattr(agent_sdk.RunApi, "post", fake_post)
        api = agent_sdk.RunApi("http://x", "run-1", "token")

        api.report_tool_call_complete(
            "tu_1", duration_ms=5, exit_code=0,
            result_excerpt="output contains sk-abcdefghijklmnopqrstuvwx",
        )

        assert "sk-abcdefghijklmnopqrstuvwx" not in posted["result_excerpt"]


class TestToolCallTracker:
    def test_duration_is_measured_and_consumed_once(self) -> None:
        tracker = agent_sdk._ToolCallTracker()
        tracker.start("tu_1")
        duration = tracker.duration_ms("tu_1")
        assert duration is not None
        assert duration >= 0
        assert tracker.duration_ms("tu_1") is None  # popped, not re-readable

    def test_unknown_tool_use_id_returns_none(self) -> None:
        tracker = agent_sdk._ToolCallTracker()
        assert tracker.duration_ms("never-started") is None


class TestExtractToolResponse:
    def test_dict_with_output_key(self) -> None:
        exit_code, text = agent_sdk._extract_tool_response({"exit_code": 0, "output": "hi"})
        assert exit_code == 0
        assert text == "hi"

    def test_dict_with_no_recognized_text_key_falls_back_to_json(self) -> None:
        exit_code, text = agent_sdk._extract_tool_response({"weird": "shape"})
        assert exit_code is None
        assert text is not None
        assert "weird" in text

    def test_plain_string(self) -> None:
        assert agent_sdk._extract_tool_response("just some output") == (None, "just some output")

    def test_none(self) -> None:
        assert agent_sdk._extract_tool_response(None) == (None, None)


class TestPostToolUseHook:
    async def test_completes_the_call_and_clears_the_tracker(self) -> None:
        api = FakeApi()
        tracker = agent_sdk._ToolCallTracker()
        tracker.start("tu_1")
        hook = agent_sdk.make_post_tool_use_hook(api, tracker)

        result = await hook(
            {"tool_use_id": "tu_1", "tool_response": {"exit_code": 0, "output": "done"}},
            None, None,
        )

        assert result == {}
        assert api.completions[0]["tool_use_id"] == "tu_1"
        assert api.completions[0]["exit_code"] == 0
        assert api.completions[0]["duration_ms"] is not None
        assert tracker.duration_ms("tu_1") is None  # already consumed by the hook

    async def test_missing_tool_use_id_is_a_harmless_noop(self) -> None:
        api = FakeApi()
        hook = agent_sdk.make_post_tool_use_hook(api, agent_sdk._ToolCallTracker())
        result = await hook({"tool_response": "x"}, None, None)
        assert result == {}
        assert api.completions == []


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

        outcome, text, details = await agent_sdk.run_agent(
            api=api, workspace_dir="/workspace/repo", max_turns=10, max_cost_usd=1.0
        )

        assert outcome == "cancelled"
        assert text == "cancelled"
        assert details is None
        assert api.usage_reports == []  # this message carried no usage, so nothing to report

    async def test_cancellation_still_reports_partial_usage_if_any_was_seen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PLAN-PHASE-2-M4.2.md §5: a run that spent real tokens before being cut off
        must not silently show $0.00 — reported as cost_source="estimated" since no
        provider-confirmed cost figure exists for a session that never finished."""
        message = agent_sdk.AssistantMessage(
            content=[agent_sdk.TextBlock(text="working on it")],
            model="claude-sonnet-5",
            usage={"input_tokens": 500, "output_tokens": 50},
        )

        async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
            yield message

        monkeypatch.setattr(agent_sdk, "query", fake_query)
        api = FakeApi(heartbeat_replies=[{}, {"cancel_requested": True}])

        outcome, _text, _details = await agent_sdk.run_agent(
            api=api, workspace_dir="/workspace/repo", max_turns=10, max_cost_usd=1.0
        )

        assert outcome == "cancelled"
        assert len(api.usage_reports) == 1
        report = api.usage_reports[0]
        assert report["usage"]["inputTokens"] == 500
        assert report["usage"]["outputTokens"] == 50
        assert report["cost_source"] == "estimated"


class TestRunAgentFailureClassification:
    async def test_no_result_message_is_a_failure_with_no_details_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
            return
            yield  # pragma: no cover - makes this an async generator with no items

        monkeypatch.setattr(agent_sdk, "query", fake_query)
        api = FakeApi()

        outcome, text, details = await agent_sdk.run_agent(
            api=api, workspace_dir="/workspace/repo", max_turns=10, max_cost_usd=1.0
        )

        assert outcome == "failed"
        assert "without a result" in text
        assert details is None

    async def test_an_exception_mid_session_is_a_failure_not_an_unhandled_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
            raise RuntimeError("CLI process died")
            yield  # pragma: no cover

        monkeypatch.setattr(agent_sdk, "query", fake_query)
        api = FakeApi()

        outcome, text, _details = await agent_sdk.run_agent(
            api=api, workspace_dir="/workspace/repo", max_turns=10, max_cost_usd=1.0
        )

        assert outcome == "failed"
        assert "CLI process died" in text

    async def test_result_message_error_surfaces_structured_details(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = agent_sdk.ResultMessage(
            subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True,
            num_turns=10, session_id="s1", stop_reason="max_turns", api_error_status=None,
        )

        async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
            yield result

        monkeypatch.setattr(agent_sdk, "query", fake_query)
        api = FakeApi()

        outcome, text, details = await agent_sdk.run_agent(
            api=api, workspace_dir="/workspace/repo", max_turns=10, max_cost_usd=1.0
        )

        assert outcome == "failed"
        assert text == "max_turns"
        assert details is not None
        assert details["subtype"] == "error_max_turns"
        assert details["stop_reason"] == "max_turns"


if __name__ == "__main__":
    pytest.main()
