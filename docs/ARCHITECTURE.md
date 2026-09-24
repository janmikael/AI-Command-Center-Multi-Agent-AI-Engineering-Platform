# AI Command Center: System Architecture

Status: Draft v1 (design only, no implementation yet)
Owner: Michael
Date: 2026-09-24

A private, single-operator control plane that runs, monitors and governs a team of AI agents (architect, developer, designer, and more later). It works on real code repositories and uses MCP tools.

---

## 0. Guiding principles

These eight rules drive every other decision in this document.

1. **The platform owns the state; agents are stateless workers.** Tasks, status, history and approvals live in Postgres and change only through the orchestrator. An agent never decides that a task is "completed". It submits a result, and the state machine decides.
2. **Hub and spoke, never agent to agent.** Agents never talk to each other directly. Every handoff goes through the orchestrator as a task artifact. This makes every interaction auditable, replayable and interruptible.
3. **Agents propose, the platform executes.** Privileged actions (merge, deploy, DB migration) are not tools that agents hold. Agents can only *request* them. After human approval, a platform executor carries them out with credentials that agents never see.
4. **Enforce at the tool boundary, not in the prompt.** "Please ask before running migrations" in a system prompt is not a control. Policies are enforced in the MCP gateway, in the Claude Agent SDK permission callback, and by sandbox isolation.
5. **Agents are plugins.** Agents are data rows plus an adapter class. Tasks are routed by *capability* (`plan`, `implement`, `review_code`, `design_ui`), not by agent name. Adding an agent should need zero schema changes.
6. **Everything is an event.** One append-only event stream feeds the dashboard, the audit log, usage accounting and replay.
7. **Derive metrics, don't trust self-reports.** Progress percentage comes from the subtask and checklist structure. An LLM saying "80% done" is only a hint.
8. **Governance is global; context is per-project.** Behavior rules — what the AI may or may not do — are authored once and apply identically to every project. Only knowledge — what the AI knows about a given codebase — varies by project. A project can never relax a global rule; there is no per-project security exception. See §10.

---

## 1. System architecture

### 1.1 Component diagram

```
                         ┌───────────────────────────────────┐
  Operator ── HTTPS ───▶ │  web  (Next.js + Tailwind)        │
  (behind Tailscale/VPN) └──────┬──────────────────▲─────────┘
                           REST │                  │ WebSocket
                         ┌──────▼──────────────────┴─────────┐
                         │  api  (FastAPI)                    │
                         │  auth · tasks · agents · approvals │
                         │  WS hub  (subscribes to Redis)     │
                         └──┬─────────────▲──────────────┬───┘
                            │ SQL         │ events       │ SQL
                     ┌──────▼─────┐  ┌────┴─────┐        │
                     │ PostgreSQL │  │  Redis   │        │
                     │ source of  │  │ pub/sub, │        │
                     │ truth, job │  │ streams, │        │
                     │ queue      │  │ locks    │        │
                     └──────▲─────┘  └────▲─────┘        │
                            │             │              │
                         ┌──┴─────────────┴──────────────▼───┐
                         │  orchestrator (worker process)    │
                         │  state machine · router ·         │
                         │  scheduler · budgets · watchdog   │
                         │  privileged executors (merge,     │
                         │  deploy, migrate)                 │
                         └──────────────┬────────────────────┘
                                        │ launches one per run
                 ┌──────────────────────▼──────────────────────────┐
                 │  agent-runner  (ephemeral sandbox container)     │
                 │  ┌───────────────────────────────────────────┐   │
                 │  │ Adapter: openai │ claude_code │ claude_api │   │
                 │  └───────────────────┬───────────────────────┘   │
                 │  workspace: git worktree of the target repo      │
                 └──────────────────────┼───────────────────────────┘
                                        │ MCP (streamable HTTP, per-run token)
                         ┌──────────────▼────────────────────┐
                         │  mcp-gateway                       │
                         │  authn · policy engine · approval  │
                         │  holds · redaction · audit events  │
                         └─┬──────┬──────┬──────┬──────┬─────┬┘
                           │      │      │      │      │     │
                          fs   terminal git    db  browser  acc-control
                                                 (Playwright) (platform tools)
```

### 1.2 Services

| Service | Tech | Responsibility | Scales by |
|---|---|---|---|
| `web` | Next.js (App Router), Tailwind, shadcn/ui, TanStack Query | Dashboard UI, calls the typed REST client, holds the WS connection | stateless |
| `api` | FastAPI, SQLAlchemy 2 (async), Pydantic v2 | REST, WebSocket fan-out, auth, validation. **No long-running work.** | stateless, horizontal |
| `orchestrator` | Python asyncio process (same codebase as the API) | Task state machine, routing, dispatching runs, budgets, timeouts, privileged executors | singleton leader (Redis lock) plus N workers |
| `agent-runner` | Ephemeral Docker container per run | Hosts one adapter session against one workspace | one per active run |
| `mcp-gateway` | Python, official MCP SDK | Single MCP endpoint for all agents. Proxies to tool servers after policy check, audit and redaction | stateless |
| MCP tool servers | Reference/community servers plus `acc-control` (ours) | filesystem, terminal, git, postgres, playwright, platform control | per run or shared |
| `postgres` | PostgreSQL 16 | System of record and durable job queue (`SELECT … FOR UPDATE SKIP LOCKED`) | vertical, then read replicas |
| `redis` | Redis 7 | Event fan-out to WS, ephemeral presence/heartbeats, leader lock | single |
| `proxy` | Caddy | TLS, routing `/` to web and `/api`, `/ws` to api | n/a |

### 1.3 Key architecture decisions (ADR summary)

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| ADR-1 | API and orchestrator are **separate processes** from one Python codebase | Agent runs last minutes to hours. They must survive API restarts and must never block request handlers. | Background tasks inside FastAPI (lost on restart, no concurrency control) |
| ADR-2 | **Postgres as the durable job queue** (transactional outbox), Redis only for fan-out | Status change and job enqueue commit in *one transaction*, so a "task says working but no job exists" state can't happen. One fewer source of truth. | Celery/RabbitMQ (another stateful system; its state can drift from the DB) |
| ADR-3 | **An MCP gateway** in front of every tool server | One place for authorization, approval holds, secret redaction and audit, whichever provider the agent uses | Each agent connects to tool servers directly (no central enforcement or visibility) |
| ADR-4 | **Adapter pattern per agent runtime** | OpenAI, the Claude Agent SDK and the raw Claude API have very different loop semantics. Adapters normalize them into one event and result contract. | Hard-coding three agents |
| ADR-5 | **Capability-based routing** | New agents (QA, DevOps, Security) plug in by declaring capabilities | Routing by agent name |
| ADR-6 | **Git worktree per task, one sandbox container per run** | Parallel agents can't clobber each other. Blast radius is limited to a disposable workspace. | Shared checkout on the host |
| ADR-7 | **Privileged actions run as platform executors** | Merge, deploy and migrate credentials never enter an agent's context, so prompt injection can't use them | "Dangerous" tools that agents hold behind a prompt rule |
| ADR-8 | **Append-only `events` table, partitioned monthly**, plus typed projection tables | Complete audit and replay, while keeping queries fast for the dashboard | Only mutable status rows (history lost) |
| ADR-9 | **Docker Compose first**, with a Kubernetes-ready design (stateless services, runner as a job) | Right-sized for a single operator on one host, with a clear path to scale | Kubernetes on day one (operational overhead with no benefit yet) |

### 1.4 How each "agent" is actually implemented

Validate this section before building. Some of the product names in the brief don't map 1:1 onto programmable APIs.

| Agent | Runtime | Notes |
|---|---|---|
| **Architect / PM / Reviewer** ("ChatGPT agent") | **OpenAI API** (Responses API with function calling / MCP tools). Model is configurable. | The ChatGPT *app* can't be driven programmatically. This agent uses the OpenAI API, is **billed separately** from a ChatGPT subscription, and needs an API key. |
| **Developer** ("Claude Code agent") | **Claude Agent SDK (Python)**, which runs Claude Code's agent loop headlessly inside the runner container | The SDK gives streaming messages, session resume, MCP server configuration, hooks (`PreToolUse`/`PostToolUse`), a `can_use_tool` permission callback and per-run cost/usage in the result message. Use an **Anthropic API key** for automated use. Confirm that the auth method you plan to use is permitted for this kind of automation. |
| **Designer** ("Claude Design agent") | **Claude API (Messages)** with a design-focused system prompt and a restricted toolset (read repo, read screenshots via the browser MCP, write design specs and components to a `design/` path) | There is no separate "Claude Design" API assumed here. "Designer" is a role: a model plus a prompt, tools and permissions. It can later be upgraded to run on the Agent SDK with write access limited to UI paths. |

