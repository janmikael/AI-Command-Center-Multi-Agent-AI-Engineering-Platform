# Implementation Plan: Phase 0 (Foundation) + Phase 1 (Dashboard Core)

Status: **Awaiting approval.** No code has been written yet.
Parent document: [ARCHITECTURE.md](ARCHITECTURE.md)
Date: 2026-09-24

## Goal

Build the first usable version: you log in, open a dashboard, and see agents, tasks on a Kanban board, task details with status history, and a live activity feed.

## Out of scope

The following are explicitly **not** in these two phases:
- the MCP gateway
- agent runtimes, the Claude Agent SDK and the OpenAI API
- sandbox runners
- the orchestrator worker process, the job queue, approvals, policies, budgets and runs

---

## 0. How the architecture rules apply before there is an orchestrator

Phase 1 has no orchestrator process and no agents yet. The rules still hold, because of the design below. When Phase 2 arrives, it plugs in without rewrites.

| Rule | How Phase 0/1 enforces it |
|---|---|
| **The database owns state** | Postgres is the only store. Redis only carries transient data: pub/sub, login rate limits. The UI never holds authoritative state. It renders REST responses, and WebSocket messages only trigger refetches. |
| **Agents never update status directly** | There is **no** generic `PATCH status` endpoint. Status changes only through one command, `transition_task()`. It validates against the domain state machine and records *who* asked. In Phase 1 the only actor is the operator (`user:<id>`). Later, agent *requests* become orchestrator commands through the same function. |
| **Everything goes through the orchestrator** | All writes live in one application layer: `acc/commands/`. This becomes the orchestrator's command layer. API routers call commands and never write through the ORM themselves. An `import-linter` contract enforces this in CI. |
| **Events are append-only** | A DB trigger rejects `UPDATE`/`DELETE` on `events`, and the app DB role has no `UPDATE`/`DELETE` grant on it. Every command writes its state change and its event **in the same transaction**. |
| **Progress is calculated, not reported** | `tasks.progress_pct` exists but can't be written by any API. It stays `0` until the Phase 4 calculator exists. The UI shows it as "—" while there are no subtasks. |

### Refinements to ARCHITECTURE.md introduced here

1. **`sessions` table** for server-side auth sessions. The architecture doc left the storage for auth credentials open.
2. **A global `events.seq`** (bigint sequence) instead of a per-topic `seq`. This is simpler, and it is enough for resuming after a reconnect with a single operator (see §3.3).
3. **Tables and columns ship as a forward-compatible subset.** Columns that need Phase 2+ tables (`workflow_id`, budgets, `attempt`) are added by later migrations, not left as unused stubs now.
4. **Agent status is derived by a Phase 1 rule** from events and assignments (§3.4). Phase 2 swaps the inputs to runs and heartbeats behind the same function.

---

## 1. Folder structure

Only what Phase 0/1 creates. Later folders from ARCHITECTURE.md §6 are added when their phase starts.

