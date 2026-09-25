# Execution Plan: Phase 2 M2 (Runner Infrastructure)

Status: **Awaiting approval. No code written.**
Parent documents: [PLAN-PHASE-2.md](PLAN-PHASE-2.md) (§1, §3, §4), [ARCHITECTURE.md](ARCHITECTURE.md) (§1.2 component diagram, §3.5 internal APIs)
Builds on: commit `9c9f21e` (Phase 2 M1 — `agent_runs`, `jobs`, the run state machine, `commands/runs.py`, the SKIP LOCKED scheduler)
Date: 2026-09-25

## Scope, as given

Implement only runner infrastructure:
- runner Docker image
- sandbox container lifecycle
- docker-socket-proxy
- launcher
- internal runner communication API
- heartbeat reporting
- cancellation support

Do not implement yet: Claude Agent SDK, repository access, git operations, real code modification, production credentials.

## 0. Reading the scope precisely

**0.1 No new migration.** `agent_runs` (M1) already has every column this milestone needs — `container_id`, `workspace_ref`, `cancel_requested_at`, `last_heartbeat_at`, `current_step`. M2 is the first milestone to actually *populate* them, not the first to need them.

**0.2 Since there's no Claude Agent SDK yet, the runner in M2 does almost nothing.** It starts, sends heartbeats on an interval, watches for a cancellation signal, and after a short fixed delay calls back to report success — proving the full round trip (launch → heartbeat → report → cleanup) with zero real capability behind it. This is deliberate: it's the cheapest possible way to validate the sandbox, the network isolation, and the internal auth boundary *before* any real secret (an Anthropic API key, git credentials) or any real capability (shell, file writes) enters the picture. M3 replaces the runner's body, not its shell.

**0.3 "Cancellation support" is a real, working feature this milestone, not a stub.** Because the runner does nothing risky yet, cancellation doesn't depend on the runner behaving well — the orchestrator can always reach in through docker-socket-proxy and stop the container directly, regardless of what's happening inside it. That's the property M2 needs to prove now, while it's still cheap to test, because M3 onward the container will be running less predictable things.

**0.4 "Preserve governance boundaries" means two things here, not one.** (a) Nothing in M2 touches `governance_rules` or weakens Phase 1.5's model — the internal API surface added is scoped to run lifecycle only (heartbeat, complete, fail), not general data access. (b) The sandbox constraints that *will* back governance enforcement once there's a tool loop to enforce (no DB credentials, no docker socket inside the runner, network egress limited to the API) are built now, correctly, even though there's nothing yet that would misuse them. Building the boundary before there's anything dangerous behind it is the point, not a coincidence.

**0.5 "Preserve existing run lifecycle state machine" means the internal API is a thin HTTP wrapper over M1's `commands/runs.py`, nothing more.** `RunStatus` and its transition table are unchanged. No new run statuses. The launcher and the internal API call the existing `mark_run_starting`, `record_heartbeat`, `complete_run`, `fail_run`, `mark_cancelled` functions verbatim.

**0.6 Heartbeat-loss *detection* (a run that goes silent and is never explicitly cancelled) is explicitly out of scope here, per PLAN-PHASE-2.md's own milestone split.** That's M5's watchdog. M2's cancellation is always operator-initiated. A silently-dead run in M2 stays visibly stuck until either it eventually calls back or the operator cancels it manually (which works, because cancellation doesn't depend on the runner's cooperation — see §0.3).

## 1. Services added