Because of ADR-4, swapping any of these (for example, using Claude as the architect too) is a configuration change.

---

## 2. Database schema

PostgreSQL 16. All IDs are UUIDv7 (time-sortable). All timestamps are `timestamptz`. Money is stored as `numeric(12,6)` USD. Migrations use Alembic.

### 2.1 Entity overview

```
users ─┐
       ├─< approvals >── tasks ──< tasks (parent/subtasks)
projects ─< repositories        │ ──< task_dependencies
       │                        │ ──< artifacts
       └──────────< tasks       │ ──< agent_runs >── agents >── agent_capabilities
                                         │
                                         ├──< events (partitioned)   ← everything
                                         ├──< tool_calls
                                         ├──< file_changes
                                         └──< usage_records
policies  (tool / action rules)     workflows (templates of stages)
jobs      (durable queue)           audit_log (human actions)
```

### 2.2 Tables (DDL sketch)

```sql
-- ─── Identity ───────────────────────────────────────────────
CREATE TABLE users (
  id            uuid PRIMARY KEY,
  email         citext UNIQUE NOT NULL,
  display_name  text NOT NULL,
  role          text NOT NULL DEFAULT 'operator',   -- operator | viewer | admin
  created_at    timestamptz NOT NULL DEFAULT now()
);
-- Auth credentials (passkeys / OIDC subject) live in a separate auth table or an external IdP.

-- ─── Projects & repos ───────────────────────────────────────
CREATE TABLE projects (
  id            uuid PRIMARY KEY,
  slug          text UNIQUE NOT NULL,
  name          text NOT NULL,
  description   text,
  settings      jsonb NOT NULL DEFAULT '{}',     -- default workflow, budgets
  created_at    timestamptz NOT NULL DEFAULT now(),
  archived_at   timestamptz
);

CREATE TABLE repositories (
  id              uuid PRIMARY KEY,
  project_id      uuid NOT NULL REFERENCES projects(id),
  name            text NOT NULL,
  remote_url      text NOT NULL,
  default_branch  text NOT NULL DEFAULT 'main',
  protected_branches text[] NOT NULL DEFAULT '{main}',
  UNIQUE (project_id, name)
);

-- ─── Agents (registry, data-driven) ─────────────────────────
CREATE TABLE agents (
  id            uuid PRIMARY KEY,
  slug          text UNIQUE NOT NULL,            -- 'architect', 'developer', 'designer'
  display_name  text NOT NULL,
  role          text NOT NULL,                   -- human-readable role label
  adapter       text NOT NULL,                   -- 'openai_responses' | 'claude_agent_sdk' | 'claude_messages'
  model         text NOT NULL,                   -- provider model id (configurable)
  system_prompt_ref text NOT NULL,               -- path/version of prompt template
  config        jsonb NOT NULL DEFAULT '{}',     -- temperature, max_turns, tool allowlist, etc.
  max_concurrent_runs int NOT NULL DEFAULT 1,
  enabled       boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agent_capabilities (
  agent_id      uuid REFERENCES agents(id) ON DELETE CASCADE,
  capability    text NOT NULL,                   -- plan | review_code | implement | test | design_ui | review_ui ...
  priority      int NOT NULL DEFAULT 100,        -- lower wins when multiple agents match
  PRIMARY KEY (agent_id, capability)
);
-- Live status (idle/working/waiting_approval/error/offline) is DERIVED from
-- agent_runs + heartbeats in Redis. It is not stored as a mutable column.

-- ─── Workflows ──────────────────────────────────────────────
CREATE TABLE workflows (
  id            uuid PRIMARY KEY,
  slug          text UNIQUE NOT NULL,            -- 'feature', 'bugfix', 'ui_feature'
  version       int NOT NULL,
  definition    jsonb NOT NULL                   -- ordered stages → capability, gates, loops
);

-- ─── Tasks ──────────────────────────────────────────────────
CREATE TYPE task_status AS ENUM (
  'backlog', 'planning', 'working', 'review',
  'awaiting_approval', 'blocked', 'completed', 'failed', 'cancelled'
);

CREATE TABLE tasks (
  id              uuid PRIMARY KEY,
  project_id      uuid NOT NULL REFERENCES projects(id),
  repository_id   uuid REFERENCES repositories(id),
  parent_id       uuid REFERENCES tasks(id),     -- subtasks created by the architect
  workflow_id     uuid REFERENCES workflows(id),
  key             text UNIQUE NOT NULL,          -- 'ACC-142', human-friendly
  title           text NOT NULL,
  description     text NOT NULL DEFAULT '',
  acceptance_criteria jsonb NOT NULL DEFAULT '[]', -- [{id, text, done}]
  status          task_status NOT NULL DEFAULT 'backlog',
  stage           text,                          -- current workflow stage name
  priority        smallint NOT NULL DEFAULT 3,   -- 1 (urgent) .. 5
  assigned_agent_id uuid REFERENCES agents(id),  -- null = route by capability
  required_capability text,
  branch_name     text,
  progress_pct    smallint NOT NULL DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100), -- derived
  budget_usd      numeric(12,6),                 -- hard cap for this task (inherits project default)
  spent_usd       numeric(12,6) NOT NULL DEFAULT 0,
  attempt         int NOT NULL DEFAULT 0,
  max_attempts    int NOT NULL DEFAULT 3,
  failure_reason  text,
  created_by      uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  completed_at    timestamptz,
  version         int NOT NULL DEFAULT 0         -- optimistic locking
);
CREATE INDEX ON tasks (project_id, status);
CREATE INDEX ON tasks (parent_id);

CREATE TABLE task_dependencies (
  task_id        uuid REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on_id  uuid REFERENCES tasks(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, depends_on_id)
);

-- Outputs that get handed from one agent to the next
CREATE TABLE artifacts (
  id            uuid PRIMARY KEY,
  task_id       uuid NOT NULL REFERENCES tasks(id),
  run_id        uuid,                            -- producing run
  kind          text NOT NULL,                   -- plan | design_spec | diff | review | test_report | screenshot | adr
  title         text NOT NULL,
  content       jsonb,                           -- structured (e.g. review verdict + comments)
  blob_uri      text,                            -- large/binary content (local volume / S3)
  version       int NOT NULL DEFAULT 1,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ─── Runs (one agent working one task stage) ───────────────
CREATE TYPE run_status AS ENUM ('queued','starting','running','waiting_approval','succeeded','failed','cancelled','timed_out');

CREATE TABLE agent_runs (
  id              uuid PRIMARY KEY,
  task_id         uuid NOT NULL REFERENCES tasks(id),
  agent_id        uuid NOT NULL REFERENCES agents(id),
  stage           text NOT NULL,
  status          run_status NOT NULL DEFAULT 'queued',
  provider_session_id text,                      -- for resume (Agent SDK session / OpenAI response id)
  container_id    text,
  workspace_ref   text,                          -- worktree path / commit base
  current_step    text,                          -- short live description ("Running tests")
  started_at      timestamptz,
  ended_at        timestamptz,
  last_heartbeat_at timestamptz,
  result_summary  text,
  error           jsonb,
  input_tokens    bigint NOT NULL DEFAULT 0,
  output_tokens   bigint NOT NULL DEFAULT 0,
  cache_read_tokens bigint NOT NULL DEFAULT 0,
  cost_usd        numeric(12,6) NOT NULL DEFAULT 0
);
CREATE INDEX ON agent_runs (agent_id, status);
CREATE INDEX ON agent_runs (task_id);

-- ─── Event stream (append-only, partitioned) ────────────────
CREATE TABLE events (
  id          uuid NOT NULL,
  ts          timestamptz NOT NULL DEFAULT now(),
  project_id  uuid,
  task_id     uuid,
  run_id      uuid,
  agent_id    uuid,
  actor       text NOT NULL,                     -- 'agent:developer' | 'user:<id>' | 'system'
  type        text NOT NULL,                     -- see event catalogue §3.4
  severity    text NOT NULL DEFAULT 'info',      -- debug | info | warn | error
  payload     jsonb NOT NULL,
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
CREATE INDEX ON events (task_id, ts);
CREATE INDEX ON events (run_id, ts);
CREATE INDEX ON events (type, ts);

-- ─── Typed projections for fast dashboard queries ──────────
CREATE TABLE tool_calls (
  id            uuid PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES agent_runs(id),
  server        text NOT NULL,                   -- fs | terminal | git | db | browser | acc
  tool          text NOT NULL,
  arguments     jsonb NOT NULL,                  -- REDACTED
  policy_decision text NOT NULL,                 -- allow | deny | require_approval
  approval_id   uuid,
  status        text NOT NULL,                   -- pending | ok | error | denied
  result_excerpt text,                           -- truncated + redacted
  exit_code     int,                             -- terminal only
  duration_ms   int,
  started_at    timestamptz NOT NULL,
  ended_at      timestamptz
);
CREATE INDEX ON tool_calls (run_id, started_at);

CREATE TABLE file_changes (
  id            uuid PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES agent_runs(id),
  task_id       uuid NOT NULL REFERENCES tasks(id),
  path          text NOT NULL,
  change_type   text NOT NULL,                   -- added | modified | deleted | renamed
  additions     int, deletions int,
  commit_sha    text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usage_records (
  id            uuid PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES agent_runs(id),
  agent_id      uuid NOT NULL REFERENCES agents(id),
  provider      text NOT NULL,                   -- openai | anthropic
  model         text NOT NULL,
  input_tokens  bigint, output_tokens bigint, cache_read_tokens bigint, cache_write_tokens bigint,
  cost_usd      numeric(12,6),
  cost_source   text NOT NULL,                   -- 'provider_reported' | 'estimated'
  recorded_at   timestamptz NOT NULL DEFAULT now()
);

-- ─── Approvals ──────────────────────────────────────────────
CREATE TYPE approval_status AS ENUM ('pending','approved','rejected','expired','cancelled');

CREATE TABLE approvals (
  id            uuid PRIMARY KEY,
  task_id       uuid REFERENCES tasks(id),
  run_id        uuid REFERENCES agent_runs(id),
  action_type   text NOT NULL,                   -- merge | deploy | db_migration | db_write | shell_command | ...
  risk_level    text NOT NULL,                   -- low | medium | high | critical
  title         text NOT NULL,
  summary       text NOT NULL,                   -- agent's justification
  payload       jsonb NOT NULL,                  -- exactly what will execute (SHA, SQL, command, env)
  payload_hash  text NOT NULL,                   -- executor refuses if payload changed after approval
  status        approval_status NOT NULL DEFAULT 'pending',
  requested_by  text NOT NULL,                   -- actor string
  decided_by    uuid REFERENCES users(id),
  decision_note text,
  expires_at    timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz,
  executed_at   timestamptz,
  execution_result jsonb
);
CREATE INDEX ON approvals (status, created_at);

-- ─── Policies (tool / action governance) ───────────────────
CREATE TABLE policies (
  id            uuid PRIMARY KEY,
  scope         text NOT NULL,                   -- global | project:<id> | agent:<id>
  server        text NOT NULL,                   -- fs | terminal | git | db | browser | *
  tool          text NOT NULL,                   -- tool name or *
  match         jsonb NOT NULL DEFAULT '{}',     -- e.g. {"command_regex": "^(rm|curl|docker)"} / {"path_glob": "migrations/**"}
  effect        text NOT NULL,                   -- allow | deny | require_approval
  risk_level    text NOT NULL DEFAULT 'medium',
  priority      int NOT NULL DEFAULT 100,
  enabled       boolean NOT NULL DEFAULT true
);

-- ─── Durable job queue (outbox) ────────────────────────────
CREATE TABLE jobs (
  id            uuid PRIMARY KEY,
  kind          text NOT NULL,                   -- start_run | advance_task | execute_approval | timeout_check
  payload       jsonb NOT NULL,
  run_after     timestamptz NOT NULL DEFAULT now(),
  attempts      int NOT NULL DEFAULT 0,
  locked_by     text, locked_at timestamptz,
  status        text NOT NULL DEFAULT 'pending', -- pending | running | done | dead
  last_error    text
);
CREATE INDEX ON jobs (status, run_after);

-- ─── Human audit log ───────────────────────────────────────
CREATE TABLE audit_log (
  id bigserial PRIMARY KEY,
  user_id uuid REFERENCES users(id),
  action text NOT NULL, target text NOT NULL, details jsonb,
  ip inet, ts timestamptz NOT NULL DEFAULT now()
);
```