```
ai-command-center/
├── .gitattributes                     # * text=auto eol=lf  (critical on Windows: shell scripts in containers)
├── .gitignore
├── .editorconfig
├── .env.example                       # every variable documented, no real secrets
├── Makefile                           # up, down, logs, migrate, seed, test, lint, gen-client, simulate
├── README.md                          # setup in <10 steps
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PLAN-PHASE-0-1.md              # this file
│   └── adr/
│       └── 0001-command-layer-as-single-write-path.md
│
├── backend/
│   ├── pyproject.toml                 # uv; ruff, mypy, pytest, import-linter config
│   ├── uv.lock
│   ├── Dockerfile                     # multi-stage, non-root, python slim
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py                     # async engine, naming conventions
│   │   └── versions/                  # 0001 … 0006 (see §2)
│   ├── src/acc/
│   │   ├── __init__.py
│   │   ├── config.py                  # pydantic-settings: Settings, env-specific validation
│   │   ├── logging.py                 # structlog setup (JSON prod / console dev), request-id binding
│   │   ├── ids.py                     # UUIDv7 generation
│   │   │
│   │   ├── domain/                    # PURE Python, no I/O, 100% unit-tested
│   │   │   ├── task_status.py         # TaskStatus enum + ALLOWED_TRANSITIONS + validate_transition()
│   │   │   ├── agent_status.py        # derive_agent_status(inputs) -> AgentStatus
│   │   │   ├── events.py              # EventType catalogue, payload schemas (pydantic)
│   │   │   └── errors.py              # DomainError, InvalidTransition, NotFound, Conflict
│   │   │
│   │   ├── db/
│   │   │   ├── base.py                # DeclarativeBase, naming convention, common columns
│   │   │   ├── session.py             # async engine + session factory, UnitOfWork
│   │   │   └── models/                # user.py, session.py, project.py, repository.py,
│   │   │                              # agent.py, task.py, event.py
│   │   │
│   │   ├── commands/                  # THE ONLY WRITE PATH (future orchestrator command layer)
│   │   │   ├── _base.py               # run_command(): tx → mutate → append_event → commit → publish
│   │   │   ├── auth.py                # login, logout, create_operator, change_password
│   │   │   ├── projects.py            # create_project, add_repository
│   │   │   ├── tasks.py               # create_task, update_task_details, transition_task, assign_task
│   │   │   └── events.py              # append_event (internal), record_system_event
│   │   │
│   │   ├── queries/                   # read models: plain SELECTs, pagination helpers
│   │   │   ├── agents.py  tasks.py  events.py  overview.py
│   │   │
│   │   ├── realtime/
│   │   │   ├── publisher.py           # publish(event) → Redis channel acc:events
│   │   │   └── hub.py                 # one Redis subscriber per process → fan-out to sockets by topic
│   │   │
│   │   ├── api/
│   │   │   ├── app.py                 # create_app(): middleware, routers, lifespan (db/redis/hub)
│   │   │   ├── deps.py                # get_db, current_operator, require_csrf_origin
│   │   │   ├── errors.py              # RFC 9457 problem+json handlers
│   │   │   ├── middleware.py          # request id, access log, security headers
│   │   │   ├── schemas/               # request/response pydantic models (API contract)
│   │   │   └── routers/
│   │   │       ├── health.py  auth.py  agents.py  projects.py
│   │   │       ├── tasks.py   events.py  overview.py  ws.py
│   │   │
│   │   └── cli.py                     # `acc` CLI: create-operator, seed, simulate-activity (dev only)
│   │
│   └── tests/
│       ├── conftest.py                # PG + Redis fixtures, per-test transaction rollback, client, auth
│       ├── unit/                      # domain/*
│       ├── integration/               # api/*, commands/*, db constraints, ws
│       └── migrations/                # upgrade/downgrade roundtrip, drift check
│
├── apps/web/
│   ├── package.json                   # pnpm
│   ├── Dockerfile                     # multi-stage, Next.js standalone output, non-root
│   ├── next.config.ts                 # output: 'standalone'
│   ├── tsconfig.json  eslint.config.mjs  postcss.config.mjs
│   ├── openapi/schema.json            # generated from backend (committed; CI checks freshness)
│   ├── src/
│   │   ├── middleware.ts              # no session cookie → redirect /login
│   │   ├── app/
│   │   │   ├── layout.tsx             # fonts, theme, QueryClientProvider, RealtimeProvider
│   │   │   ├── globals.css            # Tailwind v4 + design tokens (light/dark)
│   │   │   ├── (auth)/login/page.tsx
│   │   │   └── (dashboard)/
│   │   │       ├── layout.tsx         # shell: sidebar, top bar (system status dot, operator menu)
│   │   │       ├── page.tsx           # Overview
│   │   │       ├── agents/page.tsx
│   │   │       ├── agents/[slug]/page.tsx
│   │   │       ├── tasks/page.tsx     # Kanban
│   │   │       ├── tasks/[key]/page.tsx
│   │   │       └── activity/page.tsx  # full feed with filters
│   │   ├── components/
│   │   │   ├── ui/                    # shadcn/ui primitives
│   │   │   ├── shell/                 # Sidebar, TopBar, SystemStatusIndicator
│   │   │   ├── agents/                # AgentCard, AgentStatusBadge, AgentTable
│   │   │   ├── tasks/                 # KanbanBoard, KanbanColumn, TaskCard, CreateTaskDialog,
│   │   │   │                          # StatusHistory, AssignAgentSelect, TransitionMenu
│   │   │   └── activity/              # ActivityFeed, EventRow, EventIcon
│   │   └── lib/
│   │       ├── api/client.ts          # openapi-fetch typed client (credentials: 'include')
│   │       ├── api/schema.d.ts        # generated by openapi-typescript
│   │       ├── api/hooks.ts           # TanStack Query hooks per resource
│   │       ├── realtime/              # socket manager, reconnect/backoff, resume, event→query invalidation map
│   │       └── format.ts              # relative time, status labels/colors
│   └── tests/
│       ├── unit/                      # Vitest + Testing Library + MSW
│       └── e2e/                       # Playwright against the compose stack
│
├── infra/
│   ├── compose.yml                    # all services (prod-like)
│   ├── compose.dev.yml                # overrides: hot reload, bind mounts, exposed DB port
│   ├── caddy/Caddyfile
│   └── postgres/init/01-roles.sql     # acc_owner (migrations) + acc_app (runtime, no DELETE on events)
│
└── .github/workflows/ci.yml
```

