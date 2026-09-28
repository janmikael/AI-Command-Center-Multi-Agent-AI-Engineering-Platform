"""The Claude Agent SDK adapter (PLAN-PHASE-2-M4.md §2, hardened by PLAN-PHASE-2-M4.2.md).
Runs exactly one query() session to completion inside the sandbox — no resume, no
multi-session continuation. Reports progress/tool-calls/usage back through the internal
API exactly like every other runner path: no direct database access, ever
(PLAN-PHASE-2-M2.md §1.6).

Policy is enforced here, not decided here: governance_rules are fetched once, from the
context endpoint, at run start (a snapshot — a rule edited mid-run changes the next run's
enforcement, not this one's). can_use_tool is the sole *enforcement* gate; the PostToolUse
hook added in M4.2 is purely observational (PLAN-PHASE-2-M4.2.md §4.3) — it cannot allow,
deny, or retroactively change what already ran, it only completes the lifecycle record of
a call can_use_tool already allowed.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    query,
)

REQUEST_TIMEOUT_S = 10
MAX_REPORTED_TEXT = 4000
MAX_RESULT_EXCERPT = 2000

# Fixed, not configurable per task or per agent (PLAN-PHASE-2-M4.md §4.1) — no MCP
# gateway or per-task tool policy exists yet to make widening this safe. No Web* tools:
# each would need its own egress-allowlist entry beyond the git remote and the Anthropic
# API, which this milestone doesn't add.
ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]

# ─── Redaction (PLAN-PHASE-2-M4.2.md §4/decision 3) ───────────────────────────────────
# Layered, deliberately not exhaustive — the same honesty standard as governance's own
# command_regex data: real, meaningful coverage of common shapes, not a claim of perfect
# secret detection (arbitrary text has no such thing). Applied to tool_calls.arguments
# and result_excerpt before either ever leaves this container.
REDACTED = "[REDACTED]"

# Layer 1: known secret-shaped environment variable NAMES, shell assignment style
# (KEY=value or export KEY=value) — catches `export AWS_SECRET_ACCESS_KEY=...`,
# `DATABASE_URL=postgres://...`, etc.
_SECRET_ENV_ASSIGNMENT = re.compile(
    r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*"
    r"(?:_KEY|_SECRET|_TOKEN|_PASSWORD|_CREDENTIAL|_CREDENTIALS|_PASSWD)"
    r"(?:_[A-Z0-9]+)*)"
    r"(\s*=\s*)(\S+)"
)

# Layer 2: common real-world token formats, matched by shape regardless of surrounding
# context — catches a secret pasted into an argument with no "password=" style prefix.
_TOKEN_FORMATS = re.compile(
    r"\b("
    r"sk-[A-Za-z0-9_-]{20,}"  # Anthropic/OpenAI-style API keys
    r"|ghp_[A-Za-z0-9]{36}"  # GitHub personal access tokens
    r"|gh[oprsu]_[A-Za-z0-9]{36}"  # other GitHub token kinds
    r"|AKIA[0-9A-Z]{16}"  # AWS access key ids
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"  # Slack tokens
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"  # JWTs
    r"|Bearer\s+[A-Za-z0-9._-]{20,}"
    r")"
)

# Layer 3: password/key-shaped fields in non-shell-variable contexts — CLI flags,
# config-file lines, JSON-ish fields (`--password foo`, `api_key: "abc"`, `"token"="x"`).
_PASSWORD_KEY_FIELD = re.compile(
    r"\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|auth[_-]?token)\b(\s*[:=]\s*)\"?(\S+?)\"?(?=[\s,}\]]|$)",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    text = _SECRET_ENV_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _PASSWORD_KEY_FIELD.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _TOKEN_FORMATS.sub(REDACTED, text)
    return text


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def bounded_excerpt(text: str, *, limit: int = MAX_RESULT_EXCERPT) -> str:
    """Layer 4: a hard length cap, always applied, regardless of redaction — an
    unbounded tool result (a large file read, verbose command output) is never stored
    in full (PLAN-PHASE-2-M4.2.md §4.2)."""
    redacted = redact(text)
    if len(redacted) > limit:
        return redacted[:limit] + "... [truncated]"
    return redacted


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
        self.try_post(
            f"/internal/runs/{self._run_id}/tool-calls",
            {
                "tool": tool,
                "arguments": redact_value(arguments),
                "allowed": allowed,
                "denial_reason": denial_reason,
                "tool_use_id": tool_use_id,
                "rule_id": rule_id,
                "blocked_by_effect": blocked_by_effect,
            },
        )

    def report_tool_call_complete(
        self,
        tool_use_id: str,
        *,
        duration_ms: int | None,
        exit_code: int | None,
        result_excerpt: str | None,
    ) -> None:
        self.try_post(
            f"/internal/runs/{self._run_id}/tool-calls/{tool_use_id}/complete",
            {
                "duration_ms": duration_ms,
                "exit_code": exit_code,
                "result_excerpt": bounded_excerpt(result_excerpt) if result_excerpt else None,
            },
        )

    def report_usage(
        self,
        model: str,
        usage: dict[str, Any],
        cost_usd: float,
        *,
        cost_source: str = "provider_reported",
    ) -> None:
        self.try_post(
            f"/internal/runs/{self._run_id}/usage",
            {
                "model": model,
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
                "cache_read_tokens": usage.get("cacheReadInputTokens", 0),
                "cache_write_tokens": usage.get("cacheCreationInputTokens", 0),
                "cost_usd": cost_usd,
                "cost_source": cost_source,
            },
        )


# ─── Governance matching (PLAN-PHASE-2-M4.2.md §0/decision 1) ─────────────────────────
# Deliberately simple: each present key in `match` is an independent, AND-ed condition —
# no boolean operators, no nested rules. A rule that needs "this OR that" is two rows.
_PATH_ARG_KEYS = ("file_path",)  # Read/Write/Edit's own argument name for their target


def rule_matches(match: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Mechanical matching only. A rule with an empty `match` (most seeded rules still
    have one — nothing has authored a path_regex yet) can never match here; its
    statement still reaches the model as plain-English prompt text
    (acc.queries.context.render_system_prompt), but is not something this gate can
    enforce as a hard stop until an operator gives it a real pattern.

    Three supported keys, matching the scope you approved: tool (exact match),
    command_regex (against Bash's own `command` argument), path_regex (against
    Read/Write/Edit's `file_path` argument). All present keys must match."""
    if not match:
        return False
    if "tool" in match and match["tool"] != tool_name:
        return False
    if "command_regex" in match:
        command = str(tool_input.get("command", ""))
        if not re.search(match["command_regex"], command):
            return False
    if "path_regex" in match:
        path = None
        for key in _PATH_ARG_KEYS:
            if key in tool_input:
                path = str(tool_input[key])
                break
        # No path-bearing argument at all (e.g. a Bash call) -> nothing to match, not
        # an empty string that a permissive pattern like ".*" would wrongly match.
        if path is None or not re.search(match["path_regex"], path):
            return False
    return True


