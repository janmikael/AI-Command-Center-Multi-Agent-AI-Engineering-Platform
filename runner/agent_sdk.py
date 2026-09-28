"""The Claude Agent SDK adapter (PLAN-PHASE-2-M4.md §2). Runs exactly one query() session
to completion inside the sandbox — no resume, no multi-session continuation (that's a
real, separate feature for a later milestone, not built here just because the field
exists on ClaudeAgentOptions). Reports progress/tool-calls/usage back through the
internal API exactly like every other runner path: no direct database access, ever
(PLAN-PHASE-2-M2.md §1.6).

Policy is enforced here, not decided here: governance_rules are fetched once, from the
context endpoint, at run start (a snapshot — a rule edited mid-run changes the next run's
enforcement, not this one's). can_use_tool is the sole gate this milestone relies on;
permission_mode is deliberately left unset (the SDK's own default) rather than something
that could plausibly bypass a callback we're relying on to be authoritative — confirming
the exact interaction between the two is a live-verification item (PLAN-PHASE-2-M4.md
§12), not something asserted here with more confidence than static reading supports.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    query,
)

REQUEST_TIMEOUT_S = 10
MAX_REPORTED_TEXT = 4000

# Fixed, not configurable per task or per agent (PLAN-PHASE-2-M4.md §4.1) — no MCP
# gateway or per-task tool policy exists yet to make widening this safe. No Web* tools:
# each would need its own egress-allowlist entry beyond the git remote and the Anthropic
# API, which this milestone doesn't add.
ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]


class RunApi:
    """A small, blocking HTTP client for the internal API. Not shared with main.py's own
    module-level functions — those close over that module's globals, and this is the one
    new caller that benefits from an instance instead (PLAN-PHASE-2-M4.md §9)."""

    def __init__(self, base_url: str, run_id: str, run_token: str) -> None:
        self._base_url = base_url
        self._run_id = run_id
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {run_token}",
        }

    def get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - fixed internal URL, not user input
            f"{self._base_url}{path}", headers=self._headers, method="GET"
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:  # noqa: S310
            result: dict[str, Any] = json.loads(response.read())
            return result

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310
            f"{self._base_url}{path}",
            data=json.dumps(body).encode(),
            headers=self._headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:  # noqa: S310
            raw = response.read()
            return json.loads(raw) if raw else {}

    def try_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.post(path, body)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"POST {path} failed: {exc}", file=sys.stderr)
            return {}

    def context(self) -> dict[str, Any]:
        return self.get(f"/internal/runs/{self._run_id}/context")

    def heartbeat(self) -> dict[str, Any]:
        return self.try_post(f"/internal/runs/{self._run_id}/heartbeat", {})

    def report_message(self, text: str) -> None:
        self.try_post(f"/internal/runs/{self._run_id}/messages", {"text": text[:MAX_REPORTED_TEXT]})

    def report_tool_call(
        self, tool: str, arguments: dict[str, Any], *, allowed: bool, denial_reason: str | None
    ) -> None:
        self.try_post(
            f"/internal/runs/{self._run_id}/tool-calls",
            {
                "tool": tool,
                "arguments": arguments,
                "allowed": allowed,
                "denial_reason": denial_reason,
            },
        )

    def report_usage(self, model: str, usage: dict[str, Any], cost_usd: float) -> None:
        self.try_post(
            f"/internal/runs/{self._run_id}/usage",
            {
                "model": model,
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
                "cache_read_tokens": usage.get("cacheReadInputTokens", 0),
                "cache_write_tokens": usage.get("cacheCreationInputTokens", 0),
                "cost_usd": cost_usd,
            },
        )


def rule_matches(match: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Mechanical matching only. A rule with an empty `match` (every seeded governance
    rule today — nothing has authored one yet, PLAN-PHASE-2-M4.md §4.2) can never match
    here; its statement still reaches the model as plain-English prompt text
    (acc.queries.context.render_system_prompt), but is not something this gate can
    enforce as a hard stop until an operator gives it a real pattern."""
    if not match:
        return False
    if "tool" in match and match["tool"] != tool_name:
        return False
    if "command_regex" in match:
        command = str(tool_input.get("command", ""))
        if not re.search(match["command_regex"], command):
            return False
    return True