**Stack versions:** These are pinned in lockfiles at scaffold time, to the latest stable release of each:
- Backend: Python 3.13, FastAPI, SQLAlchemy 2 (asyncpg), Alembic, pydantic-settings, structlog, argon2-cffi, redis-py
- Frontend: Node 22 LTS, Next.js (App Router), Tailwind v4, shadcn/ui, TanStack Query, openapi-fetch, dnd-kit
- Infrastructure: Postgres 16, Redis 7, Caddy 2

---

## 2. Database migration plan

**Principles:**
- Every migration is hand-reviewed. Autogenerate is only a starting point.
- Every migration has a working `downgrade()`.
- Enums, triggers and partitions are written explicitly in the migration.
- The **migration owner role** (`acc_owner`) is separate from the **runtime role** (`acc_app`).
- Seed data does **not** go in migrations. It goes in an idempotent `acc seed` command.

| # | Migration | Contents |
|---|---|---|
| **0001** | `baseline` | Extensions `citext` and `pgcrypto`. Sets the constraint naming convention. |
| **0002** | `users_sessions` | `users` (id, email citext unique, display_name, password_hash, role, totp_secret nullable, created_at, last_login_at). `sessions` (id = sha256 of the token, user_id, created_at, last_seen_at, expires_at, ip, user_agent, revoked_at). Index on `(user_id)` and on `(expires_at)`. |
| **0003** | `projects_repositories` | `projects` (… plus **`key_prefix`** such as `ACC` and **`next_task_number`** counter). `repositories` (as in the architecture doc). |
| **0004** | `agents` | `agents` (slug, display_name, role, adapter, model, config jsonb, enabled, created_at). `agent_capabilities`. The `adapter` column is text, and `'none'` is allowed for Phase 1 seed agents. |
| **0005** | `tasks` | `task_status` enum with the **full** set (backlog, planning, working, review, awaiting_approval, blocked, completed, failed, cancelled), so no enum migration is needed later. `tasks` columns: id, project_id, repository_id, parent_id, key (unique), title, description, acceptance_criteria, status, priority, assigned_agent_id, required_capability, progress_pct (default 0), created_by, created_at, updated_at, completed_at, **version**. Indexes on `(project_id, status)`, `(assigned_agent_id) WHERE status NOT IN terminal`, and `(parent_id)`. |
| **0006** | `events` | Partitioned by `RANGE (ts)`. Columns: `id uuid`, **`seq bigint DEFAULT nextval('events_seq')`**, ts, project_id, task_id, agent_id, actor, type, severity, payload jsonb. PK is `(id, ts)`. Indexes on `(seq)`, `(task_id, ts)`, `(agent_id, ts)`, `(type, ts)`. **Default partition**, plus a SQL function `acc_ensure_event_partitions(months_ahead int)` that the API calls on startup (idempotent). **Append-only trigger:** `BEFORE UPDATE OR DELETE … RAISE EXCEPTION`. `REVOKE UPDATE, DELETE, TRUNCATE ON events FROM acc_app`. |

