# Execution Plan: Phase 2 M4 (Claude Agent SDK Integration — First Real AI Worker)

Status: **Awaiting approval. No code written.**
Parent documents: [PLAN-PHASE-2.md](PLAN-PHASE-2.md), [PLAN-PHASE-2-M2.md](PLAN-PHASE-2-M2.md), [PLAN-PHASE-2-M3.md](PLAN-PHASE-2-M3.md), [ARCHITECTURE.md §4, §10](ARCHITECTURE.md)
Builds on: commit `6fe7c37` (M3 — repository/workspace access, branch isolation, `file_changes`)
Date: 2026-09-28

## Scope, as given

Claude Agent SDK integration into the existing runner architecture. Goals: the SDK runs inside the sandbox runner; the agent receives task context; it can work inside the prepared repository workspace; progress/events flow back to the dashboard; usage and cost are tracked; the agent can be cancelled safely; its output becomes task results.

Must design: (1) the SDK adapter, (2) runner execution flow, (3) tool permissions, (4) the governance enforcement point, (5) event mapping, (6) cost/token limits, (7) failure handling, (8) testing strategy.

Do not implement: multi-agent workflows, designer agent, production deployment, autonomous merge, unrestricted tool access.

## 0. Reading the scope precisely

**0.1 This is the milestone every prior one was preparing for, and it's worth naming exactly what that means.** M1 built `agent_runs`/`usage_records`/`tool_calls` with no writer for the latter two by design ("added alongside the real adapter"). M2 built the sandbox with nothing dangerous inside it. M3 gave the runner git but no judgment. Phase 1.5 built `governance_rules` with nothing ever consulting them. M4 is where all four of those deliberate gaps close at once — this is the first milestone where a real model makes real decisions, spends real money, and the rules that were seeded eight months of milestones ago finally get evaluated for the first time.

**0.2 The SDK runs *inside the runner container*, not in the orchestrator.** ARCHITECTURE.md §4.5 sketched an `AgentAdapter` Protocol that reads as if the orchestrator drives the agent loop directly. That was written before M2/M3 established the actual topology: the workspace, the git identity, and now the SDK's own tool execution (`Read`/`Write`/`Edit`/`Bash`) all have to happen *inside* the sandbox, against the sandboxed filesystem — there's no other place `cwd` can point. So "the adapter" is a new module in `runner/` (alongside `git_ops.py`), not `acc.orchestrator` — consistent with M2's original decision to keep the runner outside the layered `acc` import graph entirely, and with §4.4's own description ("Set cwd to the worktree... Stream SDK messages").

**0.3 This is the first milestone that changes the runner's risk profile, not just its capability, and that's worth saying plainly rather than folding into a bullet point.** M2 §2.7 called the M2 runner "the smallest possible secret-bearing footprint" specifically because it had nothing installed beyond the Python stdlib. The Claude Agent SDK's Python package drives Claude Code's CLI under the hood, which needs a Node.js runtime — the first non-trivial software installed in this image. §1 below treats this as a real, accepted trade-off with explicit mitigations, not a footnote.

**0.4 "Agent output becomes task artifacts/results" maps to what M1/M3 already built, not a new generic `artifacts` table.** ARCHITECTURE.md's full schema has an `artifacts` table for a world with multiple agent roles producing different kinds of output (plans, design specs, diffs, reviews). That world is explicitly out of scope here (no multi-agent, no designer). For a single developer agent, its "artifact" *is* the pushed branch, its diff (`file_changes`, M3), and its `result_summary` (`agent_runs`, M1) — all three already exist and already have writers or near-writers. Building a generic artifacts table now would be scaffolding for a phase that isn't approved yet.

**0.5 The cost ceiling that PLAN-PHASE-2-M2.md §4.5 said would "ship in M3, not M5" never actually shipped, because the M3 that got approved was repository/workspace only — no SDK, no spend.** That commitment carries forward to *this* milestone instead, since M4 is now the actual first-real-money milestone. Same for a basic wall-clock timeout: M2's plan deferred that to M5's "reliability hardening," reasonably, because nothing expensive was running yet. Now something is. Both are treated as required parts of this plan, not nice-to-haves — see §5.

## 1. Architecture impact

