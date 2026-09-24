# Phase 1.5 Completion Report: Global AI Governance + Project Context Foundation

- Status: **Complete.**
- Commit: `08db9486260b78be89a04b0e79ffeb7e7259d0f2`
- Verified against: production-like images from `docker compose build api web migrate` + `docker compose up -d --force-recreate`, through Caddy.
- Scope: [PLAN-PHASE-1.5.md](PLAN-PHASE-1.5.md), approved decisions in the request that authorized this phase.

## Summary

Phase 1.5 adds two additive tables — `governance_rules` and `project_context_entries` — plus CRUD APIs and a Governance UI page, without touching a single line of Phase 1's existing schema, commands, or routers. The multi-project foundation Phase 1 already had (tasks and events already filter by `?project=`) needed no new work, exactly as scoped in the plan. Global AI Rules apply identically to every project; the 13 rules from the original governance brief are seeded automatically and idempotently, same pattern as the Phase 1 agent seed.

## What's live

- **`governance_rules` table** (migration `0007`): category (security/tools/code/infrastructure), title (unique), statement, effect (allow/require_approval/deny/advisory), plus `match`/`applies_to`/`priority` columns left API-invisible this phase but already shaped to match ARCHITECTURE.md §2.2's future `policies` table — so a later MCP gateway can enforce these rules with no schema redesign.
- **`project_context_entries` table** (migration `0008`): per-project knowledge base (architecture/convention/integration/gotcha/glossary), FK'd to `projects`, hard-deletable (unlike governance rules, which are soft-disable only — see PLAN-PHASE-1.5.md for why the two follow different delete semantics).
- **CRUD APIs**: `GET/POST /governance-rules`, `GET/PATCH /governance-rules/{id}`, `POST /governance-rules/{id}/enable|disable`; `GET/POST /projects/{slug}/context`, `PATCH/DELETE /projects/{slug}/context/{id}`. Every write goes through `acc.commands`, same `UnitOfWork` discipline as every Phase 1 write path — state change and event emission in one transaction.
- **Access control**: open to any authenticated operator this phase, per your explicit decision. `acc/api/routers/governance.py` carries the required forward-looking note: *"Governance administration should become admin-only when multi-user access and AI agents are introduced."*
- **Realtime**: governance/context events (`governance_rule.*`, `project_context_entry.*`) flow through the existing `activity` topic — no hub changes needed. The frontend's `queryKeysToInvalidate` was extended to match on event `type` (these events carry neither `task_key` nor `agent_slug`, the two fields it previously keyed off).
- **Governance UI**: `/governance` page, rules grouped by category, effect badges, enable/disable toggle, create-rule dialog (category/title/statement/effect only). No project-switcher UI and no context-entry editor UI, per your explicit "do not implement" list.

## What was explicitly not built (per your scope decision)

MCP gateway, AI agents, context automation, project context editor UI, approval execution system — none of these were touched.

## Verification

| Suite | Result |
|---|---|
| Backend (unit + integration, real Postgres/Redis) | 317 passed (was 286; +31 new: migration/constraint tests, 25 governance+context API tests) |
| Frontend (unit) | 58 passed (was 51; +7 new: invalidation-by-event-type, effect/category badge mapping) |
| Backend: ruff, mypy | clean |
| Frontend: ESLint, `tsc --noEmit`, production `next build` (9 routes incl. `/governance`) | clean |
| Live, through Caddy (rebuilt `api`/`web`/`migrate` images, fresh `docker compose up`) | migration + seed ran clean (13 rules created); login; `GET /governance-rules`; `GET /projects/acc/context`; full create→disable round trip on a live rule (201 → 200); `/governance` page renders server-side with real data |

New backend tests cover: CHECK-constraint violations for `category`/`effect`/`kind`, `governance_rules.title` uniqueness, `project_context_entries` FK integrity, seed idempotency (including "never overwrites an operator-edited rule," matching the existing agent-seed pattern), and the full governance/context API surface — happy paths, 404s on unknown ids, 422s on invalid category/effect/kind, double-disable no-op, and cross-project isolation of context entries.

## A bug caught by testing, fixed before commit

`commands/governance.py::create_rule` and `update_rule` inserted directly without checking for a duplicate title first — unlike `commands/projects.py`, which pre-checks and raises a typed `Conflict`. A duplicate-title test (`test_duplicate_title_is_409`) caught that this would have surfaced as an unhandled `IntegrityError` → 500, not the intended 409. Fixed by adding the same pre-check pattern `create_project` already uses, for both create and title-changing patches.

## Notes for the future

- The admin-gating note is now in code (`acc/api/routers/governance.py`); implementing it is future work, not forgotten.
- `governance_rules.match`/`applies_to`/`priority` exist in the schema and are seeded with safe defaults, but have no API surface yet — intentional, so the MCP gateway phase can wire them up without another migration.