**Deliberately deferred to later phases** (added by new migrations then):
- `agent_runs`, `artifacts`, `tool_calls`, `file_changes`, `usage_records`
- `approvals`, `policies`, `workflows`, `jobs`, `audit_log`
- task columns `workflow_id`, `stage`, `budget_usd`, `spent_usd`, `attempt`, `max_attempts`, `failure_reason`, `branch_name`

**Status history** needs no table of its own. It is the `task.status_changed` events for that task, which fits the append-only principle.

**Seed (`acc seed`, idempotent upsert by slug):**
- Default project: `ACC`.
- Agents (all with `adapter='none'` until Phase 2):
  - `architect` (Architect / Planner / Reviewer; capabilities plan, review_code)
  - `developer` (Developer; implement, test)
  - `designer` (UI/UX Designer; design_ui, review_ui)

**Operator bootstrap:** `acc create-operator --email … ` prompts for the password. There is no sign-up page.

---

## 3. API endpoint list

Base path is `/api/v1`, proxied by Caddy on the same origin as the web app, so cookies work without CORS.
- Errors use `application/problem+json`.
- Lists use a cursor: `{items, next_cursor}`.
- All mutating requests require a session cookie **and** a same-origin `Origin` header (CSRF defense, together with `SameSite=Strict`).

### 3.1 Phase 0

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | none | Liveness. The process is up. No dependency checks. |
| GET | `/readyz` | none | Readiness: DB ping, Redis ping, Alembic revision == head. Returns 503 with details on failure. |
| POST | `/api/v1/auth/login` | none | `{email, password}` → sets the `acc_session` cookie (HttpOnly, Secure, SameSite=Strict, 12 h sliding, 7 d absolute). Rate limit: 5 attempts per 15 min per IP+email (Redis). Argon2id hashes. Generic error message. |
| POST | `/api/v1/auth/logout` | session | Revokes the session and clears the cookie. |
| GET | `/api/v1/auth/me` | session | Current operator. |
| POST | `/api/v1/auth/change-password` | session | Requires the current password. Revokes the operator's other sessions. |

### 3.2 Phase 1

| Method | Path | Purpose / notes |
|---|---|---|
| GET | `/api/v1/overview` | One call for the Overview page: agents grouped by derived status, active tasks (planning/working/review), counts per status, system status (db, redis, ws clients connected, latest event seq), 20 most recent events |
| GET | `/api/v1/agents` | List: slug, name, role, capabilities, **derived status**, current task (key+title), last_activity_at |
| GET | `/api/v1/agents/{slug}` | Detail: the above plus assigned tasks and the 50 most recent events |
| GET | `/api/v1/projects` | List |
| POST | `/api/v1/projects` | Create (`name`, `slug`, `key_prefix`) |
| GET | `/api/v1/projects/{slug}` | Detail plus repositories |
| POST | `/api/v1/projects/{slug}/repositories` | Register a repo (metadata only; nothing is cloned in Phase 1) |
| GET | `/api/v1/tasks` | Filters: `project`, `status` (multi), `agent`, `q`. Board mode `?view=board` returns tasks grouped by status. |
| POST | `/api/v1/tasks` | Create a task in `backlog`. Allocates the key atomically (`UPDATE projects SET next_task_number = next_task_number + 1 … RETURNING`). Emits `task.created`. Optional `Idempotency-Key`. |
| GET | `/api/v1/tasks/{key}` | Detail: fields, assigned agent, **status history** (from events), subtasks (empty for now) |
| PATCH | `/api/v1/tasks/{key}` | Edit **only** title, description, priority and acceptance_criteria. Requires `If-Match: <version>`. Emits `task.updated`. `status`, `assigned_agent_id` and `progress_pct` are **rejected (422)**. |
| POST | `/api/v1/tasks/{key}/transition` | `{to: TaskStatus, reason?: string}` plus `If-Match`. Validated by the domain state machine. Illegal → 409 with the allowed targets listed. Emits `task.status_changed {from, to, reason}`. |
| POST | `/api/v1/tasks/{key}/assign` | `{agent_slug: string \| null}`. Checks that the agent is enabled. Emits `task.assigned {from, to}`. |
| GET | `/api/v1/tasks/{key}/events` | The task's timeline (cursor) |
| GET | `/api/v1/events` | Activity feed. Filters: `project`, `task`, `agent`, `type` (multi), `severity`, `after_seq`, `before_seq`, `limit`. Newest first. |
| WS | `/ws` | Realtime, see below |

