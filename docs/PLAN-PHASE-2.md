# Implementation Plan: Phase 2 (First Real Agent — Developer)

Status: **Awaiting approval. No code written.**
Parent documents: [ARCHITECTURE.md §1, §4, §5](ARCHITECTURE.md), [PHASE-1.5-REPORT.md](PHASE-1.5-REPORT.md)
Date: 2026-09-24

## Scope, as given

Implement only:
- Claude Agent SDK integration
- agent-runner sandbox
- task execution workflow
- event streaming into existing dashboard
- usage/cost tracking
- run history
- cancel/stop controls

Do not implement: multi-agent workflow, designer agent, full MCP gateway expansion, deployment automation.

## 0. Reading the scope precisely

Six judgment calls, checked against the actual Phase 1/1.5 code rather than assumed, before any DDL below:

**0.1 The task state machine barely needs to change.** `task_status.py`'s `OPERATOR_TRANSITIONS` already allows `planning → working` and `working → {review, blocked, failed, cancelled}` — every transition a run's lifecycle needs already exists, because Phase 1's board lets an operator drag a card through exactly this path manually. The only new work is a **side effect**: when a `planning → working` transition lands on a task with `assigned_agent_id` set to an agent whose `adapter != 'none'`, enqueue a `start_run` job in the same transaction (ADR-2). That means this phase's one necessary edit to a Phase 1 file is `commands/tasks.py::transition_task` — everything else is additive. I'm calling this out explicitly rather than silently touching a file you've asked me to keep intact in prior phases: this edit is in scope because wiring real execution into the existing lifecycle *is* Phase 2, not incidental scope creep.

**0.2 No new task statuses, no `/tasks/{id}/start` endpoint.** ARCHITECTURE.md's aspirational REST list has a dedicated `/start` endpoint; actual Phase 1 already covers the same move through the generic `POST /tasks/{key}/transition`. Phase 2 reuses it as-is.

**0.3 The orchestrator doesn't get new state-machine transitions — it gets a narrower *authorization* list.** `task_status.py` currently carries a comment: *"From Phase 2 the orchestrator gets its own transition table; agents never change status themselves."* Re-reading it against the actual transition table: every move the orchestrator needs (`working→review`, `working→failed`, `working→blocked`) is already legal for the operator actor. What's actually needed is a small `ORCHESTRATOR_ALLOWED` subset — which of the already-legal moves the *orchestrator*, as an actor, is permitted to trigger automatically. `review → completed` stays human-only, deliberately: Guiding Principle 3 ("agents propose, the platform executes... after human approval") means a run finishing review is not enough to complete a task by itself.

**0.4 No MCP gateway means tool enforcement has to live somewhere else for this phase, narrower than the target design.** Phase 1.5 already built `governance_rules`. Phase 2's `can_use_tool` check runs *inside the runner process* against a snapshot of `deny`-effect rules fetched at run start. Rules with effect `require_approval` have no approval mechanism to satisfy yet (that's Phase 3's gateway) — see the fail-closed decision in §4 and the explicit sign-off request in §7.

**0.5 "Run history" and "event streaming" need no changes to `realtime/hub.py` or the WebSocket protocol.** `topics_for()` already keys off `task_key`/`agent_slug` on the wire event; every new run/tool-call/usage event naturally carries both (a run belongs to exactly one task and one agent), so it already reaches the existing `"activity"` topic every client already subscribes to. The only frontend change is extending `queryKeysToInvalidate` for the new event-type prefixes, the same pattern Phase 1.5 used for `governance_rule.*`.

**0.6 Following Phase 1.5's own precedent, new enum-like columns use `text` + `CHECK`, not a Postgres `ENUM`.** ARCHITECTURE.md's DDL sketch for `agent_runs.status` uses `CREATE TYPE run_status AS ENUM (...)`, matching how `tasks.status` was actually built in Phase 1. But Phase 1.5 deliberately moved away from that for new tables (`governance_rules.category`/`effect`, `project_context_entries.kind`) specifically because Postgres `ENUM` alterations are more migration-friction than a `CHECK` constraint, and `run_status` is very likely to gain values in Phase 3 (e.g. `waiting_approval`). I'm following the newer, established precedent rather than the older sketch.

