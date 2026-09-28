# Execution Plan: Phase 2 M4.2 (Tool Execution Hardening & Agent Run Reliability)

Status: **Awaiting approval. No code written.**
Parent documents: [PLAN-PHASE-2-M4.md](PLAN-PHASE-2-M4.md), [ARCHITECTURE.md §4, §10](ARCHITECTURE.md)
Builds on: commit `4daf158` (M4.1 — Claude Agent SDK integration, governance enforcement data, git-credential wiring)
Date: 2026-09-28

## Scope, as given

Tool execution hardening and agent workflow reliability. Focus: tool call lifecycle, tool call visibility in dashboard, governance enforcement integration improvements, approval-state preparation (not approval execution), usage tracking improvements, failure handling, timeout handling, agent run observability.

Exclusions: multi-agent workflows, autonomous merge, deployment automation, production credentials, production repository access.

## 0. Reading the scope precisely

**0.1 Renumbering, again.** PLAN-PHASE-2-M4.md's own §10 milestone breakdown proposed M4.1 (SDK plumbing) → M4.2 (tool permissions/governance) → M4.3 (limits/failure) → M4.4 (real git) → M4.5 (testing). Your M4.1 approval message folded M4.2's tool-permissions/governance work into M4.1 itself. This M4.2 is therefore a different milestone than the original plan's M4.2 — it's a hardening/reliability pass on top of what M4.1 actually shipped, not the next item in that old sequence. I'm treating your message as the authoritative scope, same as M3's renumbering was.

**0.2 This is a "close the gaps M4.1 knowingly left" milestone, and I want to name that plainly.** M4.1's own report flagged several things as deliberately deferred: `tool_calls.result_excerpt/exit_code/duration_ms` never gets a writer; there's no operator-facing endpoint to read `tool_calls` or `usage_records` back; a cancelled or crashed run reports zero usage even if it spent real money; failure reasons are one flat string. Re-reading M4.1's own plan against what actually shipped surfaced one more: §4.4 of PLAN-PHASE-2-M4.md promised a redaction pass on `tool_calls.arguments` "extending" the git-credential redaction discipline — **this was never implemented.** `record_tool_call` stores `dict(arguments)` verbatim. That's a real gap, not a hypothetical one, and it's the first thing addressed below under §3.

**0.3 "Tool call lifecycle" is the one item here that's a genuine SDK integration, not just a query/endpoint.** Everything else in this plan is additive columns, read endpoints, and reporting logic inside code M4.1 already built. Lifecycle completion needs a `PostToolUse` hook — a mechanism M4.1 deliberately didn't wire up (§2 of that plan: "hooks become unnecessary... since I've decided to record tool_calls from `can_use_tool` directly"). That decision was right for *recording that a call happened*; it can't tell you *how it went*, because `can_use_tool` fires before execution. Adding the hook is the one piece of this milestone with real API-shape uncertainty (see §1.1).

## 1. Architecture impact

**No new services.** Everything here extends the runner (`runner/agent_sdk.py`), the internal API, the operator API, and the orchestrator's existing reconcile loop — the same four places M4.1 touched. No new container, no new network path, no new external dependency.

**1.1 Tool call lifecycle — the one real unknown.** `ClaudeAgentOptions.hooks["PostToolUse"]` takes a list of `HookMatcher`s wrapping an async callback receiving a `PostToolUseHookInput` (confirmed via SDK introspection: `session_id, tool_name, tool_input, tool_response, tool_use_id, ...`) and returning a `SyncHookJSONOutput`-shaped dict (every field optional — `{}` is a valid, complete response). `tool_response: Any` is the one field with no fixed shape — it varies per tool and isn't documented as a stable schema in the SDK's own type definitions. Design: extract what's recognizable (a `stdout`/`output`/`exit_code`-shaped mapping if `tool_response` is a dict; otherwise fall back to `str(tool_response)[:excerpt_limit]`), never fail the run if extraction doesn't match what's expected. This is exactly the kind of thing flagged rather than asserted with false confidence in PLAN-PHASE-2-M4.md §2 and §12 — confirming the real shape is an implementation-time task, done by instrumenting a real hook call against a real (or realistically-shaped fake) tool response before trusting the parsing logic, not by guessing further in a planning document.