### 3.3 WebSocket protocol (Phase 1)

```jsonc
// client → server
{"op":"subscribe","topics":["activity","task:ACC-12","agent:developer"]}
{"op":"unsubscribe","topics":["task:ACC-12"]}
{"op":"resume","after_seq":1842}          // after reconnect
{"op":"ping"}

// server → client
{"type":"hello","server_seq":1850}
{"type":"event","topics":["activity","task:ACC-12"],"event":{ id, seq, ts, type, actor, task_key, agent_slug, payload }}
{"type":"resync"}                          // gap too large (>500) → client refetches via REST
{"type":"pong"}
```

- **Auth:** The session cookie is checked during the handshake, and the `Origin` must match. The server re-validates the session every 5 minutes and closes the socket (code 4401) if it was revoked.
- **Fan-out:**
  1. A command commits.
  2. It publishes the event to Redis channel `acc:events`.
  3. Each API process has one subscriber, which routes the event to sockets subscribed to any matching topic.
- **Resume and gaps:**
  - The client tracks the highest `seq` it has seen.
  - On reconnect, the server replays `seq > after_seq - 50`, and the client de-duplicates by `id`. The overlap is there because sequence values are assigned before commit, so events can commit slightly out of order.
  - If Redis is down, events are still in Postgres. The dashboard then shows "live updates paused" and polls every 10 s.
- **UI model:** WebSocket events **never mutate UI state directly**. They map to TanStack Query invalidations (for example, `task.status_changed` invalidates `tasks`, `task:{key}`, `overview` and `agents`), except the activity feed, which prepends the event. REST remains the source of truth.

### 3.4 Derived agent status (Phase 1 rule)

`derive_agent_status(enabled, active_assigned_tasks, last_event_at, now)` is a pure function:
- `disabled`: the agent is disabled
- `working`: it has an assigned task in planning, working or review, **and** activity within 10 minutes
- `idle`: it has activity within 10 minutes and no active assigned task
- `offline`: no activity within 10 minutes. This is what the three seed agents will honestly show until Phase 2, or while the dev simulator runs.

Phase 2 replaces the inputs with runs and heartbeats. The signature and the UI stay the same.

### 3.5 Dev-only activity simulator

`acc simulate-activity --minutes 5` emits realistic `agent.message` and `agent.decision` events for the seed agents. It can also move tasks, but **through the same commands**, with actor `system:simulator`. Its purposes:
- demoing and testing live updates before real agents exist
- producing data for the e2e tests

It refuses to run when `ACC_ENV=production`.

---

## 4. Development checklist

Each item is roughly one commit. Items marked *(opt)* can be dropped without affecting the rest.

### Phase 0: Foundation

**Repo and tooling**
- [ ] `git init`; `.gitattributes` (LF), `.gitignore`, `.editorconfig`, README skeleton
- [ ] Backend scaffold: `uv init`; ruff, mypy (strict for `domain/`), pytest, import-linter configured
- [ ] Web scaffold: `create-next-app` (TS, App Router, Tailwind v4, ESLint), pnpm, shadcn/ui init, Vitest
- [ ] Makefile targets: `up`, `down`, `logs`, `migrate`, `seed`, `test`, `lint`, `fmt`, `gen-client`