### 2.3 Schema notes

- **`progress_pct` is derived by the orchestrator** as `completed leaf subtasks / total leaf subtasks`, blended with acceptance-criteria checkboxes and the workflow stage weight. Agents can report a `current_step` string, which is display-only.
- **Agent status is derived:** `working` if it has a run in `running`, `waiting_approval` if a run is paused, `error` if its last run failed within N minutes, `offline` if the adapter's health check is failing, otherwise `idle`.
- **Retention:** Detach and archive `events` partitions older than 90 days (configurable). `tool_calls.result_excerpt` is capped at 8 KB. Full outputs go to blob storage.
- **Global governance and multi-project context** (rule authoring, per-project knowledge base, project-level status and artifacts) are deferred to Phase 1.5 — see §10.

---

## 3. API design

Base path is `/api/v1`. JSON throughout. The OpenAPI schema is the contract, and the TypeScript client for `web` is generated from it (`openapi-typescript`).

### 3.1 Conventions

- Auth: session cookie (HttpOnly, SameSite=Strict) after passkey/OIDC login. Machine-to-machine calls (runner to gateway) use short-lived, per-run signed tokens (JWT with `run_id`, `agent_id` and scopes).
- Pagination: cursor-based (`?cursor=…&limit=50`). Responses have the shape `{ items, next_cursor }`.
- Errors: RFC 9457 `application/problem+json`.
- Concurrency: task mutations take an `If-Match: <version>` header, and a mismatch returns 409.
- Idempotency: every `POST` accepts an `Idempotency-Key` header.

### 3.2 REST endpoints (operator-facing)

```
Auth
  POST   /auth/login            GET /auth/me           POST /auth/logout

Projects & repos
  GET    /projects              POST /projects
  GET    /projects/{id}         PATCH /projects/{id}
  GET    /projects/{id}/repositories   POST /projects/{id}/repositories

Agents
  GET    /agents                                  → list with derived status, current task, last activity, usage today
  GET    /agents/{id}                             → detail + capabilities + recent runs
  POST   /agents                                  → register new agent (admin)
  PATCH  /agents/{id}                             → model, prompt ref, config, enabled
  POST   /agents/{id}/pause | /resume             → stop accepting new runs
  GET    /agents/{id}/usage?from&to&group_by=day

Tasks
  GET    /tasks?project_id&status&agent_id&q      → board/list
  POST   /tasks                                   → create (status=backlog)
  GET    /tasks/{id}                              → detail incl. subtasks, artifacts, runs, approvals
  PATCH  /tasks/{id}                              → title, description, priority, criteria, assignment
  POST   /tasks/{id}/start                        → backlog → planning (enqueues workflow)
  POST   /tasks/{id}/assign      {agent_id|capability}
  POST   /tasks/{id}/cancel
  POST   /tasks/{id}/retry                        → failed → re-enter at failed stage
  POST   /tasks/{id}/comments    {body}           → operator guidance, injected into next run context
  GET    /tasks/{id}/artifacts   GET /artifacts/{id}
  GET    /tasks/{id}/diff                         → aggregated branch diff vs base

Runs
  GET    /runs?task_id&agent_id&status
  GET    /runs/{id}                               → detail, usage
  GET    /runs/{id}/events?cursor                 → timeline (logs, tool calls, decisions)
  GET    /runs/{id}/tool-calls   GET /runs/{id}/file-changes
  POST   /runs/{id}/cancel                        → kill switch for one run
  POST   /runs/{id}/message      {body}           → operator steers a live run (if adapter supports it)

Approvals
  GET    /approvals?status=pending
  GET    /approvals/{id}                          → payload, diff/SQL preview, risk, context links
  POST   /approvals/{id}/approve  {note, payload_hash}   ← hash must match what the operator saw
  POST   /approvals/{id}/reject   {note}

Activity & metrics
  GET    /events?project_id&task_id&type&severity&since   → global activity feed
  GET    /metrics/overview                        → active runs, spend today, tasks by status, error rate

Policies & workflows (admin)
  GET/POST/PATCH/DELETE  /policies
  GET/POST               /workflows

System
  POST   /system/emergency-stop                   → cancel all runs, pause all agents
  GET    /healthz   GET /readyz
```