**1.2 Correlation**: `can_use_tool` (pre-execution) and the new `PostToolUse` hook (post-execution) both fire for the same tool call, in the same runner process, and both carry `tool_use_id` (confirmed present on `ToolPermissionContext` and `PostToolUseHookInput`). The `tool_calls` row created by `can_use_tool` is looked up again by `(run_id, tool_use_id)` when the hook fires, and updated in place — no new row, no new event.

**1.3 Governance traceability**: `can_use_tool` already has the full matched rule (it's what decided allow/deny) — it currently discards the rule's `id` after building a denial message. Threading that `id` through costs nothing structurally; it's already in scope.

**1.4 Usage-on-partial-completion**: `query()`'s `AssistantMessage.usage` field (confirmed present, `dict[str, Any] | None`, populated per-turn) becomes a running "last known usage" snapshot the runner keeps in memory. Today, only `ResultMessage.model_usage` (surfaced once, at the very end) is ever reported — so a run that's cancelled or crashes before reaching a `ResultMessage` reports **zero** usage even if it burned real tokens. This is a correctness gap in cost tracking specifically, which is the one place a silent gap has a real dollar cost.

## 2. Files/services affected

**Backend — schema/migration:**
- `backend/alembic/versions/0017_tool_call_lifecycle_columns.py` (new) — see §3.
- `backend/src/acc/db/models/tool_call.py` — add `tool_use_id`, `rule_id`, `blocked_by_effect`.

**Backend — commands/queries:**
- `acc/commands/runs.py` — `record_tool_call` gains `tool_use_id`/`rule_id`/`blocked_by_effect` params; new `complete_tool_call(run_id, tool_use_id, *, duration_ms, exit_code, result_excerpt)`; `record_usage` unchanged in shape but now also called from more runner code paths; `fail_run` gains an optional `details: Mapping[str, Any] | None` merged into the existing `error` JSONB (no new column).
- `acc/queries/runs.py` — new `ToolCallView`/`list_tool_calls`, `UsageRecordView`/`list_usage`; `RunView`/`_view` gains `error: dict[str, Any] | None` (an existing column that was never surfaced — the same class of gap M3 found twice with `workspace_ref`/`branch_name`).

**Backend — internal API (runner-facing):**
- `acc/api/internal/schemas.py` — `ToolCallIn` gains optional `tool_use_id`/`rule_id`; new `ToolCallCompleteIn`; `FailIn` gains an optional structured `details` field.
- `acc/api/internal/runs.py` — new `POST /internal/runs/{id}/tool-calls/{tool_use_id}/complete`.

**Backend — operator API:**
- `acc/api/schemas.py` — new `ToolCallOut`/`ToolCallList`, `UsageRecordOut`/`UsageRecordList`; `RunOut` gains `error: dict[str, Any] | None`.
- `acc/api/routers/runs.py` — new `GET /runs/{id}/tool-calls`, `GET /runs/{id}/usage` (mirrors the existing `GET /runs/{id}/file-changes` exactly).

**Backend — orchestrator:**
- `acc/domain/events.py` — new `EventType.RUN_TIMEOUT_WARNING` + payload.
- `acc/commands/runs.py` — new `record_timeout_warning`.
- `acc/orchestrator/launcher.py` — `reconcile_active_runs` emits the warning once when a run crosses `run_timeout_warning_fraction` of `max_wall_clock`, before the existing hard-kill logic (unchanged) fires at 100%.
- `acc/config.py` — new `run_timeout_warning_fraction: float = 0.8`.

**Runner:**
- `runner/agent_sdk.py` — `PostToolUse` hook wiring; `tool_use_id` threading; running usage snapshot; a small, best-effort redaction pass applied to `arguments` before `report_tool_call` and to `result_excerpt` before reporting completion (§3); richer failure classification using `AssistantMessage.error`/`ResultMessage.subtype`/`api_error_status`.
- `runner/main.py` — no structural change; passes through whatever `run_agent` now returns (a slightly richer failure payload, still one dict merged into the existing `/fail` call).

## 3. Database impact

One migration, three additive columns on `tool_calls` (the only table this touches):

```sql
ALTER TABLE tool_calls ADD COLUMN tool_use_id text;
ALTER TABLE tool_calls ADD COLUMN rule_id uuid REFERENCES governance_rules(id) ON DELETE SET NULL;
ALTER TABLE tool_calls ADD COLUMN blocked_by_effect text
    CHECK (blocked_by_effect IN ('deny', 'require_approval'));
CREATE INDEX ix_tool_calls_run_id_tool_use_id ON tool_calls (run_id, tool_use_id);
```

- `tool_use_id`: correlates the pre-execution row with the post-execution hook report. Nullable — a denied call never executes, so it never gets a `tool_use_id`-bearing completion report, which is correct (there's nothing to complete).
- `rule_id`: which governance rule caused a denial, if any. Null for an allowed call or a call denied only by the fixed allowed-tool list (no rule involved).
- `blocked_by_effect`: **approval-state preparation, concretely.** `'require_approval'` vs `'deny'` are currently indistinguishable except by parsing `denial_reason` text. This column makes "this call would need a human, if approval existed" a queryable, filterable fact — without building anything that acts on it. No `approvals` table, no approve/reject endpoint, no run-resumption. A `require_approval` match still denies exactly as it does today (PLAN-PHASE-2-M4.md §4.2's fail-closed precedent, unchanged).

**Nothing else changes schema-wise.** `agent_runs.error` (existing JSONB) gets richer *content* (a `category`/`api_error_status`/`stop_reason` alongside the existing `reason`), not a new column — the column was already designed for exactly this ("structured, not just a string" is what JSONB is for). `usage_records`/`agent_runs`' token/cost columns are unchanged; the improvement is *when* `record_usage` gets called, not what it writes.

## 4. Security impact

**4.1 The redaction gap, closed.** `tool_calls.arguments` currently stores exactly what the model passed to a tool — including, for `Bash`, the full command string. A command that embeds a secret (`export FOO=sk-...`, a curl with an inline token) is stored verbatim today, readable by anyone with dashboard access. This milestone adds a best-effort redaction pass in the runner (pattern-based — the same class of "not exhaustive, real, better than nothing" caveat as governance's `command_regex` data) before `arguments`/`result_excerpt` ever leave the container. This is real hardening, not decoration: it's the literal thing "tool execution hardening" in your scope header names, and it's closing a gap this project already promised and missed once.

**4.2 `tool_response`'s contents.** A tool's output can itself contain sensitive material (a file's contents, command stdout). `result_excerpt` gets the same redaction pass as `arguments`, plus a hard length cap (matches the existing `result_excerpt: Text` column's implicit expectation and `AgentMessage`'s own 4000-char precedent) — never the full output, always a bounded excerpt.

**4.3 No change to the trust boundary.** The hook runs in the same runner process, with the same `can_use_tool` as the sole *enforcement* authority — the `PostToolUse` hook is purely observational (it cannot approve, deny, or retroactively change what already ran). No new network egress, no new secret, no new external call. `rule_id`'s foreign key is read-only from the runner's perspective (governance_rules is never written by a runner container).

**4.4 Fail-closed is unaffected.** `blocked_by_effect` and the richer denial event are reporting improvements on top of the existing decision, not a new code path that could itself become a bypass. A `require_approval` match still returns `PermissionResultDeny` exactly as before.

## 5. Test strategy

Same real-infrastructure standard as every prior milestone — nothing new mocked at the Postgres/Redis/Docker layer.

- **Unit**: the redaction function (a fixed set of "does it strip this, does it leave this alone" cases — command with an inline secret, a plain `ls -la`, a path with no secret-shaped substring); the `tool_response` best-effort extractor, fed a handful of plausible shapes (a dict with `exit_code`, a bare string, `None`) since the real shape is confirmed only at implementation time (§1.1); the failure-classification logic (`AssistantMessage.error` values → category strings).
- **Integration**: `POST /internal/runs/{id}/tool-calls/{tool_use_id}/complete` (updates the right row, 404s for an unknown `tool_use_id`, requires auth — same pattern as every other internal endpoint); `GET /runs/{id}/tool-calls` and `GET /runs/{id}/usage` (operator-facing, same pattern as the existing file-changes tests); the orchestrator's timeout-warning emission (real Docker container, `run_timeout_warning_fraction` set low enough to trigger deterministically, asserting the event fires once and the hard-kill logic is unaffected).
- **Real-Docker**: extend the existing "invalid API key" scenario (M4.1's own live-verification pattern, now a real automated test) to also assert a `tool_calls` row's `blocked_by_effect`/`rule_id` when a seeded rule's pattern is deliberately triggered inside a real container.

## 6. Live verification plan

Same posture as M4.1: an Anthropic API key is not required for most of this (lifecycle correlation, redaction, the new read endpoints, timeout-warning emission are all exercisable with the existing invalid-key failure path or the existing stub/heartbeat path). It **is** required to observe a real `PostToolUse` hook firing against a real tool execution — a fake key fails before any tool call happens, exactly as it did in M4.1.

1. Real container, invalid key (no key needed from you): confirm `GET /runs/{id}/tool-calls` and `GET /runs/{id}/usage` return correctly even for a run that never got past authentication (empty lists, not errors).
2. Real container, a deliberately-triggered governance rule via direct DB/SQL setup (mirrors M4.1's own approach — no operator API can author a `match` yet): confirm `blocked_by_effect`/`rule_id` land correctly on the resulting `tool_calls` row.
3. Real container, wall-clock set low enough to cross the warning threshold before the hard kill: confirm the warning event appears before the existing "wall-clock limit exceeded" failure, and that the container still gets killed at the real limit, unchanged.
4. **Requires your real key**: one small, cheap real task, watched end to end, confirming a real `PostToolUse` hook fires, a `tool_calls` row gets its `duration_ms`/`exit_code`/`result_excerpt` filled in after the fact, and that `result_excerpt` is redacted/bounded correctly against real tool output.

## 7. Open decisions needing your input before I code

- **Governance match beyond Bash** (§0.2/PLAN-PHASE-2-M4.md §4.2's own noted limit): today's `match` shape (`{"tool": "Bash", "command_regex": ...}`) can't cover a secret read via the `Read` tool at all — only via a shell command. Extending `rule_matches` to also support `path_regex` (checked against `Read`/`Write`/`Edit`'s `file_path` argument) is a real, bounded capability addition, not a redesign — but it's also a data-shape decision I'd rather have your sign-off on than decide unilaterally. Recommend: yes, add it as a capability; whether to retrofit it onto the existing 3 `deny` rules' data is a separate, smaller follow-up decision.
- **Timeout-warning threshold**: recommend 80% of `run_max_wall_clock_minutes`, configurable (`ACC_RUN_TIMEOUT_WARNING_FRACTION`), consistent with every other run limit being configurable per your M4.1 instruction.
- **Redaction approach**: pattern-based (regex for common secret shapes — bearer tokens, `sk-`-prefixed keys, `.env`-style `KEY=value` lines) is necessarily incomplete. Recommend proceeding with it as real, meaningful, non-exhaustive hardening (same honesty standard as the governance `command_regex` data) rather than waiting for a complete solution that doesn't exist for arbitrary text.

Waiting for your approval on this plan, and the decisions above, before writing any code.