**Backend core**
- [ ] `config.py`: typed settings. Fails fast on missing or weak secrets when `ACC_ENV=production`.
- [ ] `logging.py`: structlog. JSON in prod, pretty in dev. Binds `request_id`. Uvicorn access log goes through structlog. Secret-looking keys are redacted.
- [ ] Middleware: `X-Request-ID` (accept or generate), security headers, request timing
- [ ] DB: async engine, session dependency, naming convention, `UnitOfWork`
- [ ] Alembic async `env.py`, and migrations 0001 and 0002
- [ ] Postgres init script: `acc_owner` and `acc_app` roles
- [ ] `/healthz`, `/readyz`
- [ ] Problem+json error handlers (domain errors mapped to 404, 409 or 422)

**Auth**
- [ ] Argon2id hashing; `acc create-operator` CLI
- [ ] Session create, validate and revoke; sliding plus absolute expiry; cookie flags
- [ ] Origin-check dependency for mutating routes
- [ ] Login rate limiting (Redis)
- [ ] Endpoints: login, logout, me, change-password
- [ ] *(opt)* TOTP second factor (enroll and verify)

**Frontend shell**
- [ ] Design tokens (light/dark), base typography, the shadcn primitives needed (button, input, card, badge, dialog, dropdown, select, toast, skeleton)
- [ ] Generated typed API client (`openapi-typescript` + `openapi-fetch`); `make gen-client`
- [ ] `/login` page (form, errors, redirect back to the original URL)
- [ ] `middleware.ts` redirect for routes that need auth; `useMe()`; logout in the operator menu
- [ ] Dashboard layout shell: sidebar (Overview, Agents, Tasks, Activity), top bar with system status indicator, empty states

**Infrastructure**
- [ ] Backend and web Dockerfiles (multi-stage, non-root, healthchecks)
- [ ] `compose.yml` + `compose.dev.yml` + Caddyfile (see §5)
- [ ] One-shot `migrate` service that gates `api` startup
- [ ] CI: lint, typecheck, unit tests, integration tests (service containers), image build, compose smoke test

**Phase 0 definition of done:** `make up` starts all five services healthy, plus `migrate`. Visiting `http://localhost` redirects to `/login`. The operator can log in and see the empty shell, and log out. CI is green.

### Phase 1: Dashboard Core

**Data**
- [ ] Migrations 0003 to 0006, including the events trigger, grants and the partition function
- [ ] SQLAlchemy models for all tables; migration drift test
- [ ] `acc seed`

**Domain (pure, test-first)**
- [ ] `TaskStatus` + `ALLOWED_TRANSITIONS` table (operator-allowed transitions, see below)
- [ ] `derive_agent_status()`
- [ ] Event type catalogue + payload schemas

**Commands and queries**
- [ ] `run_command` wrapper: one transaction for mutation plus `append_event`, then post-commit publish (publish failure is logged, never fails the request)
- [ ] `create_task` (atomic key allocation), `update_task_details`, `transition_task`, `assign_task`, `create_project`, `add_repository`
- [ ] Queries: agents (with derived status and last activity), tasks (list/board/detail with history), events (cursor), overview
- [ ] import-linter contract: `acc.api` must not import `acc.db.models` for writes. Only `acc.commands` may write.

**API**
- [ ] Routers for everything in §3.2; `If-Match` handling; `Idempotency-Key` on task create *(opt)*

**Realtime**
- [ ] Redis publisher; per-process hub; `/ws` handshake auth; subscribe, unsubscribe, resume, ping; session revalidation
- [ ] `acc simulate-activity` (dev only)