class _ToolCallTracker:
    """In-memory only, one run per process: the start time and duration_ms fields aren't
    persisted anywhere between can_use_tool (pre-execution) and the PostToolUse hook
    (post-execution) — they're the same tool_use_id in the same runner process, so a
    plain dict is enough (PLAN-PHASE-2-M4.2.md §1.2)."""

    def __init__(self) -> None:
        self._started_at: dict[str, float] = {}

    def start(self, tool_use_id: str) -> None:
        self._started_at[tool_use_id] = time.monotonic()

    def duration_ms(self, tool_use_id: str) -> int | None:
        started = self._started_at.pop(tool_use_id, None)
        if started is None:
            return None
        return int((time.monotonic() - started) * 1000)


def make_can_use_tool(rules: list[dict[str, Any]], api: RunApi, tracker: _ToolCallTracker) -> Any:
    ordered = sorted(rules, key=lambda r: r["priority"])

    async def can_use_tool(
        tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        tool_use_id = context.tool_use_id
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
                # PLAN-PHASE-2-M4.md §4.2). blocked_by_effect/rule_id (M4.2 §3): a
                # queryable record of *which* rule and *which kind* of block, not a
                # change to this outcome.
                reason = f"Denied by policy ({rule['effect']}): {rule['statement']}"
                api.report_tool_call(
                    tool_name, tool_input, allowed=False, denial_reason=reason,
                    rule_id=str(rule["id"]), blocked_by_effect=rule["effect"],
                )
                return PermissionResultDeny(message=reason)

            if tool_use_id:
                tracker.start(tool_use_id)
            api.report_tool_call(
                tool_name, tool_input, allowed=True, denial_reason=None, tool_use_id=tool_use_id
            )
            return PermissionResultAllow()
        except Exception as exc:  # fail closed when permission cannot be determined
            reason = f"policy evaluation failed, denying by default: {exc}"
            api.report_tool_call(tool_name, tool_input, allowed=False, denial_reason=reason)
            return PermissionResultDeny(message=reason)

    return can_use_tool


def _extract_tool_response(tool_response: Any) -> tuple[int | None, str | None]:
    """Best-effort: PostToolUseHookInput types tool_response as Any — the SDK gives it
    no fixed schema since it varies per tool, so this degrades gracefully rather than
    asserting a shape (PLAN-PHASE-2-M4.2.md §1.1), confirmed only at live-verification
    time against a real tool response."""
    if isinstance(tool_response, dict):
        # `or` would wrongly treat exit code 0 (success) as "not found" and fall
        # through to exitCode/None — an easy, real bug for the single most common exit
        # code there is.
        exit_code = tool_response.get("exit_code")
        if exit_code is None:
            exit_code = tool_response.get("exitCode")
        for key in ("output", "stdout", "content", "result"):
            if key in tool_response and isinstance(tool_response[key], str):
                return exit_code, tool_response[key]
        return exit_code, json.dumps(tool_response)[:MAX_RESULT_EXCERPT]
    if isinstance(tool_response, str):
        return None, tool_response
    if tool_response is None:
        return None, None
    return None, str(tool_response)


def make_post_tool_use_hook(api: RunApi, tracker: _ToolCallTracker) -> Any:
    async def post_tool_use(input_data: dict[str, Any], _tool_use_id: Any, _context: Any) -> dict[str, Any]:
        tool_use_id = input_data.get("tool_use_id")
        if not tool_use_id:
            return {}
        duration_ms = tracker.duration_ms(tool_use_id)
        exit_code, result_text = _extract_tool_response(input_data.get("tool_response"))
        api.report_tool_call_complete(
            tool_use_id, duration_ms=duration_ms, exit_code=exit_code, result_excerpt=result_text
        )
        return {}

    return post_tool_use


def _extract_message_usage(message: AssistantMessage) -> dict[str, int] | None:
    """Best-effort partial-usage signal for a session that never reaches a
    ResultMessage (PLAN-PHASE-2-M4.2.md §5) — AssistantMessage.usage's exact shape is
    unconfirmed (unlike ResultMessage.model_usage, verified by SDK introspection),
    assumed to follow the Anthropic Messages API's own per-message usage convention."""
    usage = message.usage
    if not usage:
        return None
    return {
        "inputTokens": usage.get("input_tokens", 0),
        "outputTokens": usage.get("output_tokens", 0),
        "cacheReadInputTokens": usage.get("cache_read_input_tokens", 0),
        "cacheCreationInputTokens": usage.get("cache_creation_input_tokens", 0),
    }


def _report_final_usage(api: RunApi, result: ResultMessage, fallback_model: str | None) -> None:
    if result.model_usage:
        for model, usage in result.model_usage.items():
            api.report_usage(model, usage, float(usage.get("costUSD", 0.0)))
        return
    if result.total_cost_usd is None:
        return
    # Best-effort fallback for a CLI/SDK version that doesn't populate model_usage: field
    # names here follow the Anthropic Messages API's own usage object convention, but
    # this path is unconfirmed against a real call — model_usage above is the confirmed
    # shape (verified by introspecting the installed SDK package).
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


_ERROR_CATEGORY_FIELD = "category"


async def run_agent(
    *, api: RunApi, workspace_dir: str, max_turns: int, max_cost_usd: float
) -> tuple[str, str, dict[str, Any] | None]:
    """Returns (outcome, message, details) where outcome is "succeeded", "failed", or
    "cancelled", and details is an optional structured extra (failure category,
    stop_reason, api_error_status) merged into agent_runs.error alongside message
    (PLAN-PHASE-2-M4.2.md §6) — never present for "succeeded".

    Cancellation is cooperative here at best (a heartbeat check between assistant
    messages) — the orchestrator's direct container kill remains the authoritative
    mechanism regardless of whether this loop notices in time (PLAN-PHASE-2-M2.md's
    reconcile_active_runs/handle_cancel_run, unchanged by M4). "cancelled" is a distinct
    outcome, not folded into "failed": the caller must not report a terminal status to
    the internal API for it (PLAN-PHASE-2-M4.md's own live verification — a naive
    "treat cancellation as failure" self-report raced the orchestrator's own
    mark_cancelled and won, leaving a cancelled run showing as failed).

    Whatever partial usage was observed before an early exit (cancellation, or a session
    that never reaches a ResultMessage) is still reported — a run that spent real tokens
    before being cut off must not show $0.00 (PLAN-PHASE-2-M4.2.md §5); cost_source is
    marked "estimated" for this path since no provider-confirmed cost figure exists yet.
    """
    ctx = api.context()
    tracker = _ToolCallTracker()
    options = ClaudeAgentOptions(
        system_prompt=ctx["system_prompt"],
        cwd=workspace_dir,
        model=ctx.get("model") or None,
        allowed_tools=ALLOWED_TOOLS,
        max_turns=max_turns,
        max_budget_usd=max_cost_usd,
        can_use_tool=make_can_use_tool(ctx["governance_rules"], api, tracker),
        hooks={"PostToolUse": [HookMatcher(hooks=[make_post_tool_use_hook(api, tracker)])]},
    )

    result: ResultMessage | None = None
    last_usage: dict[str, int] | None = None
    last_usage_model: str | None = None
    last_error_category: str | None = None

    def report_partial_usage() -> None:
        if last_usage is not None:
            api.report_usage(
                last_usage_model or ctx.get("model") or "unknown", last_usage, 0.0,
                cost_source="estimated",
            )

    try:
        api.heartbeat()
        async for message in query(prompt=ctx["task_prompt"], options=options):
            if isinstance(message, AssistantMessage):
                usage = _extract_message_usage(message)
                if usage is not None:
                    last_usage, last_usage_model = usage, message.model
                if message.error is not None:
                    last_error_category = message.error
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        api.report_message(block.text)
                reply = api.heartbeat()
                if reply.get("cancel_requested"):
                    report_partial_usage()
                    return "cancelled", "cancelled", None
            elif isinstance(message, ResultMessage):
                result = message
    except Exception as exc:
        report_partial_usage()
        details = {_ERROR_CATEGORY_FIELD: last_error_category} if last_error_category else None
        return "failed", f"agent sdk run failed: {exc}", details

    if result is None:
        report_partial_usage()
        details = {_ERROR_CATEGORY_FIELD: last_error_category} if last_error_category else None
        return "failed", "the SDK session ended without a result message", details

    _report_final_usage(api, result, ctx.get("model"))

    if result.is_error:
        reason = result.result or result.stop_reason or "the agent session ended in error"
        details: dict[str, Any] = {
            "subtype": result.subtype,
            "stop_reason": result.stop_reason,
            "api_error_status": result.api_error_status,
        }
        if last_error_category:
            details[_ERROR_CATEGORY_FIELD] = last_error_category
        details = {k: v for k, v in details.items() if v is not None}
        return "failed", reason, (details or None)
    return "succeeded", (result.result or "done"), None