### 3.3 WebSocket

`GET /ws` authenticates with the session cookie. There is one socket per browser tab, with topic subscriptions multiplexed over it.

```jsonc
// client → server
{ "op": "subscribe",   "topics": ["agents", "approvals", "task:ACC-142", "run:0192…"] }
{ "op": "unsubscribe", "topics": ["run:0192…"] }
{ "op": "ping" }

// server → client (every message)
{ "topic": "run:0192…", "seq": 18422, "type": "tool_call.completed",
  "ts": "2026-09-24T10:12:01Z", "data": { … } }
```

- `seq` is monotonic per topic. On reconnect, the client sends `{"op":"resume","topic":…,"after_seq":…}`. The server replays from the `events` table, or tells the client to refetch over REST if the gap is too large.
- The server pushes **deltas only**. Initial state always comes from REST, so the UI is correct even if the WebSocket drops.
- Fan-out path: a writer commits the event to Postgres, then publishes to Redis channel `acc:events:<topic>`. Every API instance subscribes and forwards to its connected sockets.

### 3.4 Event catalogue (`events.type`)

```
task.created | task.status_changed | task.assigned | task.progress | task.comment
run.queued | run.started | run.step | run.heartbeat | run.completed | run.failed | run.cancelled
agent.message            (agent narrative / log line)
agent.decision           (structured: {decision, rationale, alternatives})
tool_call.started | tool_call.completed | tool_call.denied
file.changed             command.executed          git.commit | git.push
approval.requested | approval.decided | approval.executed | approval.expired
usage.recorded           budget.warning | budget.exceeded
error                    (with class, message, run/task refs)
```

### 3.5 Internal APIs (not exposed publicly)

| From → To | Channel | Purpose |
|---|---|---|
| runner → gateway | MCP over streamable HTTP, per-run JWT | All tool use |
| runner → orchestrator | `POST /internal/runs/{id}/events` (batch) or a Redis stream | Adapter-normalized events, heartbeats, usage |
| gateway → orchestrator | internal HTTP | Create approval holds, report tool calls |
| orchestrator → runner | Docker API via **docker-socket-proxy** (create/stop only) | Launch or kill sandboxes |

---

## 4. MCP integration design

### 4.1 Topology: the gateway pattern

Every agent sees **exactly one MCP server**: `mcp-gateway`. The gateway exposes a *filtered, namespaced* union of tools from the downstream servers, based on the agent's capabilities and policies.

```
Agent (any provider) ──MCP──▶ mcp-gateway ──MCP──▶ fs server        (scoped to /workspace)
                                          ├──MCP──▶ terminal server  (inside run sandbox)
                                          ├──MCP──▶ git server       (worktree only, no push to protected)
                                          ├──MCP──▶ postgres server  (dev DB, read-only role by default)
                                          ├──MCP──▶ playwright server(browser testing, headless)
                                          └──in-proc▶ acc-control    (platform tools, ours)
```

The gateway handles each `tools/call` in this order:

1. **Authenticate** the per-run token. This gives `run_id`, `agent_id`, `task_id` and scopes.
2. **Tool visibility:** `tools/list` returns only the tools the agent's capabilities allow. For example, the architect gets `fs.read_*`, `git.diff`, `git.log` and `acc.*`, but no `terminal` or `fs.write`.
3. **Policy evaluation** against the `policies` table, most specific first, returns `allow`, `deny` or `require_approval`.
4. **Approval hold:** If approval is required, the gateway creates an `approvals` row and emits `approval.requested`. It then **awaits the decision** until `expires_at`. Approve forwards the call. Reject or expiry returns a structured tool error ("Denied by operator: <note>") so the agent can adapt.
5. **Argument redaction and secret scanning**, both before logging and on the results.
6. **Forward** the call. Record `tool_calls` and emit events (`command.executed`, `file.changed`).
7. **Rate and cost guards:** per-run tool-call ceilings and loop detection (for example, the same failing command 5 times means escalate).

### 4.2 Tool servers

| Server | Implementation | Sandbox and limits |
|---|---|---|
| **filesystem** | Reference MCP filesystem server | Root is the run's worktree only. Denies `.env*`, `**/secrets/**` and `.git/` internals. |
| **terminal** | Small custom MCP server (run command, stream output, timeout) | Runs **inside the run's container** as a non-root user, with CPU/memory/time limits, egress allowlist (package registries, git remote) and no Docker socket. |
| **git** | Reference MCP git server, plus a wrapper | Operates on the worktree. Can commit and push **only to `acc/<task-key>-*` branches**. Merging into protected branches is not available as a tool. |
| **database** | Postgres MCP server | Connects to a **dev/ephemeral** DB with a read-only role. Writes and DDL go through `acc.request_approval(db_write)`. Never gets production credentials. |
| **browser** | Playwright MCP server | Headless, in a separate container. Hits only the preview URL of the task's branch. Screenshots are stored as artifacts. |
| **acc-control** | Ours (in-process in the gateway) | See below |

### 4.3 `acc-control`: how agents talk to the command center

This is the uniform "agent to platform" protocol that works with every provider:

| Tool | Used by | Effect |
|---|---|---|
| `acc.get_task_context()` | all | Task, acceptance criteria, operator comments, prior artifacts (plan, design spec, review feedback) |
| `acc.report_step(text)` | all | Updates `current_step` (display only) |
| `acc.log_decision(decision, rationale, alternatives[])` | all | `agent.decision` event, shown in the Decisions panel |
| `acc.create_subtasks([{title, description, capability, criteria, depends_on}])` | architect | Creates the subtask tree (validated, capped count) |
| `acc.submit_artifact(kind, title, content)` | all | Plan, design spec, review, test report |
| `acc.submit_review(verdict: approve\|changes_requested, comments[])` | reviewers | Drives the review loop |
| `acc.complete(summary, criteria_status[])` | all | Signals "stage done". The orchestrator validates it (for example, tests passed and a diff exists). |
| `acc.ask_human(question, options?)` | all | Pauses the run, creates a question card in the UI, resumes with the answer |
| `acc.request_merge()` / `acc.request_deploy(env)` / `acc.request_migration(file)` | developer | Creates an **approval** executed by the platform (§5.4) |
| `acc.request_tool_access(tool, reason, estimated_cost?, permissions?)` | all | Creates a `tool_access` approval (§10.3) when a tool isn't already allowlisted. From Phase 1.5, the gateway also raises this automatically for any unlisted tool call, without the agent needing to call it explicitly. |

From Phase 1.5, every `tools/call` above is checked against policies compiled from the Global AI Rules, not just hand-seeded ones — see §10.3.

### 4.4 Provider wiring

- **Claude Agent SDK (developer):**
  - Configure `mcp_servers` with only the gateway URL and the per-run token.
  - Set `allowed_tools` to the gateway tools. Disable or restrict built-in `Bash`/`Write`/`Edit`, **or** keep the built-ins (they work better for coding) and put them under the same policy engine through the SDK's `can_use_tool` callback and `PreToolUse` hooks, which call the gateway's `/internal/policy/evaluate`. The hybrid is recommended: built-ins for fs and bash inside the sandbox, the gateway for git, db, browser and acc.
  - Set `cwd` to the worktree.
  - Stream SDK messages, normalize them into events, and read cost and usage from the final result message.
- **OpenAI Responses API (architect):** The adapter runs the tool loop itself. It lists the gateway tools, maps them to function tools, executes each call through the gateway's MCP client and feeds the results back. Usage comes from each response's `usage` field.
- **Claude Messages API (designer):** Same adapter-driven loop, using the Claude tool-use protocol with the gateway tools mapped to Claude tool definitions.