**Frontend pages**
- [ ] `RealtimeProvider`: connect, exponential backoff, resume, event-to-invalidation map, a "live / reconnecting / paused" indicator
- [ ] **Overview:** agent cards (status, current task), active tasks list, status counts, system status panel, recent activity (live)
- [ ] **Agents:** table (name, role, status badge, current task, last activity, relative time that updates) → agent detail page
- [ ] **Tasks:** Kanban with columns Backlog, Planning, Working, Review, Completed; create-task dialog; drag and drop with dnd-kit
  - Dropping onto an illegal column is rejected client-side, using the allowed transitions returned with each task. It also shows the server's 409 if it rejects.
  - Transitions are optimistic, with rollback on error.
  - A collapsible **"Other" lane** holds blocked, awaiting_approval, failed and cancelled tasks, so none are hidden.
- [ ] **Task detail:** description (markdown rendered, sanitized), editable fields, assign-agent select, transition menu with reason, **status history timeline**, event list (live)
- [ ] **Activity:** full feed with filters (type, agent, task, severity) and infinite scroll

**Phase 1 definition of done:**
- The operator creates a task, assigns `developer`, drags it Backlog → Planning → Working → Review → Completed, and sees every change as an event in the task history and the activity feed.
- A second browser tab updates live with no refresh.
- Killing and restarting `api` makes the UI reconnect and catch up.
- The simulator makes agents appear as `working` or `idle`.
- `UPDATE events …` in psql as `acc_app` fails.

**Operator transitions allowed in Phase 1** (manual mode; the orchestrator takes over these in Phase 2+):

```
backlog  → planning, cancelled
planning → working, backlog, blocked, cancelled
working  → review, blocked, failed, cancelled
review   → working, completed, failed, cancelled
blocked  → planning, working, cancelled
failed   → backlog
completed, cancelled → (terminal)
awaiting_approval → not reachable manually in Phase 1 (approval system arrives in Phase 3)
```

---

## 5. Docker setup plan

### 5.1 Services (`infra/compose.yml`)

| Service | Image | Networks | Exposed | Healthcheck | Depends on |
|---|---|---|---|---|---|
| `caddy` | `caddy:2-alpine` | edge, internal | **80/443** (only public entry) | `wget /healthz` via itself | web, api healthy |
| `web` | built `apps/web` (standalone Next.js, `node` user) | internal | – | `GET /` 200 | api healthy |
| `api` | built `backend` (uvicorn, `app` user) | internal | – | `GET /readyz` | migrate completed, postgres + redis healthy |
| `migrate` | same image as `api`, cmd `alembic upgrade head && acc ensure-partitions` | internal | – | one-shot | postgres healthy |
| `postgres` | `postgres:16-alpine` | internal | – (dev: `127.0.0.1:5432`) | `pg_isready` | – |
| `redis` | `redis:7-alpine`, `--appendonly no`, password set | internal | – | `redis-cli ping` | – |

- **Networks:** `edge` connects Caddy only. `internal` is `internal: true` in prod, so postgres and redis are unreachable from outside.
- **Volumes:** `pgdata` and `caddy_data` (certs). No other persistent state.
- **Caddy routing** (same origin, no CORS):
  - `/api/*`, `/ws`, `/healthz`, `/readyz` → `api:8000`, with WebSocket upgrade handled by Caddy automatically
  - everything else → `web:3000`
  - Dev: `http://localhost`. Prod: a domain with automatic TLS, or a Tailscale hostname.
- **Next.js server-side fetches** go directly to `http://api:8000` (`API_INTERNAL_URL`) and forward the session cookie. Browser fetches go to the relative `/api/...`.
- **Secrets:** `.env` for dev. For prod, `POSTGRES_PASSWORD`, `REDIS_PASSWORD` and `SESSION_SECRET` come from Docker secrets or an env file outside the repo. The API refuses to start in prod with default secrets.
- **Hardening (prod file):**
  - `read_only: true` where possible
  - `no-new-privileges`, dropped capabilities
  - memory limits
  - log rotation (`json-file`, max-size)

### 5.2 Dev mode (`compose.dev.yml`)