**Runner image**: adds a Node.js runtime and the `claude-agent-sdk` Python package (which invokes the Claude Code CLI as a subprocess). This is the one deliberate expansion of the sandbox's software surface this project has made since M2. Everything else about the sandbox is unchanged and still applies in full: `read_only` rootfs, `cap_drop: [ALL]`, non-root user, no Docker socket, `runner-egress`-only networking, resource limits. Mitigations specific to this addition:
- Pin exact Node and `claude-agent-sdk` versions in the Dockerfile (no floating `latest`).
- No global npm packages beyond what the SDK's own install requires; no package manager left usable at runtime beyond what's already frozen into the image (matches the existing "never attacker-selectable, launcher hardcodes the tag" principle — nothing about *installing more software at runtime* changes here).
- The added surface is supply-chain and CVE risk in the image itself, not a new capability granted to a hostile *agent* — the sandbox boundary (§2) is what bounds what a misbehaving or successfully-injected agent can do, and that boundary is unchanged.

**New runner module**: `runner/agent_sdk.py` — the adapter. Wraps the SDK's client, normalizes its message stream into calls against the internal API (§6), and is the only new file that imports the SDK at all. `runner/main.py` gains a dispatch: if the agent's adapter is `claude_agent_sdk`, run this loop; otherwise (adapter `none`, or a repository-only task with no adapter work) fall through to M2/M3's existing behavior unchanged. This mirrors exactly how M3 added the git step conditionally without touching the no-repository path.

**New internal endpoint**: `GET /internal/runs/{run_id}/context` — the four-layer context render from ARCHITECTURE.md §10.2, built for the first time (§3). Deferred in both M2's and M3's plans until there was an adapter to consume it; this is that moment.

**New internal endpoints, one per event family** (not one generic batch endpoint — see §6 for why): `POST /internal/runs/{id}/messages`, `POST /internal/runs/{id}/tool-calls`, `POST /internal/runs/{id}/usage`. Same auth (`RunClaimsDep`, unchanged since M2), same "never routed through Caddy" boundary.

**Orchestrator change**: `acc.orchestrator.launcher.reconcile_active_runs` (M2) already scans every active run on a timer; it gains a second check alongside "has the container exited" — "has this run been going for longer than the wall-clock cap" (§5). Reuses the existing scan, not a new loop.

**New settings** (`acc.config.Settings`, following the `runner_token_secret`/`git_credential` pattern exactly): `anthropic_api_key: SecretStr`, `run_max_cost_usd: Decimal` (default proposed: `2.00`), `run_max_turns: int` (default proposed: `20`), `run_max_wall_clock_minutes: int` (default proposed: `15`).

## 2. Claude Agent SDK adapter

`runner/agent_sdk.py`'s job is narrow: given a rendered context and a workspace, run one SDK session to completion (or failure, or cancellation) and report what happened. It does not decide policy — the governance check (§4) and the cost/turn ceilings (§5) are separate, composable pieces it calls into, not logic baked into the adapter itself, so each is independently testable.

```
async def run_agent(context: RenderedContext, workspace_dir: Path, settings: RunnerSettings) -> AgentOutcome:
    options = ClaudeAgentOptions(
        system_prompt=context.system_prompt,       # layers 1+2, §3 — stable, cacheable
        cwd=str(workspace_dir),
        model=context.model,                        # from agents.model (§9)
        allowed_tools=ALLOWED_TOOLS,                 # §4.1, fixed list
        max_turns=settings.max_turns,
        can_use_tool=make_governance_gate(context.governance_rules, context.capabilities),
        hooks={"PostToolUse": [record_tool_call_hook]},
    )
    async for message in query(prompt=context.task_prompt, options=options):  # layers 3+4
        await handle_message(message)                # §6 — normalizes + reports
        if exceeds_cost_ceiling(...): break            # §5
    return AgentOutcome(...)
```