def make_can_use_tool(rules: list[dict[str, Any]], api: RunApi) -> Any:
    ordered = sorted(rules, key=lambda r: r["priority"])

    async def can_use_tool(
        tool_name: str, tool_input: dict[str, Any], _context: Any
    ) -> PermissionResultAllow | PermissionResultDeny:
        try:
            if tool_name not in ALLOWED_TOOLS:
                reason = f"{tool_name} is not on this agent's allowed tool list"
                api.report_tool_call(tool_name, tool_input, allowed=False, denial_reason=reason)
                return PermissionResultDeny(message=reason)

            for rule in ordered:
                if rule["effect"] not in ("deny", "require_approval"):
                    continue
                if not rule_matches(rule.get("match") or {}, tool_name, tool_input):
                    continue
                # require_approval fails closed: no approval mechanism exists yet
                # (PLAN-PHASE-2-M2.md §4.3's precedent, now with real stakes — see
                # PLAN-PHASE-2-M4.md §4.2).
                reason = f"Denied by policy ({rule['effect']}): {rule['statement']}"
                api.report_tool_call(tool_name, tool_input, allowed=False, denial_reason=reason)
                return PermissionResultDeny(message=reason)

            api.report_tool_call(tool_name, tool_input, allowed=True, denial_reason=None)
            return PermissionResultAllow()
        except Exception as exc:  # fail closed when permission cannot be determined
            reason = f"policy evaluation failed, denying by default: {exc}"
            api.report_tool_call(tool_name, tool_input, allowed=False, denial_reason=reason)
            return PermissionResultDeny(message=reason)

    return can_use_tool


def _report_usage(api: RunApi, result: ResultMessage, fallback_model: str | None) -> None:
    if result.model_usage:
        for model, usage in result.model_usage.items():
            api.report_usage(model, usage, float(usage.get("costUSD", 0.0)))
        return
    if result.total_cost_usd is None:
        return
    # Best-effort fallback for a CLI/SDK version that doesn't populate model_usage: field
    # names here follow the Anthropic Messages API's own usage object convention, but
    # this path is unconfirmed against a real call (PLAN-PHASE-2-M4.md §12) — model_usage
    # above is the confirmed shape (verified by introspecting the installed SDK package).
    usage = result.usage or {}
    api.report_usage(
        fallback_model or "unknown",
        {
            "inputTokens": usage.get("input_tokens", 0),
            "outputTokens": usage.get("output_tokens", 0),
            "cacheReadInputTokens": usage.get("cache_read_input_tokens", 0),
            "cacheCreationInputTokens": usage.get("cache_creation_input_tokens", 0),
        },
        float(result.total_cost_usd),
    )


async def run_agent(
    *, api: RunApi, workspace_dir: str, max_turns: int, max_cost_usd: float
) -> tuple[str, str]:
    """Returns (outcome, message) where outcome is "succeeded", "failed", or "cancelled".

    Cancellation is cooperative here at best (a heartbeat check between assistant
    messages) — the orchestrator's direct container kill remains the authoritative
    mechanism regardless of whether this loop notices in time (PLAN-PHASE-2-M2.md's
    reconcile_active_runs/handle_cancel_run, unchanged by M4). "cancelled" is a distinct
    outcome, not folded into "failed": the caller must not report a terminal status to
    the internal API for it (caught live, during this milestone's own verification — a
    naive "treat cancellation as failure" self-report raced the orchestrator's own
    mark_cancelled and won, leaving a cancelled run showing as failed)."""
    ctx = api.context()
    options = ClaudeAgentOptions(
        system_prompt=ctx["system_prompt"],
        cwd=workspace_dir,
        model=ctx.get("model") or None,
        allowed_tools=ALLOWED_TOOLS,
        max_turns=max_turns,
        max_budget_usd=max_cost_usd,
        can_use_tool=make_can_use_tool(ctx["governance_rules"], api),
    )

    result: ResultMessage | None = None
    api.heartbeat()
    async for message in query(prompt=ctx["task_prompt"], options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    api.report_message(block.text)
            reply = api.heartbeat()
            if reply.get("cancel_requested"):
                return "cancelled", "cancelled"
        elif isinstance(message, ResultMessage):
            result = message

    if result is None:
        return "failed", "the SDK session ended without a result message"

    _report_usage(api, result, ctx.get("model"))

    if result.is_error:
        reason = result.result or result.stop_reason or "the agent session ended in error"
        return "failed", reason
    return "succeeded", (result.result or "done")