- `api`: bind-mounts `backend/src`, runs `uvicorn --reload`
- `web`: bind-mounts `apps/web`, runs `next dev`
- postgres is exposed on `127.0.0.1:5432`, so you can use psql or a GUI
- **Windows note:** Use Docker Desktop with the WSL2 backend. Hot reload over a Windows-path bind mount is slow and file watching is unreliable. Either keep the repo inside the WSL filesystem, or use the **hybrid option**: `make infra-up` runs only postgres, redis and caddy in Docker, while `api` and `web` run natively. Both are documented in the README.

---

## 6. Testing strategy

| Layer | Tooling | What is covered | Target |
|---|---|---|---|
| **Domain unit** | pytest | Every legal and illegal transition (table-driven); `derive_agent_status` edge cases (boundary at 10 min, disabled, no events); event payload validation | 100% lines/branches in `domain/` |
| **Command integration** | pytest + real Postgres (no SQLite: enums, triggers and partitions must be real) | Each command writes state **and** exactly one event atomically. An injected failure after the mutation leaves no state and no event. Concurrent `create_task` produces unique keys. Optimistic lock conflict → 409. | all commands |
| **DB guarantees** | pytest | `UPDATE`/`DELETE` on `events` raises (trigger) and is denied for `acc_app` (grant). Partition function is idempotent. FK and unique constraints hold. | – |
| **API integration** | httpx `AsyncClient` against the app | Auth flows (bad password, rate limit, expired or revoked session, missing Origin → 403); PATCH rejects `status`; transition 409 lists allowed targets; problem+json shape; pagination cursors | every endpoint: happy path plus at least one failure |
| **WebSocket** | Starlette test client WS | Unauthenticated → close 4401. Subscribe then a command yields the event on the right topics only. Resume replays with overlap. Resync for large gaps. | – |
| **Migrations** | pytest | `upgrade head → downgrade base → upgrade head` on an empty DB; **drift check** (models vs migrations produce no autogenerate diff) | every migration |
| **Architecture** | import-linter | Routers can't write through models; `domain` imports nothing from db, api or redis | enforced in CI |
| **Frontend unit** | Vitest + Testing Library + MSW | Kanban column mapping and "Other" lane; illegal drop rejected; optimistic rollback; realtime event → invalidation map; login form errors | key components and hooks |
| **E2E** | Playwright against `make up` | Login → create task → assign → drag through all columns → task detail shows history. **Two browser contexts:** a change in A appears in B without a reload. Restart `api` → UI shows reconnecting, then catches up. Logout blocks access. | 5 to 6 critical flows |
| **Contract** | CI step | Regenerate OpenAPI → TS types. Fail if they differ from what is committed. | – |
| **Smoke** | CI step | `docker compose up --wait`, then `curl` `/healthz`, `/readyz` and `/` through Caddy | – |

**Test isolation:**
- Integration tests use one database per CI run (Alembic-migrated once). Each test runs inside a transaction with savepoint rollback.
- Tests that need real commits (WebSocket, concurrency) use a truncate fixture.
- Events are exempt from truncation, because the trigger blocks it. Those tests use a dedicated schema that is dropped afterwards.

**CI pipeline (GitHub Actions):** `lint+typecheck (py, ts)` → `unit` → `integration (PG+Redis service containers)` → `build images` → `compose smoke + e2e`. It runs on every push, and the images are cached.

---

## 7. Decisions needed before coding

1. **Operator login:** password (Argon2id) with **optional TOTP** is recommended. The TOTP item can slip to after Phase 1 without affecting anything else. Passkeys would add roughly 2 days.
2. **Manual status moves in Phase 1:** The operator can drag tasks between legal statuses, recorded as `actor=user:<id>`. When the orchestrator arrives in Phase 2+, stages that agents own will be locked from manual moves, apart from an explicit "override" action. Recommended: **yes**.
3. **Dev workflow on Windows:** full Docker with the repo in WSL2 (recommended), or the hybrid option (Docker for infra, native `api` and `web`).
4. **Git hosting and CI:** Should I `git init` here and set up GitHub Actions, targeting a GitHub repo you will create?
5. **Package managers:** `uv` for Python and `pnpm` for Node are recommended. Say if you prefer pip/poetry or npm.
