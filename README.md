# AI Command Center

A private control plane for monitoring and coordinating a team of AI agents: an architect, a developer and a designer, with more to come.

- Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Current plan: [docs/PLAN-PHASE-0-1.md](docs/PLAN-PHASE-0-1.md)
- Decisions: [docs/adr/](docs/adr/)

**Status:** Phase 0 (foundation) is complete: auth, API skeleton, dashboard shell and Docker stack. Phase 1 (agents, tasks, events, realtime) is next.

## Stack

| Layer | Tech |
|---|---|
| Web | Next.js 16 (App Router), Tailwind v4, shadcn/ui (Base UI) |
| API | FastAPI, SQLAlchemy 2 (async), Alembic, structlog |
| Data | PostgreSQL 16 (source of truth), Redis 7 (rate limits, pub/sub only) |
| Edge | Caddy (single origin: `/api/*` → API, everything else → web) |
| Tooling | uv (Python), pnpm (Node), Docker Compose |

## Getting started (Windows + WSL2)

The repository must live on the **WSL filesystem** (for example `~/ai-command-center`), not under `/mnt/c`. Docker Desktop's WSL integration must be enabled for your distro.

```bash
# 1. One-time tools (no sudo needed for these)
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv
# Node 22 + pnpm: any method works; pnpm via `corepack enable pnpm`
sudo apt-get install -y make                        # optional: enables the Makefile shortcuts

# 2. Secrets (writes .env with random values, never committed)
scripts/init-env.sh

# 3. Start everything
docker compose up -d --build --wait                 # or: make up

# 4. Create your operator account (prompts for a password, min. 12 characters)
docker compose exec api acc create-operator --email you@example.com --name "Your Name"

# 5. Open http://localhost
```

To open the project in VS Code, run `code .` from WSL inside the repo. That uses the WSL remote.

## Everyday commands

| Task | Command |
|---|---|
| Dev mode (hot reload for api and web) | `make dev` · `docker compose -f compose.yml -f compose.dev.yml up -d --build --wait` |
| Stop | `make down` |
| Logs | `make logs` |
| Backend tests (starts dev Postgres/Redis) | `make test-backend` · `scripts/test-backend.sh` |
| Web tests | `make test-web` · `cd apps/web && pnpm test` |
| Lint, types, architecture contracts | `make lint` |
| End-to-end smoke test | `make smoke` · `scripts/smoke.sh` |
| Regenerate the typed API client | `make gen-client` |

Dev mode runs `next dev` from your checkout, so run `pnpm install` in `apps/web` first.

## Layout

```
backend/            FastAPI app (package `acc`), Alembic migrations, tests
  src/acc/domain/     pure rules, no I/O (enforced by import-linter)
  src/acc/commands/   the only write path (future orchestrator command layer)
  src/acc/queries/    read models
  src/acc/api/        HTTP layer: routers, schemas, deps, problem+json errors
apps/web/           Next.js dashboard
infra/              Caddyfile, Postgres init (roles)
scripts/            env generation, test runner, smoke test
compose.yml         full stack · compose.dev.yml hot reload · compose.prod.yml production
```

## Architecture rules (enforced, not just documented)

- **Only `acc.commands` writes.** API routers can't import ORM models (`lint-imports`).
- **The domain is pure.** `acc.domain` can't import SQLAlchemy, FastAPI or Redis.
- **Least-privilege database roles.** Migrations run as `acc_owner`, and the API runs as `acc_app` with data access only.
- **No secrets in git.** `.env` is generated locally, and production refuses to start with placeholder secrets.

## Ports

Only Caddy publishes ports (80/443). In dev mode, Postgres (`127.0.0.1:5432`) and Redis (`127.0.0.1:6379`) are also exposed on localhost for tests and `psql`. If port 80 is taken on Windows, set `ACC_HTTP_PORT=8080` **and** `ACC_PUBLIC_ORIGIN=http://localhost:8080` in `.env`.

**Known conflict: host port 8000.** On the primary dev machine, host port 8000 is already taken by another Docker project (`emega-sync-service`, a PHP app). The Command Center is not affected: the API listens on 8000 *inside* the Docker network and is never published, and all traffic goes through Caddy on port 80. So `curl http://localhost:8000` reaches that other app, not this API. If you ever need to publish the API directly for debugging, bind it to a free port such as `127.0.0.1:8001:8000`.
