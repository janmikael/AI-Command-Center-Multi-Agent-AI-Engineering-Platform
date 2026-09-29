# Architecture Review: ARCHITECTURE.md vs. the Implemented System

Status: **Report only. No implementation changed.**
Date: 2026-09-29
Scope: reconcile [ARCHITECTURE.md](ARCHITECTURE.md) (the original Phase-0 design, dated 2026-09-24, written before any code existed) against what commits `0f60a33` through `163c975` actually built.
Method: every claim below is checked against a real source — a phase completion report, a plan document's own "reading the scope precisely" section, a migration file, or the current directory tree — not against memory of what was *intended*. Where I couldn't verify something directly, I've said so rather than guessed.

---

## 1. Implemented architecture

### Phase 0 — Foundation (complete, `0f60a33`)

Deployable skeleton, matching ARCHITECTURE.md §7's Phase 0 row closely: Docker Compose stack (caddy/web/api/postgres/redis), Alembic baseline, structured logging with secret redaction, `/healthz`+`/readyz`, CI (`.github/workflows/ci.yml`, verified running all steps locally — see drift note in §4). **Auth deviated from the original design**: ARCHITECTURE.md's Phase 0 row says "passkey or password+TOTP"; what was actually built is email+password with Argon2id hashing, HMAC-stored session tokens, and TOTP explicitly deferred (PHASE-0-REPORT.md, finding #5). No passkey support exists anywhere in the codebase.

### Phase 1 — Dashboard core (complete, `ece39ec`)

Full data model (projects, repositories, agents, tasks, append-only `events` with a database-enforced insert-only trigger — not just application-level discipline), the REST API (18 endpoints), real cross-process WebSocket realtime (`RedisPublisher` → Redis pub/sub → per-process `Hub`, verified against two independent API instances, not an in-process shortcut), and every dashboard page ARCHITECTURE.md's folder structure names for this phase: Overview, Agents, Agent detail, Tasks Kanban, Task detail, Activity feed.

Matches ARCHITECTURE.md §5.1's task state machine closely, with one confirmed simplification: no dedicated `POST /tasks/{id}/start` endpoint — the generic `POST /tasks/{key}/transition` covers the same `backlog → planning` move (PLAN-PHASE-2.md §0.2, checked against the actual Phase 1 code before Phase 2 began).

### Phase 1.5 — Global AI governance + project context (complete, `08db9486`)

Two additive tables: `governance_rules` (category/title/statement/effect, plus `match`/`applies_to`/`priority` — deliberately shaped to match ARCHITECTURE.md §2.2's future `policies` table) and `project_context_entries` (per-project knowledge, four kinds: architecture/convention/integration/gotcha/glossary). CRUD APIs and a `/governance` UI page. Explicitly **not** built this phase: MCP gateway, any AI agent, a project-context editor UI (data model and API exist; there is still no UI to write a context entry through, only to read the seeded/API-created ones — see §4).

### Phase 2 M1 — Agent run data model (complete, `9c9f21e`)

`agent_runs`, `usage_records`, `tool_calls`, `file_changes`, `jobs` tables; the run-status state machine (`acc/domain/agent_run.py`); the durable job queue (`SELECT ... FOR UPDATE SKIP LOCKED`, ADR-2). `tool_calls`/`usage_records` were built with no writer yet, deliberately — Phase 2 M1's own scope was "the row + job-queue side only."

### Phase 2 M2 — Runner infrastructure (complete, `02a249d`)

The `orchestrator` process (new compose service, same `acc-api:local` image, `acc.orchestrator.main` entrypoint — matching ADR-1's "separate processes, one codebase" exactly). The sandboxed runner container: read-only rootfs, `cap_drop: ALL`, non-root, `runner-egress`-only network, no Docker socket (`docker-socket-proxy` is the only thing that touches it, scoped to container-lifecycle verbs only). Per-run HMAC-signed tokens authenticate the runner's calls back to a new `/internal/*` API surface, unreachable through Caddy.

**Confirmed structural divergence from ARCHITECTURE.md's folder sketch**: the design names `backend/src/acc/runner/` as a nested package inside the layered `acc` import graph. What was actually built is a **top-level `runner/` directory**, a sibling of `backend/`, entirely outside `acc` and the import-linter contract — not accidentally, but because the runner must never import `acc.db`/`acc.commands`/`sqlalchemy`/etc. at all (it never receives a database credential), and keeping it a physically separate codebase makes that guarantee structural rather than just a lint rule. Documented in PLAN-PHASE-2.md §1.5 at the time.

### Phase 2 M3 — Repository and workspace access (complete, `6fe7c37`)

`GIT_ASKPASS`-only credential injection (never a git remote URL or config file), branch-name allowlisting (`acc/*` only, enforced independently in the runner before any git command runs), clone/branch/commit/push wrapped in a fixed, non-extensible five-function module (`runner/git_ops.py`). A hermetic dev-only git server (`infra/git-test-server/`) stands in for a real repository — no external repository has been connected in any milestone through Phase 2 M4.3.

### Phase 2 M4.1 — Claude Agent SDK integration (complete, `4daf158`)

The actual first AI worker. **Confirmed structural divergence from ARCHITECTURE.md §4.5**: the design sketches an `AgentAdapter` Protocol living inside `acc/adapters/`, driven by the orchestrator. What was actually built is `runner/agent_sdk.py` — the adapter runs *inside the sandbox*, not the orchestrator, because tool execution (`Read`/`Write`/`Edit`/`Bash`) has to happen against the sandboxed filesystem directly. There is no `acc/adapters/` package at all. `openai_responses.py`/`claude_messages.py` (architect/designer) remain entirely unbuilt.

The four-layer agent context (ARCHITECTURE.md §10.2 — global rules + role, then project context + task) is real and rendered per run via a new `GET /internal/runs/{id}/context` endpoint. Governance enforcement runs as a `can_use_tool` callback *inside the runner process* — matching PLAN-PHASE-2.md §0.4's own explicit statement that this is "narrower than the target design" (no MCP gateway exists to enforce it centrally yet). `require_approval`-effect rules fail closed (denied), since no approval mechanism exists.

### Phase 2 M4.2 — Tool execution hardening (complete, `c79b67c`)

Tool-call lifecycle completion (a `PostToolUse` hook), layered secret redaction, governance match extended to `path_regex` (alongside `tool`/`command_regex`), partial-usage reporting on cancellation/crash, richer failure classification, a wall-clock timeout warning, and two new operator-facing read endpoints (`GET /runs/{id}/tool-calls`, `GET /runs/{id}/usage`). `blocked_by_effect` (`deny` vs `require_approval`) was added as explicit **approval-state preparation** — a queryable distinction, not an approval mechanism.

### Current schema state

17 migrations (`0001` through `0017`), 8 operator-facing API routers (`agents`, `auth`, `events`, `governance`, `health`, `overview`, `projects`, `runs`, `tasks`, `ws`). No `acc/gateway/` package, no `mcp-servers/` directory, no `approvals` table — none of these exist anywhere in the codebase yet.

---

## 2. Partially implemented

### M4.3 — First complete real AI developer workflow (**PAUSED**, prep at `163c975`)

Per your instruction, tracked precisely in [PLAN-PHASE-2-M4.3.md](PLAN-PHASE-2-M4.3.md)'s status line: **preparation complete, live validation pending.** What's actually done and tested (602/602 backend tests passing, none needing a real key): the developer role prompt now explicitly instructs inspect-before-edit, minimal changes, run-tests-before-finishing, and summarize-for-review; the hermetic test repository now seeds real, editable content instead of just a README; a real build-caching bug in `scripts/test-backend.sh` was found and fixed along the way.

**What has not happened, at all**: a real Claude Agent SDK session actually executing a task, real tool calls against real code, a real git commit landing on a real branch, a real usage/cost record, and the resulting task actually reaching human review with real content to review. No `ACC_ANTHROPIC_API_KEY` is configured (centrally managed, not provided). This is the one piece of the entire architecture review's "Implemented" section above that has never been exercised end-to-end with a real model — every M4.1/M4.2 live check deliberately used an invalid key to prove plumbing without spending anything, and M4.3 is where that was supposed to stop being true. It hasn't yet.

---

## 3. Future architecture (designed in ARCHITECTURE.md, not built)

### MCP gateway expansion

ARCHITECTURE.md §4 designs a single `mcp-gateway` service that every agent talks to, fronting filesystem/terminal/git/database/browser tool servers plus `acc-control` (platform tools), with policy evaluation, approval holds, and redaction centralized there (ADR-3, ADR-4's "hybrid" note in §4.4). **None of this exists.** What stands in for it today, explicitly narrower by design (PLAN-PHASE-2.md §0.4, restated in every M4 plan since): governance enforcement as a `can_use_tool` callback inside each runner process, checking a per-run snapshot of `governance_rules`. This works for one agent, one tool surface (`Read`/`Write`/`Edit`/`Glob`/`Grep`/`Bash`), enforced independently in every container. It does not centralize policy across agents, doesn't proxy `db`/`browser`/`terminal` as separate MCP servers, and has no `acc-control` platform-tool protocol (`acc.get_task_context()`, `acc.log_decision()`, `acc.request_merge()`, etc. — none of these exist; the equivalent context is fetched via a plain internal REST call, and there is no agent-initiated request path for a privileged action at all).

### Multi-agent workflow

ARCHITECTURE.md §5.2/§5.3 designs a data-driven workflow engine (plan → design → implement → code_review → ui_review → merge stages), the OpenAI-backed architect and Claude-Messages-backed designer agents, subtask trees with dependencies, and a review loop with a loop cap escalating to a human. **None of this exists.** `architect` and `designer` are real rows in the `agents` table (seeded since Phase 1) with `adapter='none'` — they have never run. Only one agent (`developer`, `claude_agent_sdk`) has ever executed, and every milestone through M4.3 has explicitly excluded multi-agent workflows from scope. There is no `workflows` table, no subtask-dependency scheduling, no review-loop cap.

### Approval execution

ARCHITECTURE.md §5.4 designs two mechanisms — platform-executed privileged actions (merge/deploy/migration, with a payload-hash re-verification step so an approved action can't silently execute something different) and inline tool holds (a paused tool call, resumed or denied by an operator decision). **Neither exists.** There is no `approvals` table, no `POST /approvals/{id}/approve|reject` endpoint, no platform executor with its own elevated credentials, no run-suspend-and-resume capability. Today, a `require_approval`-effect governance rule is mechanically indistinguishable in *outcome* from a `deny` rule — both refuse the tool call outright. M4.2's `blocked_by_effect` column is the only piece of this that exists, and it is explicitly inert: a queryable record of "this would need a human, if that existed yet," read by nothing.

### Company AI knowledge layer

This isn't a named section in ARCHITECTURE.md — the closest existing concept is `project_context_entries` (Phase 1.5: per-project architecture/convention/integration/gotcha/glossary notes, rendered into layer 3 of the four-layer agent context). That data model and API exist, but: there is still no UI to create or edit an entry (Phase 1.5 explicitly deferred this), no cross-project or organization-wide knowledge base, no ingestion of external documents, and no retrieval mechanism beyond "every entry for this project, every time" (no embeddings, no relevance ranking, no size management — `render_task_prompt` just concatenates all of them). If "company AI knowledge layer" means something broader than per-project notes — an org-wide corpus agents draw from across projects — that's a genuinely new design surface ARCHITECTURE.md doesn't cover at all, not an extension of something partially built. Worth a dedicated design pass rather than assuming it's `project_context_entries` grown up.

---

## 4. Documentation drift

Concrete places ARCHITECTURE.md no longer matches reality, beyond the "not built yet" items already covered in §3 (which are drift too, just expected drift):

1. **Runner package location.** §6's folder structure puts runner code at `backend/src/acc/runner/`. It actually lives at top-level `runner/`, deliberately outside `acc` entirely (§1 above). A reader following the folder structure today would look in the wrong place.
2. **Adapter package location.** §6 and §4.5 put the Claude Agent SDK adapter at `backend/src/acc/adapters/claude_agent_sdk.py`, driven by the orchestrator. It actually lives at `runner/agent_sdk.py`, driven from inside the sandbox. There is no `acc/adapters/` directory at all.
3. **Auth mechanism.** §7's Phase 0 row says "passkey or password+TOTP." Built: email+password, TOTP deferred, no passkey support.
4. **`agent_runs.status`/similar enum columns.** §2.2's DDL sketch uses Postgres `ENUM` types (`run_status`, `task_status`, `approval_status`). Actual practice since Phase 1.5 uses `text` + `CHECK` for every new status-like column, a deliberate, documented precedent shift (PLAN-PHASE-2.md §0.6) because `ENUM` alteration is higher-friction than a `CHECK` constraint and these columns are expected to gain values later (e.g. `waiting_approval`). `tasks.status` itself is still the original `ENUM`, unchanged since Phase 1 — so the codebase now has both patterns, and ARCHITECTURE.md documents only the older one.
5. **REST endpoint list (§3.2).** Several named endpoints don't exist: `POST /tasks/{id}/start` (folded into `/transition`, §1 above), all of `/approvals/*`, `POST /system/emergency-stop`, `GET /agents/{id}/usage`, `GET /tasks/{id}/diff`. Endpoints that do exist and aren't in the original list: `GET /runs/{id}/tool-calls`, `GET /runs/{id}/usage` (both M4.2), `POST /internal/runs/{id}/context` and the other `/internal/*` runner-facing surface (§3.5 gestures at this existing but doesn't enumerate it — reasonably, since it predates any real internal API).
6. **`tool_calls`/`agent_runs` DDL sketch (§2.2).** Missing several columns the real schema has: `agent_runs.cache_write_tokens`, `timeout_warning_emitted_at`; `tool_calls.tool_use_id`, `rule_id`, `blocked_by_effect`. Has columns the real schema doesn't: `tool_calls.server` (there's only one tool surface today, the runner's built-in tools — no per-server namespacing since no gateway exists), `tool_calls.policy_decision`/`approval_id` (no approval system, §3 above).
7. **`policies` table (§2.2)** doesn't exist under that name — its intended shape was deliberately pre-echoed into `governance_rules.match`/`applies_to`/`priority` instead (Phase 1.5's own stated reasoning), so a future gateway "can enforce these rules with no schema redesign." Whether that future table is still `policies` or just an evolved `governance_rules` is an open question ARCHITECTURE.md doesn't anticipate.
8. **Frontend has no Phase-2 surface at all.** Every page ARCHITECTURE.md's folder structure names for `runs/`, `approvals/` either doesn't exist (`approvals/`) or was never built despite the backend being ready for it (`runs/[id]/` — the live run console). An operator today can watch a task move through the Kanban board, but the moment it reaches `working`, everything about the actual agent run (tool calls, usage, governance denials, failure detail) is API-only — visible via `curl`, invisible in the dashboard. This is the single largest gap between what the backend now supports and what an operator can actually see without leaving the terminal.
9. **§9's open questions** (repository hosting, deployment target, budget ceiling, notification channel, hosting for ACC itself) are still genuinely open — M3 through M4.3 all explicitly deferred connecting a real repository (hermetic git server used instead), and nothing in Phase 2 has touched deployment, notifications, or a real budget policy beyond the per-run `ACC_RUN_MAX_COST_USD` ceiling. Worth re-reading as still-current questions, not stale ones.