### 4.5 Adapter contract (design-level interface)

```python
class AgentAdapter(Protocol):
    name: str                                    # 'claude_agent_sdk'
    supports_resume: bool
    supports_live_steering: bool

    async def start(self, ctx: RunContext) -> AsyncIterator[NormalizedEvent]: ...
    async def resume(self, ctx: RunContext, session_id: str, message: str) -> AsyncIterator[NormalizedEvent]: ...
    async def cancel(self, run_id: UUID) -> None: ...
    async def health(self) -> AdapterHealth: ...

# RunContext: task, stage, prompt (rendered template), workspace path,
#             gateway url + token, budget remaining, max_turns, timeout
# NormalizedEvent: message | decision | tool_call | usage | step | completed | failed
```

To add a new agent: (1) implement or reuse an adapter, (2) insert an `agents` row, (3) add capabilities, (4) add a prompt template. Nothing else changes.

---

## 5. Agent communication workflow

### 5.1 Task state machine

```
             ┌───────── cancel (any non-terminal) ─────────▶ cancelled
             │
 backlog ──start──▶ planning ──plan accepted──▶ working ──complete──▶ review
                      │  ▲                        ▲  │                  │
                      │  └─── needs replan ───────┘  │ ask_human        │ changes_requested
                      │                              ▼                  │ (attempt < max)
                      │                           blocked ◀─────────────┤
                      │                              │ answered         │
                      │                              └──▶ working ◀─────┘
                      │                                                 │ approved by reviewer
                      │                                                 ▼
                      │                                         awaiting_approval ──human approves──▶ completed
                      │                                                 │ reject → working (with note)
                      └── error / budget / attempts exhausted ──▶ failed ◀┘
```

Every transition is (a) validated by a pure `domain.task_state_machine` function, (b) written together with its event and follow-up job in **one transaction**, and (c) broadcast.

### 5.2 Default "feature" workflow (data-driven, in `workflows.definition`)

```yaml
stages:
  - name: plan
    capability: plan                  # → Architect (OpenAI)
    outputs: [plan, subtasks]
    status: planning
  - name: design
    capability: design_ui             # → Designer (Claude API)
    when: "task.labels contains 'ui'"
    outputs: [design_spec]
    status: planning
  - name: implement
    capability: implement             # → Developer (Claude Agent SDK), per subtask, respects depends_on
    status: working
    exit_checks: [tests_pass, diff_nonempty, lint_pass]
  - name: code_review
    capability: review_code           # → Architect
    status: review
    on_changes_requested: goto implement   (max 3 loops, then escalate to human)
  - name: ui_review
    capability: review_ui             # → Designer, uses Playwright screenshots
    when: "task.labels contains 'ui'"
    status: review
  - name: merge
    gate: human_approval              # approval(action=merge) → platform executor
    status: awaiting_approval
```

### 5.3 Sequence: one feature, end to end

```
Operator      Orchestrator        Architect(OpenAI)    Designer(Claude)   Developer(Claude Code)   Gateway/Executor
   │ create+start │                       │                   │                    │                    │
   │─────────────▶│ status=planning       │                   │                    │                    │
   │              │── start_run(plan) ───▶│                   │                    │                    │
   │              │                       │ get_task_context, read repo (fs/git)   │                    │
   │              │                       │ log_decision, create_subtasks, submit_artifact(plan)        │
   │              │◀──── complete ────────│                   │                    │                    │
   │              │── start_run(design) ─────────────────────▶│ design_spec        │                    │
   │              │◀──────────────────────────── complete ────│                    │                    │
   │              │ status=working; for each ready subtask:    │                    │                    │
   │              │── start_run(implement, worktree acc/ACC-142-x) ───────────────▶│                    │
   │              │                       │                   │  edit, run tests, commit, push branch   │
   │              │                       │                   │  (bash "npm i x" → policy: allow)       │
   │ ◀─ approval card (db migration) ─────────────────────────────── request_migration ─▶ hold          │
   │ approve ─────────────────────────────────────────────────────────────────────────────────────────▶│ run on dev DB
   │              │◀────────────────────────────────────── complete (tests ✓) ─────│                    │
   │              │ status=review; start_run(code_review) ─▶│ reads diff, submit_review(changes_requested)
   │              │ loop → implement (attempt 2) → review → approve                                     │
   │              │ status=awaiting_approval; approval(merge, sha=abc123)                               │
   │ ◀─ approval card (diff, tests, reviews, cost) ───────────────────────────────────────────────────── │
   │ approve(hash)────────────────────────────────────────────────────────────────────────────────────▶│ merge abc123 only
   │              │ status=completed; worktree cleanup; summary artifact                                │
```

### 5.4 Approval workflow in detail

There are two mechanisms, and both appear in the same Approvals inbox:

**A. Platform-executed actions** (merge, deploy, production DB migration). This is the strongest mechanism.
1. The agent calls `acc.request_merge()`. The orchestrator snapshots the exact payload: `{repo, source_branch, head_sha, target, diff_stats, test_report_id, review_ids}`. It then computes `payload_hash`.
2. The UI shows the diff, test results, reviewer verdicts, spend and risk level.
3. The operator approves. The API checks that the submitted `payload_hash` matches.
4. An executor job runs **with platform credentials** and re-verifies that the branch head still equals the approved SHA. If the head moved, it aborts and requests a new approval.
5. The result is written to `approvals.execution_result` and emitted as an event.

**B. Inline tool holds** (policy says `require_approval`: risky shell patterns, DB writes on dev, touching CI config).
- The gateway (or the SDK `can_use_tool` callback) blocks the call until a decision or expiry. Default expiry is 30 minutes, after which the call is denied.
- If the hold would exceed the run's timeout, the run is **suspended** (session id persisted) and resumed after the decision. This needs `supports_resume`.

Default risk policy (seed data):

| Action | Effect |
|---|---|
| Read anything in the worktree, run tests/linters/builds | allow |
| Install packages from the registry allowlist | allow (logged) |
| `rm -rf` outside the worktree, `curl \| sh`, `sudo`, `docker`, network to non-allowlisted hosts | deny |
| Edit `.github/workflows/**`, `Dockerfile*`, `infra/**`, `**/migrations/**` | require_approval |
| DB DML/DDL on dev DB | require_approval |
| Merge / deploy / prod migration | platform-executed + approval (always) |
| Tool or service not yet on the allowlist (new paid tool, new third-party API, unknown external tool) | require_approval — governance-derived default, see §10.3 |

From Phase 1.5 this table's defaults are generated from the Global AI Rules rather than hand-maintained — see §10.3, §10.4.

### 5.5 Failure handling

- **Heartbeats:** A runner heartbeats every 15 s. If it misses 3, the watchdog marks the run `failed` (worker lost) and the task is retried if `attempt < max_attempts`.
- **Budgets:** These are checked on every `usage.recorded` event. At 80% a `budget.warning` is emitted. At 100% the run is cancelled and the task goes to `blocked` with the reason "budget exceeded". The operator can raise the cap.
- **Loop detection:** Repeated identical failing tool calls, or more than N review cycles, escalate to `blocked` with a question for the human.
- **Emergency stop:** A single button cancels all runs, pauses all agents and expires pending holds.

---

## 6. Folder structure

A monorepo. One Python package serves several processes, to avoid duplicating domain logic.