## 1. Architecture impact

### 1.1 New processes

| Process | Codebase | How it runs |
|---|---|---|
| `orchestrator` | Same `acc` package (ADR-1: "one Python codebase, separate processes") | New compose service, reuses the `acc-api:local` image, different `command:` — a new `acc.orchestrator.main` asyncio entrypoint, not a new Docker build |
| `agent-runner` | New, small, purpose-built image | Ephemeral — one container per run, created and destroyed by the orchestrator |

The orchestrator is a single process in Phase 2 (no Redis-leader election yet — one operator, low concurrency). The `jobs` table's `SKIP LOCKED` design (§2) already makes adding more workers later a config change, not a redesign, so this isn't a dead end, just deferred until it's actually needed.

### 1.2 New domain modules (pure, per the existing import-linter contract)

- `acc/domain/agent_run.py` — run-status transitions, heartbeat-timeout policy, cost-ceiling check. Mirrors `task_status.py`'s shape exactly.
- `acc/domain/task_status.py` — gains `ORCHESTRATOR_ALLOWED` (§0.3). `validate_transition()` itself is unchanged.
- `acc/domain/agent_status.py` — swaps `active_task_count` + event-based `last_activity_at` for `active_run_count` + heartbeat-based, exactly as the file's own docstring already anticipates. `derive_agent_status()`'s signature changes; its callers (the agents query layer) update to pass run data instead of task-assignment counts.

### 1.3 New layer: `acc/orchestrator/`

Sits at the same level as `acc/api` in the import-linter "Layering" contract — both consume `acc.commands | acc.queries | acc.realtime`, neither imports the other.

- `scheduler.py` — the job loop, `SELECT ... FOR UPDATE SKIP LOCKED` over `jobs` (ADR-2)
- `launcher.py` — talks to `docker-socket-proxy` to create/start/stop the runner container; mounts the worktree; injects per-run secrets as container env
- `watchdog.py` — heartbeat-timeout and wall-clock-timeout enforcement (independent of the runner behaving correctly — see §4.5)
- `main.py` — the asyncio entrypoint

### 1.4 New layer: `acc/adapters/`

Only `claude_agent_sdk.py` is implemented this phase, against the `AgentAdapter` Protocol already designed in ARCHITECTURE.md §4.5. `openai_responses.py`/`claude_messages.py` (architect/designer) stay unbuilt — out of scope by your own instruction.

### 1.5 New: `acc/runner/` — deliberately outside the layered `acc` import graph

This is the code that runs *inside* the sandboxed, untrusted container. It must never import `acc.db`, `acc.commands`, or any database driver — it never receives a database credential, full stop. It talks to our own backend only over HTTP, through a new small internal API surface, authenticated by a per-run signed token instead of the operator's session cookie.

A new import-linter contract enforces this mechanically, the same way domain purity already is:

```toml
[[tool.importlinter.contracts]]
name = "Runner is sandboxed: no DB access, no commands/queries"
type = "forbidden"
source_modules = ["acc.runner"]
forbidden_modules = ["acc.db", "acc.commands", "acc.queries", "sqlalchemy", "asyncpg", "redis"]
```

### 1.6 New internal API surface