(Exact SDK field/class names — `ClaudeAgentOptions`, the hook registration shape, `PermissionResult` — get pinned down against the installed SDK version during implementation, not guessed at in a planning doc; the architecture here doesn't depend on getting a class name exactly right.)

`supports_resume`/session continuation (ARCHITECTURE §4.5) is **not built this milestone** — every run is one session, start to finish. `agent_runs.provider_session_id` (M1, unused) gets populated from the SDK's result message purely for future use and debugging; nothing reads it back yet. Resuming a suspended session is a real feature with real design questions (what triggers a resume, how long a session can be parked) that belongs with whatever milestone actually needs it (a future approval-hold or budget-pause flow) — building the plumbing now with no caller would repeat the mistake M1 explicitly avoided with `tool_calls`/`usage_records`.

## 3. Runner execution flow

```
runner/main.py, when AGENT_ADAPTER=claude_agent_sdk:

1. heartbeat()                                   # confirms alive, flips STARTING -> RUNNING
2. context = GET /internal/runs/{id}/context      # NEW — the four-layer render (§below)
3. if context.repository:
       git_ops.clone(...)                          # reused verbatim from M3
       git_ops.create_branch(context.branch_name)  # reused verbatim from M3
   # No fixed marker-file step this time — the agent decides what changes, if any.
4. outcome = agent_sdk.run_agent(context, workspace_dir, settings)   # §2
5. if workspace has uncommitted changes:
       git_ops.commit(outcome.commit_message)       # reused, real message not a constant
       git_ops.push(context.branch_name)             # reused, same acc/* restriction
       changes = git_ops.diff_stat(...)              # reused verbatim from M3
       POST /internal/runs/{id}/file-changes          # reused verbatim from M3
   # Attempted regardless of outcome.success — see §7, partial progress is still useful.
6. if outcome.success: POST /internal/runs/{id}/complete {result_summary: outcome.summary}
   else:               POST /internal/runs/{id}/fail {reason: outcome.reason}
```

**The four-layer context** (`GET /internal/runs/{id}/context`, ARCHITECTURE §10.2, built now for the first time):

| Layer | Source | Rendered into |
|---|---|---|
| 1. Global AI Rules | `governance_rules` (Phase 1.5, `enabled=true`, filtered by `applies_to` against the agent's capabilities) | `system_prompt` (with layer 2) |
| 2. Agent Role | `agents.role`/a static per-adapter description of what a developer agent does | `system_prompt` |
| 3. Project Context | `project_context_entries` (Phase 1.5; table and CRUD API already exist, no editor UI needed to have entries in it) | `query()`'s prompt (with layer 4) |
| 4. Task Instructions | `tasks.title`/`description`/`acceptance_criteria` | `query()`'s prompt |

Layers 1–2 go into `system_prompt` and layers 3–4 into the actual query prompt specifically because that's the split ARCHITECTURE §10.2 already reasoned about for prompt caching: 1–2 are identical across every run of this agent, 3 changes per project, only 4 changes per run — a stable prefix is exactly what Anthropic's prompt caching wants, and this costs nothing extra to get right by simply respecting the layer order that was already designed.

## 4. Security model

### 4.1 Tool permissions

No MCP gateway exists (excluded again this milestone, same as M2 and M3), and "unrestricted tool access" is explicitly excluded — so the allowed-tool list is small and fixed, not configurable per task or per agent:

**Allowed**: `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`.
**Not allowed**: `WebFetch`, `WebSearch` (no task need yet, and each would need its own egress-allowlist entry beyond the git remote and `api.anthropic.com` — a real expansion of network policy this milestone doesn't need to make), any MCP-server tools (none are configured, so moot), any multi-agent coordination tools (none exist).

File-path escape is bounded by the sandbox itself, unchanged since M2: the container's writable surface is only the `/workspace` tmpfs, so `Write`/`Edit`/`Bash` are physically confined regardless of what the model attempts — this is true before any permission logic runs at all.

### 4.2 The governance enforcement point

This is the first time `governance_rules` (seeded in Phase 1.5, never consulted since) is evaluated by anything. The enforcement point is the SDK's `can_use_tool` callback, running **inside the runner process**, checking a snapshot of rules fetched once via the context endpoint at run start (not re-fetched mid-run — simpler, and avoids a rule edit mid-run producing inconsistent enforcement within one session):

```
for every tool call the model attempts:
    matching_rules = [r for r in governance_rules if applies(r, tool_name, arguments)]
    if any(r.effect == "deny" for r in matching_rules): reject, with the rule's statement as the reason
    elif any(r.effect == "require_approval" for r in matching_rules): reject — see below
    else: allow (default, or an explicit "allow"/"advisory" rule)
```

`match` (jsonb, e.g. `{"tool": "Bash", "command_regex": "^(rm|curl|sudo)"}`) is evaluated against the attempted tool call — this is the first real consumer of that column, which Phase 1.5 deliberately shaped "so a future MCP gateway can consume it directly." M4 becomes that consumer sooner than a gateway exists, using the exact shape already designed, not a redesign.

**`require_approval` rules fail closed, same as M2/M3's established precedent — but the stakes are real now, not theoretical, so this is re-presented as an explicit decision rather than silently inherited.** No approval mechanism exists (no MCP gateway, no approvals table consumer). Concretely: the seeded rules mean the agent categorically cannot use paid third-party tools, cannot touch CI config or infra paths, cannot run destructive commands, and cannot attempt a DB migration — all of Phase 1.5's `require_approval`-effect rules become hard blocks this milestone. This is the same trade-off M2 made, just no longer hypothetical. Recommended default: keep it. A denied call gets fed back to the model as a normal tool error ("Denied by policy: <rule statement>"), so it can adapt rather than the run failing outright — the run only fails if the model can't make progress without the denied action.

**Weaker than the target design, stated honestly, same framing as M2 §4.3 and M3 §2.4**: this check lives in the same process as tool execution. A vulnerability in the SDK's own execution path could in principle let a compromised runner process bypass Python logic it's supposed to obey — prompt injection alone cannot do this (the model chooses *which* tool calls to attempt; it cannot rewrite the callback that decides whether they're allowed). Defense in depth is unchanged from every prior milestone: no DB credential in the runner, no merge/deploy capability, branch-name restriction enforced independently in `git_ops.py`, network confined to the git remote and `api.anthropic.com`.

### 4.3 Tool call recording

Every tool call gets a `tool_calls` row (M1 schema, first real writer) via a `PostToolUse` hook — `tool`, `arguments` (redacted, §4.4), `allowed`, `denial_reason`, timing. Not every call becomes a realtime event (§6) — the table is the source of truth for the run-detail view; only denials are individually eventful.

### 4.4 Secrets

The Anthropic API key is a new kind of secret this system hasn't held before: unlike the git credential (scoped to one throwaway repo), it's a **billing-capable** credential — a leak has a real financial dimension, not just a repo-access one. Same injection discipline as every prior secret (env var, never logged, never in `tool_calls.arguments` — the redaction pass already required for git credentials extends to this one). Recommended operational mitigation, external to this codebase the same way host-side branch protection was for git: provision a key with a spend limit or budget alert configured in Anthropic's own console, so a runaway or compromised run has a hard ceiling beyond even this system's own cost check (§5).

## 5. Cost/token limits

Two independent backstops, neither trusting the runner alone to enforce its own limit — same principle M2 §4.5 already established for the (until-now theoretical) cost ceiling:

1. **In-runner, cooperative**: after every SDK turn's usage report, check cumulative `cost_usd` against `run_max_cost_usd`. Exceeding it stops the loop, attempts to push whatever progress exists (§7), and fails the run with "cost ceiling reached" — no silent continuation. `max_turns` (SDK-native option) is a second, independent cap on the same side.
2. **Orchestrator-side, authoritative**: `reconcile_active_runs`'s existing per-tick scan (M2) gains a wall-clock check — any run whose `started_at` exceeds `run_max_wall_clock_minutes` gets its container stopped directly via `docker-socket-proxy`, exactly like an operator-initiated cancel, regardless of what the runner process is doing. This doesn't depend on the runner cooperating at all, closing the gap a hung or compromised runner would otherwise leave open.

Every `usage.recorded` event (§6) also increments `agent_runs.input_tokens`/`output_tokens`/`cache_read_tokens`/`cost_usd` directly, so the running total is visible without summing `usage_records` on every read.

## 6. Event mapping

| SDK signal | Event type | Volume-driven decision |
|---|---|---|
| Assistant text/message | `agent.message` (M1, unused until now) | One per turn — this *is* "progress flows to the dashboard" |
| Tool call attempted + result | `tool_calls` row (§4.3) | No event per call by default — same reasoning M1 already applied to `record_step`/heartbeats: high volume, low individual signal, and the run-detail view reads the table directly |
| Tool call denied | `tool_call.denied` (**new**) | Always an event — rare, security-relevant, an operator should see it live without opening the run detail |
| Turn usage/cost report | `usage.recorded` (**new**) | One per turn — moderate volume, and cost visibility is exactly what this event is for |
| Structured decision | `agent.decision` (M1, unused) | **Still not used this milestone** — its intended source is an explicit `acc.log_decision()`-style platform tool (ARCHITECTURE §4.3), which needs `acc-control`/MCP-gateway-shaped tooling that doesn't exist yet. Forcing a mapping onto plain SDK messages would misuse a payload shape designed for something more structured. |

Three new internal endpoints, not one generic batch endpoint (§1): `tool_calls`, `usage_records`, and `agent.message` events are different operations with different validation and different tables — matching the project's existing one-endpoint-per-concern convention (`heartbeat`/`complete`/`fail`/`file-changes` are each already separate) rather than introducing a generic envelope type this codebase hasn't used anywhere else.

## 7. Failure handling

- **SDK/network error** (Anthropic API unreachable, malformed response, CLI subprocess crash): caught, `/fail` with the real exception message.
- **`max_turns` reached without the model finishing**: `/fail` with "max turns reached" — but git commit/push/diff-report (step 5 in §3) is attempted *regardless* of success or failure, so a partial diff is still visible to a human reviewer rather than lost. This is a deliberate, small design choice: a failed run with a reviewable partial branch is more useful than a failed run with nothing to show.
- **Cost ceiling reached**: same partial-progress-then-fail pattern as above.
- **Container crashes outright** (OOM, Node/CLI segfault): already handled, unchanged — M2's `reconcile_active_runs` catches any container that exits without reaching a terminal run status, exactly the mechanism verified live in M2 with a real `docker kill`.
- **Cancellation**: unchanged and already safe by construction — the orchestrator can stop the container directly at any time regardless of what the SDK loop is doing (M2's authoritative mechanism). Added this milestone as a cooperative nicety, not a requirement: the runner checks `cancel_requested` between turns (already returned by the existing `/heartbeat` response) and, if set, stops requesting further turns and goes straight to step 5 (commit/push whatever exists) before exiting cleanly — so a cooperative cancel preserves partial work the same way a hard kill wouldn't.
- **Repeated denied tool calls / loop behavior**: not specially handled this milestone. `max_turns` is the only bound. Real loop detection (ARCHITECTURE §5.5) is a hardening-phase concern; flagging it as a known, accepted gap rather than silently omitting it.

## 8. Database impact

**One small, additive migration** — not zero this time, and worth being precise about why: `agent_runs` is missing a `cache_write_tokens` column that `usage_records` already has (an asymmetry in M1's original schema, not something this milestone introduces). Since M4 is the first real writer of either table, this is the moment to notice and fix it rather than carry the inconsistency forward:
```sql
ALTER TABLE agent_runs ADD COLUMN cache_write_tokens bigint NOT NULL DEFAULT 0;
```
Everything else — `usage_records`, `tool_calls` (both M1), `governance_rules` (Phase 1.5), `agent_runs.provider_session_id`/`input_tokens`/`output_tokens`/`cache_read_tokens`/`cost_usd` — already exists with no writer until now. `agents.model` (existing, nullable) gets populated for `developer` via a seed update (§9), not a schema change.

**Explicitly not built**: an `artifacts` table (§0.4), session-resume plumbing (§2), a stored/hashed rendered-context column for reproducibility (a real gap ARCHITECTURE.md §10.2 itself names — "the architecture records the template version, not the fully assembled text a specific run actually saw" — genuinely worth closing eventually, but nothing in this milestone's approved goals needs it, so it stays a noted gap rather than a preemptive column).

## 9. Services/files changed

**New:**
- `runner/agent_sdk.py` — the adapter (§2)
- `backend/src/acc/api/internal/context.py` or an extension to `internal/runs.py` — `GET /internal/runs/{id}/context`
- `backend/src/acc/queries/context.py` (or similar) — assembles the four layers (§3) from `governance_rules`, `project_context_entries`, `agents`, `tasks`
- `backend/src/acc/commands/runs.py` additions: `record_tool_call`, `record_usage` (both explicitly deferred from M1 to "alongside the real adapter" — this is that moment), `record_agent_message`
- Three new internal endpoints (§6)
- One migration (§8)

**Modified:**
- `runner/main.py` — the `AGENT_ADAPTER` dispatch (§3)
- `runner/Dockerfile` — Node.js + `claude-agent-sdk` (§1)
- `acc/orchestrator/launcher.py` — inject `ANTHROPIC_API_KEY`/cost/turn/wall-clock settings; `reconcile_active_runs` gains the wall-clock check (§5)
- `acc/config.py` — four new settings (§1)
- `acc/domain/events.py` — `TOOL_CALL_DENIED`, `USAGE_RECORDED` event types + payloads
- `acc/commands/seed.py` — populate `developer`'s `agents.model` (recommend `claude-sonnet-5`, overridable)

**Unchanged, reused as-is**: `git_ops.py`/`git_askpass.py` (M3, called from the new flow instead of the old stub), the entire M2 cancellation/reconciliation/cleanup machinery, the internal-API auth mechanism (`RunClaimsDep`), the operator-facing `/api/v1/runs/*` routes.

## 10. Milestone breakdown

**M4.1 — SDK plumbing, no governance or cost enforcement yet.** Runner image + SDK, `agent_sdk.py`, `AGENT_ADAPTER` dispatch, the context endpoint (governance rules included in the payload but not yet enforced), `agent.message` streaming only. Exit: a real, tiny, cheap task runs the SDK end to end and produces at least one message.

**M4.2 — Tool permissions + governance enforcement.** `can_use_tool` gate, `PostToolUse` hook, `tool_calls` recording, `tool_call.denied` events, the fail-closed `require_approval` behavior (§4.2) verified against real seeded rules.

**M4.3 — Cost/token limits + failure handling.** `usage_records`/`agent_runs` accumulation, the in-runner cost-ceiling check, the orchestrator's wall-clock addition, the partial-progress-on-failure git flow (§7).

**M4.4 — Real git integration.** Replace M3's fixed marker-file stub with the agent's actual changes; real commit messages and `result_summary` instead of constants.

**M4.5 — Testing + live verification.** The full test pyramid (§11), full suite run, live verification against a real (tiny, cheap) task with a real Anthropic API key.

## 11. Testing strategy

No real Anthropic API calls in the default test suite — matches this project's own established `pytest.mark.integration`/`pytest.mark.docker` gating exactly, extended with one more tier:

- **Unit**: governance-gate logic (given a rule set + a proposed tool call, allow/deny/fail-closed-on-`require_approval`) and cost-ceiling arithmetic, both pure functions, no SDK involved. Event-mapping logic tested against hand-constructed fake SDK message objects (matching the SDK's documented message shapes), not a real session.
- **Integration (mocked SDK, real Postgres/Docker where relevant)**: the adapter's *own* orchestration — loop structure, heartbeat interleaving between turns, error handling, partial-progress-on-failure — tested against a hand-rolled fake client implementing the same async-message-stream interface the real SDK exposes. Deterministic, free, runs in the normal suite.
- **Real-Docker (no live API)**: confirms the runner image actually has Node + the SDK installed and importable, correctly receives its context/env, and can be stopped cleanly mid-"session" (a deliberately-invalid placeholder API key forces an immediate, realistic failure path — proving the failure/cleanup machinery works against the real image without spending anything).
- **New tier: `pytest.mark.live_llm`, skipped by default, opt-in via an env var** (mirrors exactly what PLAN-PHASE-2.md's original testing strategy proposed for this milestone). Makes one real, minimal, cheap call end to end against a real repository-linked task. Requires a real Anthropic API key — see §12.
- **Live verification** (same bar as every prior milestone): a real assignment, a real small task, watched end to end — event stream visible via the existing runs/events APIs, cost recorded, a mid-run cancel actually stopping the container, a deliberately-triggered `deny`-rule tool call actually refused.

## 12. Open decisions needing your input before I code

**A real Anthropic API key** is needed for §11's opt-in live tier and for live verification — not for M4.1–M4.4's implementation or the default test suite, which use a mocked SDK client throughout. Please provide one (or confirm using an existing one), ideally with a spend limit configured on Anthropic's side (§4.4).

Everything below has a stated recommendation; flag any you'd like changed, otherwise I'll proceed with these:
- **Model for the developer agent**: `claude-sonnet-5` (§9).
- **Cost ceiling**: $2.00 per run (§5, §1).
- **Max turns**: 20 (§5, §1).
- **Wall-clock cap**: 15 minutes (§5, §1).
- **`require_approval` handling**: fail closed, unchanged from M2/M3 (§4.2) — now with real consequences, restated for explicit sign-off rather than silently inherited.
- **Allowed tool list**: `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash` only (§4.1) — no `WebFetch`/`WebSearch`.

Waiting for your approval on this plan, and the decisions above, before writing any code.