```
ai-command-center/
├── apps/
│   └── web/                              # Next.js (App Router) + Tailwind + shadcn/ui
│       ├── src/app/
│       │   ├── (auth)/login/
│       │   └── (dashboard)/
│       │       ├── page.tsx              # Overview: agents grid, active runs, spend, pending approvals
│       │       ├── agents/[slug]/
│       │       ├── tasks/                # Kanban board + list
│       │       ├── tasks/[key]/          # Task detail: timeline, artifacts, diff, runs
│       │       ├── runs/[id]/            # Live run console (logs, tool calls, files, decisions)
│       │       ├── approvals/            # Inbox + detail with diff/SQL preview
│       │       ├── activity/             # Global event feed with filters
│       │       └── settings/             # Agents, policies, workflows, budgets
│       ├── src/components/{ui,agents,tasks,runs,approvals,activity}/
│       ├── src/lib/api/                  # generated OpenAPI client + TanStack Query hooks
│       ├── src/lib/ws/                   # socket manager, topic subscriptions, resume
│       └── Dockerfile
│
├── backend/
│   ├── pyproject.toml                    # uv-managed
│   ├── alembic/                          # migrations
│   ├── src/acc/
│   │   ├── config.py                     # pydantic-settings
│   │   ├── domain/                       # PURE: entities, state machines, policy evaluation, progress calc
│   │   ├── db/                           # SQLAlchemy models, repositories, unit-of-work, outbox
│   │   ├── events/                       # event schemas, publisher (PG + Redis), catalogue
│   │   ├── api/
│   │   │   ├── routers/                  # projects, agents, tasks, runs, approvals, events, policies, system
│   │   │   ├── ws.py                     # WebSocket hub
│   │   │   ├── internal/                 # runner/gateway endpoints
│   │   │   └── deps.py                   # auth, db session
│   │   ├── orchestrator/
│   │   │   ├── scheduler.py              # job loop (SKIP LOCKED)
│   │   │   ├── router.py                 # capability → agent selection
│   │   │   ├── workflows.py              # stage engine
│   │   │   ├── budgets.py  watchdog.py
│   │   │   └── executors/                # merge.py, deploy.py, migrate.py (privileged)
│   │   ├── adapters/
│   │   │   ├── base.py                   # AgentAdapter protocol, NormalizedEvent
│   │   │   ├── registry.py
│   │   │   ├── openai_responses.py
│   │   │   ├── claude_agent_sdk.py
│   │   │   └── claude_messages.py
│   │   ├── gateway/
│   │   │   ├── server.py                 # MCP server facing agents
│   │   │   ├── upstreams.py              # MCP clients to tool servers
│   │   │   ├── policy.py  holds.py  redaction.py
│   │   │   └── acc_control/              # platform tools (§4.3)
│   │   ├── runner/                       # entrypoint executed inside sandbox container
│   │   └── entrypoints/                  # api.py, orchestrator.py, gateway.py, runner.py
│   ├── prompts/                          # versioned prompt templates per agent/stage
│   └── tests/{unit,integration,e2e}/
│
├── mcp-servers/
│   └── terminal/                         # custom sandboxed terminal MCP server
│
├── images/
│   ├── runner/Dockerfile                 # python, node, git, claude code CLI, non-root user
│   └── browser/Dockerfile                # playwright MCP
│
├── infra/
│   ├── compose.yml                       # dev
│   ├── compose.prod.yml                  # prod overrides, resource limits, secrets
│   ├── caddy/Caddyfile
│   ├── postgres/init/                    # roles: app, readonly, agent_dev_ro
│   └── seed/                             # default agents, capabilities, policies, workflows
│
├── docs/
│   ├── ARCHITECTURE.md                   # this file
│   └── adr/                              # one file per decision
│
├── .github/workflows/                    # lint, typecheck, test, build images
├── Makefile                              # dev shortcuts (works in WSL2 / Git Bash)
└── .env.example
```

---

## 7. Development phases

Each phase ends with something usable and a demo checklist.

| Phase | Goal | Scope | Exit criteria |
|---|---|---|---|
| **0. Foundations** (1 wk) | Skeleton that deploys | Monorepo, Docker Compose (web, api, postgres, redis, caddy), Alembic baseline, auth (single operator, passkey or password+TOTP), CI, structured logging, `/healthz` | `docker compose up` runs the stack, you can log in, CI is green |
| **1. Data model and dashboard shell** (1–2 wk) | See and manage work | Full schema, Projects/Agents/Tasks CRUD, Kanban board, agent grid, event table plus WebSocket hub with resume, seed agents | Create a task and drag it across statuses. The feed updates live in two tabs. |
| **1.5. Governance & multi-project** (1–2 wk) | Guardrails exist before any agent runs; more than one codebase can be managed | `governance_rules`, `project_context_entries`, project `status`, project-level `artifacts`; seeded default rules for the security/tools/code/infrastructure categories; a governance→policy compiler job; project switcher, project settings page, admin Global Rules page, a context-preview endpoint (see §10) | An operator creates a second project with its own context entries. Previewing any task in either project shows the same Global Rules verbatim in layer 1, and a request to an unlisted tool produces a `tool_access` approval card. |
| **2. First real agent: developer** (2 wk) | One agent does real work, fully observed | Runner image, orchestrator job loop, worktree management, Claude Agent SDK adapter, event normalization, usage and cost capture, run console UI, cancel/kill switch, budgets | Assign "add endpoint X" and watch live logs, commands, file changes and cost. The run ends with a pushed `acc/*` branch. |
| **3. Governance** (2 wk) | Nothing risky happens without you | MCP gateway, policy engine, seed policies, inline holds, platform merge executor with payload hash, approvals inbox and notifications | A migration or merge attempt pauses, appears in the inbox, and the approve or reject path works end to end. Direct push to main is impossible. |
| **4. Multi-agent workflow** (2 wk) | Planner, then builder, then reviewer | OpenAI Responses adapter, `acc-control` tools, workflow engine, subtasks and dependencies, review loop with max iterations, derived progress %, `ask_human` | "Feature" workflow runs: the plan becomes subtasks, they get implemented, the review asks for changes, the fixes pass, and the merge is approved |
| **5. Design agent and browser testing** (1–2 wk) | UI work gets designed and visually verified | Claude Messages adapter, design spec artifacts, Playwright MCP, preview environments per branch, screenshot artifacts, UI review stage | A UI task produces a design spec, implementation and screenshots, and the designer reviews them |
| **6. Hardening** (ongoing) | Production quality | OpenTelemetry tracing, Prometheus/Grafana, backups plus restore drill, secret scanning, event partition rotation, load test with 10 concurrent runs, chaos tests (kill runner mid-run) | Documented SLOs and runbooks. Restore tested. |
| **7. Extensibility** | Grow the team | QA agent, DevOps agent, Security reviewer; per-project workflows; notifications (email/Telegram); optional Kubernetes runner backend | A new agent is added with only config, a prompt and seed rows |

---

## 8. Risks and limitations

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Product ≠ API.** The ChatGPT app and Claude.ai features can't be automated. Only the OpenAI and Anthropic APIs (and the Agent SDK) can. | Separate API billing, and some consumer features are unavailable | Budget for API usage from day one. Keep providers swappable (ADR-4). Check each provider's terms for automated use of your chosen auth method. |
| R2 | **Runaway cost** (loops, huge contexts, parallel runs) | Unexpected bills | Per-task, per-agent and per-day hard caps; max turns; loop detection; emergency stop; spend visible on the overview |
| R3 | **Prompt injection** via repo files, web pages (browser tool), issue text or dependency READMEs | An agent is manipulated into harmful actions | Privileged actions are platform-executed only (ADR-7); sandbox with egress allowlist; no secrets in the agent environment; policies at the tool boundary; human approval on risky paths |
| R4 | **Sandbox escape or host damage** from terminal access | Host compromise | Non-root containers, no Docker socket in runners (the orchestrator uses a restricted socket proxy), read-only root FS, resource limits, network policy. Treat the runner as hostile. |
| R5 | **Secrets leakage** into logs, events or LLM context | Credential exposure | Redaction layer in the gateway and event writer, `.env` denied in fs policy, secret scanning on diffs before merge |
| R6 | **LLM reviewing LLM** gives false confidence | Bugs merged | Reviews are advisory. Hard gates are tests, lint and typecheck in `exit_checks`. The human approves merges. Show a test coverage delta on the approval card. |
| R7 | **Parallel agents on the same code** cause merge conflicts | Wasted runs | Worktree per task, `depends_on` ordering, file-overlap check before scheduling parallel subtasks, rebase step before the merge request |
| R8 | **Long approval waits** stall runs or time out sessions | Lost progress | Suspend and resume with persisted session IDs, and commit work-in-progress to the branch before suspending |
| R9 | **Usage accuracy varies by provider** | Misleading dashboards | Store `cost_source` (reported or estimated) and show estimates as such |
| R10 | **Non-determinism**: the same task gives different results on each run | Hard to debug | Full event log, prompt template versioning, model version recorded per run, artifacts snapshotted |
| R11 | **Provider outages and rate limits** | Stalled pipeline | Adapter health checks, exponential backoff, a `blocked: provider_unavailable` state, optional fallback model per capability |
| R12 | **Single-host deployment** is a SPOF | Downtime | Acceptable for a private tool. Daily PG backups off-host. The stateless design allows moving to multiple hosts later. |
| R13 | **Windows dev host** | Docker, filesystem and perf quirks | Develop inside WSL2 with Docker Desktop. Keep the repo on the WSL filesystem. Deploy target is Linux. |
| R14 | **Scope creep**: seven subsystems | Never ships | Strict phase gates. Phase 2 alone already delivers value. |
| R15 | **Governance rule sprawl or conflicting rules** | Confusing or contradictory enforcement | One global set, not per-project overrides, removes the conflict surface by construction (§10.1). Categorized (security/tools/code/infrastructure), versioned via events, admin-only edit. |
| R16 | **Compiled-policy drift**: a `policies` row generated from a Global AI Rule is hand-edited directly, so enforcement silently diverges from the documented rule | Enforcement no longer matches what the Global Rules page claims | Generated policies carry `source_rule_id` and are read-only in the UI (edit the rule, not the row); a periodic drift-check job re-diffs generated policies against their source rules. |