`acc/api/internal/runs.py`, added to the existing `api` service (not a new deployable):
```
POST /internal/runs/{id}/events        (batch: run.step, tool_call.*, file.changed, agent.message, agent.decision)
POST /internal/runs/{id}/heartbeat
POST /internal/runs/{id}/usage
GET  /internal/runs/{id}/context       (rendered 4-layer prompt: global rules, role, project context, task — §10.2)
POST /internal/runs/{id}/complete      (result_summary, branch pushed, final usage)
POST /internal/runs/{id}/fail          (error, classification)
```
Authenticated by a per-run JWT (`run_id`, `agent_id`, expiry = the run's max timeout), issued by the orchestrator at launch — never the session cookie, and scoped so a token for one run cannot write events onto another run (tested explicitly, §6).

### 1.7 New operator-facing API

`acc/api/routers/runs.py`:
```
GET  /runs?task_id&agent_id&status
GET  /runs/{id}
GET  /runs/{id}/events?cursor
GET  /runs/{id}/tool-calls
GET  /runs/{id}/file-changes
POST /runs/{id}/cancel
```

### 1.8 Network topology (new)

`compose.prod.yml` currently makes the `internal` network fully internet-isolated (`internal: true`) — correct for `api`/`web`/`postgres`/`redis`, but the runner needs outbound internet (Anthropic API, git remote, package registries) and must **never** reach Postgres or Redis even if compromised. Proposal: a new `runner-egress` network, with a route to the internet, joined only by runner containers and by `api` (for the new internal endpoints) — never by `postgres`/`redis`, and the runner never joins `internal`.

## 2. Database migrations required

Five migrations, purely additive — nothing in Phase 1/1.5's schema is altered except one new nullable column on `tasks` and one on `agents` (both additive, both noted below).

**Migration 0009 — `agent_runs`**
```sql
CREATE TABLE agent_runs (
  id                  uuid PRIMARY KEY,
  task_id             uuid NOT NULL REFERENCES tasks(id),
  agent_id            uuid NOT NULL REFERENCES agents(id),
  status              text NOT NULL DEFAULT 'queued',   -- queued|starting|running|succeeded|failed|cancelled|timed_out
  provider_session_id text,                              -- Agent SDK session id, for resume
  container_id        text,
  workspace_ref        text,                              -- git worktree path
  branch_name          text,
  current_step         text,                              -- display-only, agent-reported
  cancel_requested_at  timestamptz,                        -- set by POST /runs/{id}/cancel
  queued_at            timestamptz NOT NULL DEFAULT now(),
  started_at           timestamptz,
  ended_at             timestamptz,
  last_heartbeat_at    timestamptz,
  result_summary        text,
  error                 jsonb,
  input_tokens          bigint NOT NULL DEFAULT 0,
  output_tokens         bigint NOT NULL DEFAULT 0,
  cache_read_tokens     bigint NOT NULL DEFAULT 0,
  cost_usd              numeric(12,6) NOT NULL DEFAULT 0,
  created_by            uuid REFERENCES users(id) ON DELETE SET NULL,
  CHECK (status IN ('queued','starting','running','succeeded','failed','cancelled','timed_out'))
);
CREATE INDEX ON agent_runs (agent_id, status);
CREATE INDEX ON agent_runs (task_id, queued_at DESC);
-- Safety net beyond application logic: at most one non-terminal run per task.
CREATE UNIQUE INDEX ix_agent_runs_one_active_per_task ON agent_runs (task_id)
  WHERE status IN ('queued','starting','running');
```

**Migration 0010 — `usage_records`** (one row per SDK turn, so cost is graphable over a run's timeline, not just a final total)
```sql
CREATE TABLE usage_records (
  id                 uuid PRIMARY KEY,
  run_id             uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  agent_id           uuid NOT NULL REFERENCES agents(id),
  provider           text NOT NULL,          -- 'anthropic'
  model              text NOT NULL,
  input_tokens       bigint NOT NULL DEFAULT 0,
  output_tokens      bigint NOT NULL DEFAULT 0,
  cache_read_tokens  bigint NOT NULL DEFAULT 0,
  cache_write_tokens bigint NOT NULL DEFAULT 0,
  cost_usd           numeric(12,6) NOT NULL DEFAULT 0,
  cost_source        text NOT NULL,          -- 'provider_reported' | 'estimated'
  recorded_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (cost_source IN ('provider_reported', 'estimated'))
);
CREATE INDEX ON usage_records (run_id);
CREATE INDEX ON usage_records (recorded_at);
```

**Migration 0011 — `tool_calls` and `file_changes`** (trimmed from ARCHITECTURE.md's sketch: no `policy_decision`/`approval_id`, since there's no gateway to make that decision yet)
```sql
CREATE TABLE tool_calls (
  id             uuid PRIMARY KEY,
  run_id         uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  tool           text NOT NULL,              -- 'Bash' | 'Edit' | 'Write' | 'Read' | ...
  arguments      jsonb NOT NULL,             -- redacted before storage, §4.7
  allowed        boolean NOT NULL,           -- this run's can_use_tool decision
  denial_reason  text,
  result_excerpt text,                       -- truncated + redacted, capped 8KB
  exit_code      int,
  duration_ms    int,
  started_at     timestamptz NOT NULL,
  ended_at       timestamptz
);
CREATE INDEX ON tool_calls (run_id, started_at);

CREATE TABLE file_changes (
  id            uuid PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  task_id       uuid NOT NULL REFERENCES tasks(id),
  path          text NOT NULL,
  change_type   text NOT NULL,               -- added | modified | deleted | renamed
  additions     int,
  deletions     int,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (change_type IN ('added', 'modified', 'deleted', 'renamed'))
);
CREATE INDEX ON file_changes (run_id);
```

**Migration 0012 — `jobs`** (the durable outbox, ADR-2 — scoped to exactly the three job kinds Phase 2 needs)
```sql
CREATE TABLE jobs (
  id          uuid PRIMARY KEY,
  kind        text NOT NULL,                 -- 'start_run' | 'cancel_run' | 'timeout_check'
  payload     jsonb NOT NULL,
  run_after   timestamptz NOT NULL DEFAULT now(),
  attempts    int NOT NULL DEFAULT 0,
  locked_by   text,
  locked_at   timestamptz,
  status      text NOT NULL DEFAULT 'pending',   -- pending | running | done | dead
  last_error  text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CHECK (status IN ('pending', 'running', 'done', 'dead'))
);
CREATE INDEX ON jobs (status, run_after);
```

**Migration 0013 — small additive columns**
```sql
ALTER TABLE tasks ADD COLUMN branch_name text;               -- populated once the agent pushes
ALTER TABLE agents ADD COLUMN max_concurrent_runs int NOT NULL DEFAULT 1;
-- agents.model already exists (nullable) — Phase 2 populates it for 'developer' via a seed update.
```

Nothing else on `tasks`/`agents` changes. No `budget_usd`/`spent_usd`/`attempt`/`max_attempts` columns — see the budget decision in §7.

**New event types** (extend `acc.domain.events.EventType`, same append-only pattern as every prior phase):
```
run.queued | run.started | run.step | run.heartbeat_missed
run.succeeded | run.failed | run.cancelled | run.timed_out
tool_call.started | tool_call.completed | tool_call.denied
file.changed
usage.recorded
```
`agent.message` and `agent.decision` already exist from Phase 1 (currently unused placeholders) — reused as-is for the SDK's narrative and decision messages, not redefined.

**Realtime:** no `hub.py` change (§0.5). Frontend `queryKeysToInvalidate` gets one addition: a `run_id`-bearing event invalidates `queryKeys.runs.detail(id)`, mirroring Phase 1.5's `type`-prefix pattern for governance events.

## 3. New services/components

**Backend** (same `acc` package and image, except the runner):
- `acc/domain/agent_run.py`, `acc/domain/task_status.py` (+`ORCHESTRATOR_ALLOWED`), `acc/domain/agent_status.py` (updated inputs)
- `acc/commands/runs.py` — `enqueue_start_run`, `record_heartbeat`, `record_step`, `complete_run`, `fail_run`, `request_cancel`
- `acc/queries/runs.py` — list/detail, tool-calls, file-changes, usage rollups
- `acc/orchestrator/` — `scheduler.py`, `launcher.py`, `watchdog.py`, `main.py`
- `acc/adapters/` — `base.py` (Protocol), `claude_agent_sdk.py`
- `acc/runner/` — the in-container entrypoint: context rendering (§10.2's four layers), the SDK's tool loop, the `can_use_tool` denylist, git worktree setup/commit/push, event/usage/heartbeat reporting, `cancel_requested_at` polling between turns
- `acc/api/internal/runs.py` — the per-run-JWT-authenticated surface (§1.6)
- `acc/api/routers/runs.py` — the operator-facing surface (§1.7)
- Alembic migrations 0009–0013

**Infra:**
- `images/runner/Dockerfile` — Python + Node + git + the Claude Agent SDK's dependencies, non-root user, minimal OS packages, no shell utilities beyond what the sandboxed terminal tool needs
- `docker-socket-proxy` service (e.g. `tecnativa/docker-socket-proxy`), scoped to container create/start/stop/kill only — no exec, no arbitrary image pulls (the runner image is pre-built, not agent-selectable)
- `runner-egress` network (§1.8)
- `orchestrator` compose service (reuses `acc-api:local`, different command, joins `internal` + a private link to `docker-socket-proxy`, does **not** join `runner-egress` itself — only the containers it launches do)
- `compose.prod.yml`: resource limits for the runner containers (cpus, memory, pids) set by the launcher at container-create time, matching the pattern already used for `api`/`web`/`postgres`/`redis`

**Frontend:**
- Task detail page: new "Run" section — live status, streaming step/tool-call log, cost-so-far, Cancel button. No new route; Phase 2 has exactly one run per task.
- Agent detail page: extend with a run-history list (status, duration, cost) alongside the existing `recent_events`.
- `lib/api/hooks.ts`: `useRun`, `useRunEvents` (mirrors `useTaskEvents`'s infinite-query shape), `useCancelRun`
- `format.ts`: run-status label/color map, mirroring `TASK_STATUS_COLOR`

## 4. Security considerations

Real API keys, real shell/file access, and a real (if narrow) blast radius all enter the system for the first time this phase. This section is the one I most want your explicit read on before I write a line of code.

**4.1 Sandbox isolation** (ADR-6, ADR-9, Risk R4 — "treat the runner as hostile"): one ephemeral container per run; non-root user; `read_only` root filesystem with a tmpfs workspace mount; `cap_drop: [ALL]`; `no-new-privileges`; `pids_limit`; CPU/memory limits; **no Docker socket inside the runner** — only the orchestrator, through `docker-socket-proxy`, can create containers, so a compromised runner can't spawn siblings. Filesystem is confined to that run's own git worktree; nothing else from the host is bind-mounted.

**4.2 No credentials for our own systems ever enter the runner.** No `ACC_DATABASE_URL`, no `ACC_REDIS_URL`, no session secret. The runner receives exactly three secrets: the per-run JWT (scoped to that one run, short-lived), the Anthropic API key, and a scoped git push credential.

**4.3 Tool-boundary enforcement without the MCP gateway is deliberately narrower than the target design.** Phase 3 doesn't exist yet, so:
- The SDK's `can_use_tool` callback runs *inside the runner*, checking a snapshot of `deny`-effect `governance_rules` fetched once at run start. A denial blocks the call locally and is logged as `tool_call.denied`.
- `require_approval` rules have no approval mechanism to satisfy yet. **Proposed default: fail closed** — treat `require_approval` as a block, not a pass-through. Concretely, this means the seeded rules ("paid tools require approval," "third-party services/APIs require approval," "DB migrations require approval," "destructive commands require approval") all block the agent outright in Phase 2. This is a real behavior change worth your sign-off — see §7.2.
- This is in-process enforcement, not a separate process — weaker than the target design (a compromised runner could in principle patch its own check). Mitigated by defense in depth that doesn't depend on the check holding: no DB credentials, no merge/deploy capability, and branch protection on `main` configured at the git host itself (not just app-level policy), so even a fully bypassed check can't reach anything that matters.

**4.4 Git credentials:** recommend a fine-grained, single-repo-scoped credential (a GitHub fine-grained PAT or deploy key), with host-side branch protection on `main`, rather than a broad personal token. Injected as container env at launch, scrubbed from any logged tool-call arguments via the same redaction pass as everything else (§4.7). This needs a real answer from you before Phase 2 can be built — ARCHITECTURE.md §9 Q1 ("which repos, hosted where") has been an open question since the original architecture doc; it's now blocking. See §7.1.

**4.5 Cost and runaway-execution safety net** (Risk R2): two independent backstops, neither trusting the runner to police itself correctly —
- A hard `ACC_RUN_MAX_COST_USD` ceiling, checked by the runner after every SDK turn (the SDK reports usage per turn); exceeding it stops the loop and reports `run.failed`, no silent continuation.
- A wall-clock timeout enforced by the **orchestrator's watchdog**, independent of the runner: if `now - started_at` exceeds the configured max, the orchestrator kills the container directly through `docker-socket-proxy`, regardless of what the runner process is doing. This closes the gap where a compromised or hung runner ignores its own limit.

**4.6 Prompt injection** (Risk R3): a full mitigation is the gateway's job (Phase 3), out of scope here. The sandbox and credential-scoping above already bound the worst case: an injected instruction can't reach the database, can't merge or deploy, can't touch another project's data, and can't push anywhere but its own scratch branch.

**4.7 Secret redaction:** applied in `acc.runner` before events are posted to the internal API, and re-applied server-side in `acc/api/internal/runs.py` before writing to `tool_calls.arguments`/`result_excerpt` — so a runner-side bug in the first pass doesn't leak a secret into permanent storage. Same discipline ARCHITECTURE.md always intended for the gateway (§4.1 step 5), just enforced in two closer places since the gateway doesn't exist yet.

## 5. Development milestones

Five milestones, each independently committed, tested, and verified — same discipline as every prior phase.

**M1 — Data model + job queue skeleton.** Migrations 0009–0013, `acc.domain.agent_run`, `acc.orchestrator.scheduler` (claims and completes jobs — no container involved yet), `acc/commands/runs.py`, `acc/queries/runs.py`. Exit: a fake `start_run` job can be enqueued and claimed by the scheduler under concurrent load in a test, exactly once.

**M2 — Runner image + sandboxed execution, no SDK yet.** `images/runner/Dockerfile`, `docker-socket-proxy` + `runner-egress` network, `acc.orchestrator.launcher`, `acc/api/internal/runs.py` (JWT-authenticated), a trivial `acc.runner` entrypoint that proves the round trip (heartbeat, echo the task context, exit). Exit: moving an assigned task to `working` launches a real, isolated container that reports back and cleans up — verified live, not just in a test double.

**M3 — Claude Agent SDK adapter, real execution.** `acc.adapters.claude_agent_sdk`, the real `acc.runner` loop (context rendering, git worktree, the SDK's tool loop, the `can_use_tool` denylist, event/usage/tool-call/file-change reporting, commit+push). **The cost ceiling from §4.5 ships in this milestone, not M5** — this is the first milestone that spends real money, so the safety net can't wait for the "hardening" pass. Exit: assign a real small task, watch it edit a file and push a branch, with real (small) Anthropic spend.

**M4 — Dashboard.** Run console UI (task detail Run tab), cancel/stop end-to-end (operator click → API → job → container kill → task lands in `blocked`), agent detail's run history, usage/cost display. Exit: the whole loop is observable and controllable from the browser, live, in two tabs — the same bar Phase 1 M4 was held to.

**M5 — Reliability hardening.** Watchdog (heartbeat + wall-clock timeout independent of the runner), `timeout_check`/dead-letter handling for stuck jobs, redaction pass, full test suite, live Docker verification, milestone report. Exit: kill a container manually mid-run and confirm the platform notices within one watchdog interval, fails the run cleanly, and the task becomes actionable again rather than stuck in `working` forever.

## 6. Testing strategy

**Backend, domain (pure, no DB):** `agent_run` status-transition tests, `ORCHESTRATOR_ALLOWED` subset tests, cost-ceiling arithmetic, `agent_status.derive_agent_status`'s new run-based inputs — same pattern as the existing `task_status`/`agent_status` unit tests.

**Backend, integration (real Postgres/Redis, existing `tests/integration/` pattern):**
- Scheduler claim/lock semantics under concurrency — two scheduler ticks racing the same job must give it to exactly one, verified with real concurrent tasks, not mocked locking.
- `commands/runs.py` full lifecycle, and the one-active-run-per-task unique index actually rejecting a second concurrent run.
- Internal API authorization, not just authentication: a request with an expired or wrong-run JWT must 401; a request with a *valid* JWT for a different run must not be able to write events onto this one.
- The `can_use_tool` denylist: table-driven against every seeded governance rule, confirming `deny` and (per §4.3's fail-closed decision) `require_approval` both block, while `allow`/`advisory` pass through.
- The launcher, against a **real `docker-socket-proxy` in the dev compose stack** — create a container, confirm it genuinely can't reach Postgres/Redis (a connect-refused check from inside it, not just a config assertion), stop it, confirm cleanup. Same "real infra, not a shortcut" bar Phase 1 M3's cross-process WebSocket test held itself to.

**Adapter:** tested against a **mocked/recorded SDK session** for the normal suite — no real Anthropic calls in CI. A small number of tests are separately marked (skipped by default, opt-in via an env var) that make one real, cheap call, mirroring how `pytest.mark.integration` already gates on real Postgres/Redis being available.

**Frontend:** `format.ts` run-status mapping (mirrors the governance-badge test from Phase 1.5), `queryKeysToInvalidate` extended for run events (same pattern), run-console component rendering across every status, Cancel button wiring.

**Live verification** (same bar as every prior phase): rebuild all images including the new runner image, fresh `docker compose up`, assign a real task end-to-end with real, small Anthropic spend, watch it live in two browser tabs, cancel a run mid-flight and confirm the container is actually gone (`docker ps`, not just the DB status), and confirm a `deny`-rule tool call is genuinely blocked by trying to get the agent to do something a seeded rule forbids.

## 7. Open decisions needing your input before I code

**7.1 Repo hosting and git credentials.** Which repository will the developer agent work on first, hosted where (GitHub, GitLab, self-hosted), and how should push credentials be provisioned — a fine-grained PAT or deploy key you create and hand me the reference for, or something you'd like help setting up? This is the one item in this plan I can't make a reasonable default choice for.

**7.2 `require_approval` governance rules in Phase 2**, given no approval mechanism exists yet:
- **Fail closed (my recommendation):** blocked, same as `deny`, until Phase 3's gateway can actually route these to a human.
- Temporarily treat as `allow` with a loud warning event — not recommended, violates Guiding Principle 4 (enforcement at the tool boundary, not just visibility after the fact).
- Skip enforcing `require_approval` rules entirely this phase (only `deny` rules are checked) — simpler to build, but leaves a real gap while a real agent runs with real tool access.

**7.3 Run console UI location:** inline in the existing task detail page (my recommendation — Phase 2 has exactly one run per task, so a dedicated route adds nav complexity for no present benefit) vs. a new top-level `/runs/[id]` route now, matching ARCHITECTURE.md's original folder structure and more directly reusable once Phase 4 gives a task multiple runs.

**7.4 Cost ceiling scope:** a single global `ACC_RUN_MAX_COST_USD` setting (my recommendation — a real safety net without the schema/UI weight of the full budget system) vs. building `tasks.budget_usd`/`spent_usd` and the per-task override now, as ARCHITECTURE.md's full DDL already designs. I'd rather ship the global ceiling in M3 and treat per-task budgets as a fast-follow once there's usage data to know what limits actually make sense.

**7.5 Egress control on `runner-egress`:** DNS/firewall-based host allowlisting for Phase 2 (my recommendation — ships now, covers the real risk) vs. a proper egress-filtering forward proxy immediately (more correct, meaningfully more infra work, reasonable as a fast-follow rather than a Phase 2 blocker).

Waiting for your decisions on §7 (and any other changes you want) before writing any code.