| Service | What it is | Network |
|---|---|---|
| `docker-socket-proxy` | `tecnativa/docker-socket-proxy`, the *only* container with access to the real `docker.sock` | `internal` only |
| `orchestrator` | New compose service — reuses the `acc-api:local` image, different `command:` (`acc.orchestrator.main`, a new asyncio entrypoint wrapping M1's `scheduler.run_once` in a poll loop) | `internal` (DB/Redis/docker-socket-proxy) — never `runner-egress` |
| runner image | New `images/runner/Dockerfile` — minimal Python, non-root, no OS package manager left installed at runtime | n/a (not a compose service; launched per-run by the orchestrator) |

**Not a new service:** the internal API lives inside the existing `api` container (`acc/api/internal/`), not a separate deployable — consistent with ADR-1 ("one Python codebase, separate processes" means processes, not a proliferation of containers for every concern).

**Network topology change:** a new `runner-egress` network, joined by every runner container and by `api` (so runners can reach the internal API), never by `postgres` or `redis`. `api` keeps its existing `internal` membership too (unchanged). This is the concrete implementation of PLAN-PHASE-2.md §1.8.

```
runner-egress:  [api]  [runner (ephemeral)]
internal:       [api]  [orchestrator]  [docker-socket-proxy]  [postgres]  [redis]
```

A runner container is never on `internal`, so even a fully compromised runner has no network path to Postgres or Redis — this is enforced by Docker's own network isolation, not by an application-level check.

New Python dependency: `aiodocker` (async Docker Engine API client, pointed at `docker-socket-proxy` instead of the real socket — consistent with the rest of the stack being async throughout).

## 2. Security model

**2.1 docker-socket-proxy scoping.** The proxy's own env config allows only the container-lifecycle verbs the launcher needs, nothing else:

```yaml
CONTAINERS: 1   # create / start / stop / kill / inspect / remove
POST: 1         # without this the proxy is read-only
IMAGES: 0
NETWORKS: 0     # attaching to an existing network by name in the create payload
                # doesn't require network-management permissions
VOLUMES: 0
EXEC: 0         # the orchestrator can never exec into a running runner
BUILD: 0
COMMIT: 0
SWARM: 0
SERVICES: 0
# (the remaining categories the proxy supports — SECRETS, NODES, PLUGINS,
#  SESSION, DISTRIBUTION, AUTH, INFO, SYSTEM — all default to 0/off too)
```

Exact flag names get pinned to a specific proxy version during implementation; the intent (container lifecycle only, nothing else) is what's load-bearing here.

**2.2 The runner image is not attacker-selectable.** The launcher hardcodes the image reference from `Settings.runner_image` (default `acc-runner:local`); nothing in a job payload or a task/run row can influence which image gets launched. Same principle as the API never trusting a client-supplied SQL fragment — the "what code runs" decision is a server-side constant.

**2.3 Runner container hardening**, matching the pattern already used for `api`/`web` in `compose.yml`, extended with resource ceilings since this container runs less-trusted code:

```
read_only: true          # root filesystem is read-only
tmpfs: [/tmp, /workspace] # the only writable paths — /workspace is discarded on exit
cap_drop: [ALL]
security_opt: ["no-new-privileges:true"]
user: non-root (baked into the image, not overridable at launch)
network: runner-egress only (never internal)
mem_limit / cpus / pids_limit: set at container-create time by the launcher
no Docker socket, no host mounts beyond the tmpfs workspace
```

**2.4 The internal API's authentication is a per-run, self-contained signed token — not a session cookie, not a full JWT library.** Consistent with this codebase's existing session-token pattern (`acc/security.py::session_id_for`, HMAC-based, no new crypto dependency), a run token is:

```
base64url(json({"run_id": ..., "agent_id": ..., "exp": <unix ts>})) + "." + hmac_sha256_hex(...)
```

verified with `hmac.compare_digest`, signed with a **new, separate** secret (`Settings.runner_token_secret`) — deliberately not the same secret that protects operator sessions, so a leak of one doesn't compromise the other. Minted once by the launcher at container-create time, with a bounded TTL (`Settings.run_token_ttl_seconds`, default 3600s) — a safety bound on the credential's own lifetime, independent of (and simpler than) M5's future wall-clock watchdog.

**2.5 Token scope is enforced per request, not just at login.** The internal API's auth dependency checks that the token's `run_id` claim matches the `{run_id}` in the URL path — a valid token for run A can't be replayed to write events onto run B. This is the exact property PLAN-PHASE-2.md's original testing strategy called for and gets a dedicated test (§5).

**2.6 `/internal/*` is unreachable from outside the Docker network, in two independent ways** (defense in depth, not redundant caution — either one failing shouldn't matter):
- Caddy's routing table only forwards `/api/*`, `/ws`, `/ws/*`, `/healthz`, `/readyz` to `api`. `/internal/*` isn't in that matcher, so it's unreachable through the public entrypoint even before the token check runs. I'll add an explicit `respond /internal/* 404` block to the Caddyfile so this is asserted, not just an absence.
- The token check itself is the real boundary regardless of Caddy: no valid Bearer token, no access, full stop. `/internal/*` gets no `require_same_origin` dependency (unlike `/api/v1/*`) — it's server-to-server, not browser-originated, so CSRF defenses don't apply and would be the wrong tool here.

**2.7 What the runner does *not* get, even now:** no database credential, no Redis credential, no session secret, no Anthropic API key (nothing to do with it yet), no git credential (nothing to push yet). The only secret a runner container holds in M2 is its own run token, scoped to itself, expiring within the hour. This is the smallest possible secret-bearing footprint the architecture will ever have — every later phase only adds to it, never starts smaller.

## 3. Container lifecycle

All of it flows through M1's job queue (ADR-2) — no separate polling loop, reusing the scheduler infrastructure already tested under concurrency in M1.

### 3.1 Launch

```
transition_task (existing, M1) → enqueue_start_run_in_uow → Job(kind="start_run")
                                                                    │
scheduler.run_once claims it ──────────────────────────────────────┘
                    │
        launcher.handle_start_run(claimed_job)
                    │
        1. Load the run (acc.queries.runs.get_run)
        2. If run.container_id is already set → a retried job found a container
           from a prior attempt; skip creation, just confirm state (idempotency
           guard — see §3.4)
        3. If run.cancel_requested_at is set → the operator cancelled before we
           got to it; call commands.runs.mark_cancelled directly, no container
           to create
        4. Otherwise: mint a run token, build the container spec (image, env:
           RUN_ID, AGENT_ID, RUN_TOKEN, API_BASE_URL, HEARTBEAT_INTERVAL_S;
           resource limits; runner-egress network only), create + start it via
           aiodocker → docker-socket-proxy
        5. commands.runs.mark_run_starting(run_id, container_id)
        6. On any Docker-side failure: commands.runs.fail_run(run_id, reason=...)
```

Inside the container, the M2 runner entrypoint (no SDK, no adapters — just a loop):

```
loop every HEARTBEAT_INTERVAL_S seconds:
    POST /internal/runs/{run_id}/heartbeat  → { cancel_requested: bool }
    if cancel_requested: break               # graceful self-exit; see §3.3
after a short fixed delay (proves the round trip, nothing more):
    POST /internal/runs/{run_id}/complete  { result_summary: "M2 smoke run OK" }
    exit 0
```

### 3.2 Heartbeat

`POST /internal/runs/{run_id}/heartbeat` calls M1's `commands.runs.record_heartbeat` verbatim (first heartbeat flips `STARTING → RUNNING`, matching the state machine unchanged per §0.5) and returns whether a cancellation has been requested, read from the same row. No new command needed — `record_heartbeat` already updates `last_heartbeat_at`; the internal API just also reads `cancel_requested_at` back out via `acc.queries.runs.get_run` to answer the runner.

### 3.3 Cancellation

Two cooperating mechanisms, only one of which is load-bearing:

- **Authoritative (works even if the runner is unresponsive or hostile):** `POST /api/v1/runs/{id}/cancel` (new, operator-facing, session-cookie-authenticated like every other `/api/v1/*` route) calls a small addition to M1's `commands.runs.request_cancel` — it already sets `cancel_requested_at`; this milestone adds enqueuing a `Job(kind="cancel_run")` in the same transaction, so cancellation flows through the same durable queue as everything else. `launcher.handle_cancel_run` then: loads the run; if already terminal, no-ops (the run may have finished on its own in the race); if `container_id` is still `None`, calls `mark_cancelled` directly (§3.1 step 3's counterpart for a run cancelled after M2's own launch path already handled the not-yet-launched case, kept for the manual-cancel entry point too); otherwise stops the container (`SIGTERM`, short grace period), kills it if still running after the grace period, confirms it's gone via inspect, then calls `commands.runs.mark_cancelled`.
- **Cooperative (a nicety, not required):** the runner sees `cancel_requested: true` in its next heartbeat response and exits early on its own, which just means the orchestrator's `stop` finds an already-exited container instead of having to wait out the grace period or fall back to `kill`.

### 3.4 Idempotency

Job handlers can retry (M1's scheduler retries a failed job up to `MAX_ATTEMPTS`). Both handlers are written to be safe to re-run:
- `handle_start_run`: checks `run.container_id is None` before creating a container (§3.1 step 2).
- `handle_cancel_run`: checks `RunStatus(run.status) not in TERMINAL` before doing anything (a retry after a successful cancel is a no-op, not a second stop attempt against a container that's already gone).

### 3.5 Cleanup

The launcher issues an explicit container removal (`DELETE`, via docker-socket-proxy) once a run reaches a terminal state and its container has been confirmed stopped — not `AutoRemove: true` at create time, which would remove the container before the orchestrator gets a chance to inspect its exit status.

## 4. APIs

**Internal** (new package `acc/api/internal/`, Bearer-token authenticated per §2.4–2.5, mounted on the existing `api` app, not a new service):

```
POST /internal/runs/{run_id}/heartbeat   { current_step?: string }  → { cancel_requested: bool }
POST /internal/runs/{run_id}/complete    { result_summary: string }
POST /internal/runs/{run_id}/fail        { reason: string }
```

Deliberately **not** built yet: `GET /internal/runs/{id}/context` (nothing to render — no adapter, no context assembly until M3), `POST .../events` batch and `POST .../usage` (no tool calls or token usage exist yet). Adding these now would be building an API ahead of any caller, which this project has consistently avoided (see M1's own note on deferring `record_tool_call`/`record_usage`).

**Operator-facing** (new `acc/api/routers/runs.py`, session-cookie authenticated, same pattern as every existing router):

```
GET  /api/v1/runs?task_id=&agent_id=&status=   → list (acc.queries.runs.list_runs, M1)
GET  /api/v1/runs/{id}                          → detail (acc.queries.runs.get_run, M1)
POST /api/v1/runs/{id}/cancel                   → the authoritative half of §3.3
```

`GET` endpoints are read-only wrappers over queries M1 already built and are what makes this milestone verifiable end-to-end (curl, and the test suite) without waiting for M4's UI. Not built yet: `/runs/{id}/events`, `/tool-calls`, `/file-changes` — no data exists behind them until M3.

**Settings additions** (`acc/config.py`, following the existing `ACC_`-prefixed pattern):

```
runner_token_secret: SecretStr        # separate from session_secret, §2.4
run_token_ttl_seconds: int = 3600
runner_image: str = "acc-runner:local"
runner_network: str = "acc_runner-egress"
docker_proxy_url: str = "tcp://docker-socket-proxy:2375"
```

## 5. Tests

**Domain/unit (no Docker, no Postgres needed):**
- Run-token minting and verification: valid round-trip, tampered payload rejected, tampered signature rejected, expired token rejected, wrong-run-id-for-path rejected.
- Launcher's pure decision logic (container spec construction, idempotency branch selection) tested against a fake/mocked `aiodocker`-shaped client — no real Docker needed for the "what would we ask Docker to do" logic.

**Integration, against the real dev stack (`docker-socket-proxy` included — this is exactly the kind of thing this project has consistently refused to mock, per Phase 1 M3's cross-process WebSocket precedent):**
- End to end: enqueue a `start_run` job → scheduler claims it → launcher creates a *real* container from the built runner image → the container's own heartbeat loop reaches the internal API → `agent_runs.status` observably becomes `RUNNING` → the container's completion call moves it to `SUCCEEDED` → the container is confirmed removed (`docker ps` / inspect 404).
- Isolation is asserted, not assumed: from inside a launched runner container, a connection attempt to `postgres`/`redis` fails (connection refused / no route) — mirrors the network-isolation test already written for `docker-socket-proxy` access in PLAN-PHASE-2.md's original M2 testing strategy.
- Cancellation: launch a run whose entrypoint sleeps well past the test's patience, call the cancel endpoint, confirm the container is actually stopped (not just that the DB row says `cancelled`) within one grace-period window.
- Cancel-before-start race: set `cancel_requested_at` on a still-`QUEUED` run (simulating a cancel that lands before the scheduler gets to it), confirm the launcher never creates a container and the run lands in `CANCELLED`.
- Idempotency: manually re-invoke `handle_start_run` on a job whose run already has a `container_id` set, confirm no second container is created.
- Internal API auth: missing token → 401; expired token → 401; well-formed token for a different `run_id` than the URL → 403; tampered token → 401.
- `/internal/*` is unreachable through Caddy (a request through the public origin gets the Caddyfile's explicit 404, not a proxied response).
- Operator-facing `GET /api/v1/runs`, `GET /api/v1/runs/{id}`, `POST /api/v1/runs/{id}/cancel`: the same auth/CSRF/not-found conventions every other router in this codebase is already tested against (session required, same-origin required for the mutating one, 404 on an unknown id).

**Live verification** (same bar as every prior milestone): rebuild all images including the new runner image, bring up `docker-socket-proxy` and `orchestrator` fresh, assign a task to a runnable agent, watch a real container appear (`docker ps`), heartbeat, complete, and disappear — then repeat with a manual cancel mid-flight and confirm the container is actually gone, not just marked cancelled in the database.

## Open items carried from the top-level plan

§7.1 (repository hosting and git credentials) still isn't needed for M2 — nothing here touches git. It becomes load-bearing at M3, when the runner's body stops being a stub.

Waiting for your approval on this M2 plan before writing any code.