**Known limitations (by design, v1):**
- A single operator. RBAC tables exist, but multi-user collaboration isn't a v1 goal.
- Agents can't message each other freely. That is deliberate (auditability). Collaboration happens through artifacts and review loops.
- Live steering of a running agent depends on the adapter. The Agent SDK supports it, and the other adapters inject steering at the next turn.
- Deployment executor targets are initially limited to one mechanism (for example, `docker compose` on a target host over SSH). Add others per project.
- Multi-project workspaces and Global AI Governance rules (§10) are deferred to Phase 1.5. Phase 1 manages a single implicit project.

---

## 9. Open questions for the operator

1. Which repositories will agents work on first, and where are they hosted (GitHub, GitLab, local)?
2. Where does "deployment" go (VPS with Docker, a cloud PaaS, Kubernetes)? This defines the deploy executor.
3. What monthly budget ceiling should the defaults use for API spend?
4. Where do you want approval notifications: browser only, or also email or a phone push (Telegram/ntfy)?
5. Where will the command center run: your Windows machine (WSL2) or a small Linux VPS reachable over Tailscale?
6. For the Global Rules page (§10.5): should Global AI Rules be editable through the UI as soon as Phase 1.5 ships, or ship first as seeded, read-only rules with editing added in a later phase?

---

## 10. Multi-project workspace & Global AI Governance

Status: **design only.** Nothing in this section changes Phase 0 or Phase 1, which ship first, unmodified, against the single implicit project already in PLAN-PHASE-0-1.md. This is the design for **Phase 1.5**, a new phase inserted between Phase 1 and Phase 2 in §7.

### 10.0 Why this comes before Phase 2, not after

Guiding Principle 4 requires enforcement at the tool boundary, before any agent tool call — not layered on after the fact. Phase 2 gives the developer agent a real runtime with real tool access. If Phase 1.5's governance layer doesn't exist yet, Phase 2 would briefly run a real agent against hand-seeded policy defaults only, with no compiled, auditable rule set behind them. Phase 1.5 closes that gap first. It also lets the dashboard manage more than one codebase — real usage rarely stays single-project for long.

### 10.1 The core split: governance is global, context is per-project

This is Guiding Principle 8. A duplicated rule is a rule that can silently drift out of sync between projects, so behavior rules are authored exactly once and knowledge is scoped per project:

```
                    Global AI Rules                    ← WHAT the AI may do
                    (security, tools, code, infra)         same everywhere, one set
                            │
              ┌─────────────┼─────────────┐
              │                           │
         Project: eMega            Project: Affiliate
         WooCommerce               Sites                ← WHAT the AI knows
              │                           │                 differs per project
         ├─ Repository(s)            ├─ Repository(s)
         ├─ Context entries          ├─ Context entries
         │   • Laravel sync svc      │   • WordPress sites
         │   • WooCommerce = source  │   • Amazon affiliate
         │     of truth              │     workflows
         │   • AutoDS integration    │
         ├─ Status, tasks            ├─ Status, tasks
         └─ Artifacts, history       └─ Artifacts, history
```

A project **never** overrides a global rule. There is no per-project security exception — only per-project *knowledge*. This closes off an entire class of misconfiguration (a rule quietly relaxed "just for this one project") and keeps the whole rule set auditable in one place.

### 10.2 Rule hierarchy: how an agent's context is assembled

Every agent run renders its context in this fixed order, as four distinct, labeled blocks — never merged into one undifferentiated prompt, so each layer stays attributable:

1. **Global AI Rules** — permanent, operator-governed, identical for every agent, every project, every run. (source: new `governance_rules` table, §10.4)
2. **Agent Role Rules** — what a developer/architect/designer does. (source: existing `agents.system_prompt_ref`, unchanged)
3. **Project Context** — repository info and project-specific knowledge. (source: existing `repositories` plus new `project_context_entries`, §10.4)
4. **Task Instructions** — this task's title, description, acceptance criteria, operator comments, prior-stage artifacts. (source: existing `acc.get_task_context()`, §4.3, unchanged)

```
render_agent_context(agent, project, task) =
    [1] global_rules()                    # identical for every call
  + [2] agent.system_prompt_ref            # per agent role
  + [3] project.context_entries + repos    # per project
  + [4] task.get_task_context()            # per task, most volatile
```

Two consequences of this exact order, both deliberate:

- **Precedence.** If a task instruction or a project note ever appears to conflict with a global rule, the global rule wins, unconditionally. The layers are additive for *knowledge*, never for *permission* — a project's context can tell the agent what WooCommerce is; it can never tell the agent an approval isn't required.
- **Prompt caching.** Static content first, volatile content last, is exactly what prompt caching wants: layers 1–2 are identical across many runs, layer 3 changes only per project, and only layer 4 changes every run. This directly serves Risk R2 (runaway cost) at no extra engineering cost.

The rendered context (or its hash) is stored per run — either as an `artifacts` row (`kind = 'prompt_context'`) or a new `agent_runs.prompt_hash` column — so a run's actual instructions are always reconstructable. This closes a gap in Risk R10 (non-determinism): today the architecture records the *template version*, not the fully assembled text a specific run actually saw.

### 10.3 Tool enforcement: rules compile to policy, not just prompt text

Guiding Principle 4 already says enforcement belongs at the tool boundary. Global AI Rules make that concrete: every governance rule that constrains a *tool call* compiles into one or more rows of the existing `policies` table (§2.2, §4.1) — the same table `mcp-gateway` already evaluates on every `tools/call`. A rule never lives only in a prompt.

```
Global AI Rules (authored, human-readable, versioned)
        │
        │  compiled on save (governance sync job)
        ▼
   ┌──────────────────────┬───────────────────────────┐
   │                       │                           │
policies rows         workflow exit_checks        rendered into
(mcp-gateway           (process gates, e.g.        context layer 1
 enforces at the        "tests_pass" before          (defense in depth:
 tool boundary, §4.1)   a stage can complete)         the agent is also
                                                       TOLD the rule)
```

Worked example, matching the requirement exactly — an agent requests to connect an external API:

1. The agent calls a tool the gateway has no explicit `allow` policy for.
2. Gateway policy evaluation (§4.1 step 3) falls through to the **default effect for unlisted tools**, which Global AI Rules fix at `require_approval`, never `allow`. This is deny-by-default: anything not explicitly allowed needs a human.
3. The gateway creates an `approvals` row, `action_type = 'tool_access'`, and pauses the call (§4.1 step 4 / §5.4 mechanism B).
4. The approval card shows exactly the fields the requirement asks for, using the existing flexible `approvals.payload` jsonb — no schema change needed for the payload shape itself:
   ```json
   {
     "action_type": "tool_access",
     "payload": {
       "tool_name": "stripe-mcp",
       "reason": "Task ACC-88 needs to look up a customer's subscription status",
       "estimated_cost": "pay-per-call, ~$0.001/request",
       "permissions_requested": ["read: customers", "read: subscriptions"]
     }
   }
   ```
5. The operator approves or rejects (§5.4). Only on approval does the gateway forward the call. Both the decision and any resulting policy change (if the operator chooses to allowlist the tool going forward) are events (§3.4).

