# Phase 1 Completion Report: Dashboard Core

- Status: **Complete.**
- Verified against: commit `ece39ec`, on the production-like images from `docker compose up -d --build --wait`.
- Scope: [PLAN-PHASE-0-1.md §4 Phase 1](PLAN-PHASE-0-1.md)

## Summary

Phase 1 delivers the working dashboard: the operator creates a task, assigns it, drags it across a live Kanban board, watches its status history and activity build up, and sees all of that happen in a second browser tab with no reload — through a real Redis-backed WebSocket, not a shortcut. Four milestones, each committed and verified independently:

| Milestone | Commit | What it added |
|---|---|---|
| M1 | `abf59a6` | Data model: projects, repositories, agents, tasks, append-only events; the task state machine; the `UnitOfWork` that writes state and events atomically |
| M2 | `3641be3` | The REST API: 18 endpoints for tasks, agents, projects, events, overview — every write going through `acc.commands`, optimistic locking, no way to set status or progress except through their own guarded paths |
| — | `02fb43c` | Architecture-only: designed (not built) Phase 1.5 governance/multi-project, approved, deliberately not implemented this phase |
| M3 | `5955bba` | WebSocket realtime: a real Redis pub/sub fan-out across processes, resume-with-overlap, resync, and `acc simulate-activity` for testing the live dashboard before any agent exists |
| M4 | `ece39ec` | Every frontend page, wired to the above: Overview, Agents, Agent detail, Tasks Kanban, Task detail, Activity feed, and the `RealtimeProvider` connecting all of them |

## What's live

- **Data and state machine (M1):** the full schema with the append-only `events` table enforced by a database trigger — not just application code — plus indexes and check constraints matching the design.
- **API (M2):** every task mutation is optimistically locked (`If-Match`), and status can only change through `POST /transition`, validated against the same state machine used everywhere else. `PATCH` explicitly rejects any attempt to set `status`, `assigned_agent_id` or `progress_pct` — 422, not a silent no-op.
- **Realtime (M3):** `RedisPublisher` → Redis pub/sub → each API process's own `Hub`, exactly the multi-process design in the architecture doc. Verified with two independent FastAPI instances against the same Postgres/Redis, so the test exercises the real cross-process path, not an in-process shortcut.
- **Frontend (M4):** every WebSocket event only triggers a TanStack Query invalidation — it never writes query data directly. REST stays the single source of truth, per your instruction for this milestone. The Kanban board's drag-and-drop reads each task's own server-computed `allowed_transitions` to decide, live, whether a drop is legal, and rejects it visibly if not.

## Verification

| Suite | Result |
|---|---|
| Backend (unit + integration, real Postgres/Redis) | 286 passed |
| Frontend (unit) | 51 passed |
| Backend: ruff, mypy, all 3 import-linter architecture contracts | clean |
| Frontend: ESLint, `tsc --noEmit`, production `next build` (all 8 routes) | clean |
| Live, through Caddy, real Microsoft Edge | 20/20 (Phase 1 frontend) + 12/12 (Phase 0 smoke) |

The live pass included things a unit test can't: an actual mouse-driven Kanban drag (both a successful move and a rejected illegal one), the full transition/assign/edit round trip, and — the actual point of the milestone — a change made in one browser tab appearing in a second tab with no reload.

## Bugs found and fixed during this phase

None of these were staged; all were caught by building against the real stack and are already fixed and covered by tests.

| # | Found in | Bug | Fix |
|---|---|---|---|
| 1 | M3 | Login crashed (500) if `request.client.host` wasn't a parseable IP — true of Starlette's `TestClient`, and of any misbehaving upstream proxy in production, since `sessions.ip` is a Postgres `inet` column | `_client_ip()` validates and falls back to `null` instead of failing the request |
| 2 | M4 | A draggable `<a>` card, transformed in place with no `DragOverlay`, still had its native link navigation fire on drop — the drag succeeded server-side, but the browser also navigated away | Render a non-interactive `DragOverlay` preview instead of moving the source element; the real link is never under the cursor at drop time |
| 3 | M4 | Base UI's `<Select.Value>` shows the raw value by default (an agent's slug, a filter's `__all__` sentinel), not the matching item's label | Every `<Select>` in the app now passes an explicit label-rendering function |

## Scope decisions (documented, not silent deviations)

A few checklist items were deliberately simplified to keep Phase 1 focused on the core loop (create → assign → move → watch it live), each a reasonable, callable-out trade-off rather than a corner cut silently:

- **Description rendering** is plain text (`white-space: pre-wrap`), not markdown. No agent exists yet to produce markdown, and React already escapes it safely; adding a markdown+sanitize pipeline for operator-typed plain text isn't worth the dependency yet.
- **Acceptance criteria** are read-only on the task detail page (checkboxes reflect `done`, no add/remove/toggle UI). The API supports full editing already; only the UI for it is deferred.
- **Activity feed type filter** is a single-select, not multi-select.
- **"Infinite scroll"** is implemented as a "Load more" button (an accessible, common variant of the same pattern) rather than scroll-position-triggered loading.
- **The WebSocket subscribes to the single global `"activity"` topic** for the whole app lifetime, rather than per-page `task:`/`agent:` topics. Since `"activity"` already matches every event server-side, this is fully correct — just less bandwidth-efficient than the finer-grained subscriptions the protocol also supports. Worth revisiting if the event volume grows much past single-operator scale.

## Phase 1 definition of done (from the plan)

| Requirement | Met |
|---|---|
| Create a task, assign it, drag it Backlog → Planning → Working → Review → Completed, see every change as an event in both the task history and the activity feed | Yes — verified live |
| A second browser tab updates live with no refresh | Yes — verified live, the actual point of M4 |
| The simulator makes agents appear as `working` or `idle` | Yes — verified live in M3 |
| `UPDATE events …` as `acc_app` fails | Yes — verified in M1 (database-enforced, not just application code) |

## Reproducing the checks

```bash
docker compose up -d --build --wait
scripts/smoke.sh                    # Phase 0: auth, session, CSRF
scripts/test-backend.sh             # 286 backend tests
cd apps/web && pnpm test            # 51 frontend tests
```

## What's next

Phase 1 is done. Two paths are open, both already scoped:

- **Phase 1.5** (approved design, not built): multi-project workspaces and Global AI Governance rules — see [ARCHITECTURE.md §10](ARCHITECTURE.md#10-multi-project-workspace--global-ai-governance).
- **Phase 2**: the first real agent (Claude Agent SDK), which is what the realtime dashboard, the simulator, and the governance design have all been building toward.