Global Rules → compiled policy defaults (seed data, extends the §5.4 table):

| Governance rule (Tools category) | Compiles to |
|---|---|
| Free, already-allowlisted tools may be used automatically | `policies` row, `effect = allow` |
| Paid tools require approval | `policies` row, `effect = require_approval`, `risk_level = medium` |
| Third-party services/APIs require approval before first connection | `policies` row per new `server`, `effect = require_approval` until explicitly allowlisted |
| Unknown external tools require approval | the gateway's **default** effect (no matching row) is `require_approval`, never `allow` |

Rules that aren't tool-call-shaped ("run tests before completing a task," "review changes before merge") compile into `workflows.definition[].exit_checks` (already in the schema, §5.2) instead of `policies` rows — same governance source, a different enforcement point, because that's what the rule actually constrains.

### 10.4 Database considerations (new in Phase 1.5)

```sql
-- ─── Global AI governance rules (the authored source of truth) ──────────
CREATE TABLE governance_rules (
  id            uuid PRIMARY KEY,
  category      text NOT NULL,                 -- security | tools | code | infrastructure
  statement     text NOT NULL,                  -- human-readable rule, e.g. "Never expose secrets"
  enforcement   text NOT NULL,                  -- advisory | require_approval | hard_block
  applies_to    text[] NOT NULL DEFAULT '{}',   -- capabilities this rule targets; empty = all
  enabled       boolean NOT NULL DEFAULT true,
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Traceability: a policy row generated from a rule points back to it, and is
-- read-only in the UI (edit the rule, not the generated policy) — mitigates R16.
ALTER TABLE policies ADD COLUMN source_rule_id uuid REFERENCES governance_rules(id);

-- approvals.action_type is already free text (§2.2): 'tool_access' joins
-- merge | deploy | db_migration | db_write | shell_command | ... with no migration needed.

-- ─── Per-project knowledge base ──────────────────────────────────────────
CREATE TABLE project_context_entries (
  id            uuid PRIMARY KEY,
  project_id    uuid NOT NULL REFERENCES projects(id),
  kind          text NOT NULL,                 -- architecture | convention | integration | gotcha | glossary
  title         text NOT NULL,
  body          text NOT NULL,                 -- markdown
  priority      int NOT NULL DEFAULT 100,       -- render order within a project
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    uuid REFERENCES users(id)
);

-- ─── Project-level status, distinct from any single task's status ───────
ALTER TABLE projects ADD COLUMN status text NOT NULL DEFAULT 'active';
                                        -- active | paused | archived

-- ─── Project-level artifacts (README, ADRs), alongside task-level ones ──
ALTER TABLE artifacts ALTER COLUMN task_id DROP NOT NULL;
ALTER TABLE artifacts ADD COLUMN project_id uuid REFERENCES projects(id);
ALTER TABLE artifacts ADD CONSTRAINT artifacts_task_or_project
  CHECK (task_id IS NOT NULL OR project_id IS NOT NULL);
```

New event types extend the §3.4 catalogue: `governance_rule.created | governance_rule.updated | governance_rule.disabled`, `project.context_updated`, `project.status_changed`. Rule and context changes are events like everything else — every edit to a Global AI Rule is in the permanent, append-only history, same as a task status change.

### 10.5 UI considerations (new in Phase 1.5)

- **Project switcher** in the sidebar, replacing today's implicit single project. Each project keeps its own task board, agent activity and events, filtered by `project_id` (the API already supports this — §3.2's `GET /tasks?project_id=…`, `GET /events?project_id=…`).
- **Project settings page:** context entries editor (markdown, grouped by `kind`), a status toggle (active/paused/archived), the repository list (already designed), and an artifacts tab for project-level artifacts.
- **Global Rules page**, admin-only (existing `Role.ADMIN`): rules grouped by the four categories, each showing what it compiled to (which `policies` rows or `exit_checks`), so an operator sees cause and effect, not just intent. Editing is add/edit/disable, never a hard delete — disabling is an event, same as everything else.
- **Approval card** gains a `tool_access` layout: requested tool, reason, estimated cost, permissions requested — one more card variant in the existing generic Approvals inbox (§5.4), not a new inbox.
- **Context preview**, per task: "what will the agent actually see" — renders the four layers of §10.2 read-only, for trust and debugging, before a run starts.

### 10.6 Phase placement

See the inserted **Phase 1.5** row in §7. It sits between Phase 1 (dashboard shell) and Phase 2 (first real agent), for the reason in §10.0.

### 10.7 A note on the parallel to §11

The `docs/context/` layer described in §11 — a durable, per-repository knowledge base for whichever AI assistant is developing *this* codebase — is a specific instance of the "Project Context" idea in §10.1, just stored as files instead of `project_context_entries` rows because there's no running AI Command Center instance managing its own development yet. Worth remembering for later: once the product exists, dogfooding is available for free — this repository could be registered as one of the Command Center's own projects, and `docs/context/*.md` could seed that project's `project_context_entries`.

---

## 11. AI session continuity (`docs/context/`)

This section is about how *any* AI assistant — this one or a future session, on this tool or another — keeps working effectively on this repository across separate conversations. It is not a runtime feature of the AI Command Center product itself (see §10.7 for the parallel), and adding it changes no code and no Phase 0/1 scope.

### 11.1 The problem

A new session starts with zero memory of this repository's history: which decisions were made and why, what has already been tried and failed, what the user's standing preferences are. Re-deriving all of that from `git log` and code reading is slow and lossy. A standing rule such as "wait for approval before each phase" has no durable, version-controlled home unless one is designed for it.

### 11.2 The layer

A plain-file documentation layer, `docs/context/`, alongside the existing `docs/adr/`:

| File | Contents | Update cadence |
|---|---|---|
| `current-context.md` | A snapshot, not a log: current phase/milestone, what's in progress, immediate next steps | Rewritten each "save session" |
| `decisions.md` | A running, dated log of decisions, one line to one paragraph each. Links out to the matching `docs/adr/NNNN-*.md` for anything that got the full ADR treatment | Appended each "save session" |
| `lessons-learned.md` | Mistakes and their fixes — process and tooling knowledge, not product design (e.g. "PowerShell mangles nested quotes across `wsl.exe`; write scripts to disk and invoke them, don't inline `-c`") | Appended each "save session" |
| `recurring-rules.md` | Standing working agreements from the user that apply to every future turn (e.g. "report files changed / tests passed / status / next step after each milestone") — never one-off task instructions | Appended, rarely |
| `archive.md` | Anything superseded, moved here with a dated note of why, instead of deleted | Appended each "save session" |

`decisions.md` versus `docs/adr/`: an ADR is for one significant, weighed decision with alternatives considered (`docs/adr/0001-command-layer-as-single-write-path.md` is the existing model). `decisions.md` is the lighter, faster log everything else goes in — smaller calls and operational choices worth remembering but not worth a whole ADR.

### 11.3 The "save session" command

When the user says **"save session"**, the assistant:

1. Reviews the conversation and recent commits for anything decided → appends dated entries to `decisions.md`.
2. Rewrites `current-context.md` from scratch to reflect the latest state — it is a snapshot, so stale content is replaced, not accumulated.
3. Appends any new mistake-and-fix pairs to `lessons-learned.md`.
4. Appends any new standing, durable rule the user gave to `recurring-rules.md`, filtering out anything specific to one task.
5. Scans the active files for anything now superseded and moves it to `archive.md`, with a one-line dated note of why, rather than deleting it.

This is a manual, explicit trigger, not something run on every turn — cheap at a natural checkpoint, wasteful continuously.

### 11.4 Relationship to this assistant's own memory files

This assistant already maintains a separate, private memory store outside this repository (this machine's local memory directory), with similarly named files (`acc-project-phases.md`, `wsl-dev-environment.md`). The distinction:

- `docs/context/` is **in the repository**, git-tracked, and visible to any collaborator or any AI tool that opens this codebase — the durable, shared source of truth.
- The private memory files are **local to one machine and one tool** — a fast index pointing at what matters, convenient but not portable and not visible to a teammate, human or AI.

Once `docs/context/` exists, the private memory files should shrink to pointers into it rather than duplicating its content. That reconciliation happens after this update is approved, not as part of it.
