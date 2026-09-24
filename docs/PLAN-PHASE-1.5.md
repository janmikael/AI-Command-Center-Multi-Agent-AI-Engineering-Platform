# Implementation Plan: Phase 1.5 (Governance & Multi-Project Foundation)

Status: **Awaiting approval. No code written.**
Parent documents: [ARCHITECTURE.md §10](ARCHITECTURE.md#10-multi-project-workspace--global-ai-governance), [PHASE-1-REPORT.md](PHASE-1-REPORT.md)
Date: 2026-09-24

## Scope, as given

Implement only:
- multi-project workspace foundation
- Global AI Governance rules
- context management foundation

Do not implement: Claude Agent SDK, MCP gateway, sandbox runners, real AI agents.

## 0. Reading the scope precisely

Two things below are genuine judgment calls, not just implementation detail — flagging both before writing any code, per your instructions.

**0.1 "Multi-project workspace foundation" turns out to be almost entirely already built.** Phase 1 (M1/M2) already shipped `projects` and `repositories` tables, `create_project`/`add_repository` commands, `GET/POST /projects`, and — this is the part worth calling out — **task and event listing already accept `?project=<slug>`, and task creation already takes a `project` field.** I checked this just now rather than assume it. So "workspace foundation" needs no new backend work; what's missing is purely a UI to use more than the one seeded project. Requirement 6 ("UI only for governance") means that UI is explicitly out of scope this phase — so multi-project stays foundation-only, exactly as asked, and I'm not proposing any project-related backend work below.

**0.2 "Context management foundation" is the per-project knowledge base from ARCHITECTURE.md §10.1** ("projects contain knowledge/context"), **not** the `docs/context/` session-continuity idea from §11. That was explicitly scoped as a development-process convention for whichever AI works on this repo, not a product feature, and it stays that way — nothing here touches it. The DB-backed thing is `project_context_entries`, which is what requirement 3 is actually asking for.

Restating the three deliverables concretely:

| Scope item | What actually gets built |
|---|---|
| Multi-project workspace foundation | Nothing — already complete from Phase 1. Verified, not rebuilt. |
| Context management foundation | New: `project_context_entries` table + command/query/API layer. No UI (req. 6). |
| Global AI Governance rules | New: `governance_rules` table + command/query/API layer + the one UI page. |

## 1. Database changes

Two new tables. Nothing existing is altered — requirement 1 ("keep existing Phase 1 architecture intact") is met by construction: purely additive migrations, no `ALTER` on any Phase 1 table.

### Migration 0007 — `governance_rules`

```sql
CREATE TABLE governance_rules (
  id            uuid PRIMARY KEY,
  category      text NOT NULL,               -- security | tools | code | infrastructure
  title         text NOT NULL,                -- short label, e.g. "Paid tools require approval"
  statement     text NOT NULL,                -- the full human-readable rule
  effect        text NOT NULL,                -- allow | require_approval | deny | advisory
  match         jsonb NOT NULL DEFAULT '{}',  -- machine-checkable pattern, for a future gateway
  applies_to    text[] NOT NULL DEFAULT '{}', -- capabilities this targets; empty = all
  priority      int NOT NULL DEFAULT 100,     -- evaluation order for a future engine
  enabled       boolean NOT NULL DEFAULT true,
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (category IN ('security', 'tools', 'code', 'infrastructure')),
  CHECK (effect IN ('allow', 'require_approval', 'deny', 'advisory'))
);
CREATE INDEX ON governance_rules (category, enabled);
```

**Why this shape satisfies requirement 4** ("rules must be designed so a future MCP gateway can enforce them") **without building the gateway now:** `match` / `effect` / `priority` are deliberately the same three fields ARCHITECTURE.md §2.2 already gives the (not-yet-built) `policies` table. A future gateway can query `governance_rules` directly, or a trivial compiler can turn each row into one `policies` row — either path stays open. Nothing here needs redesigning later; it just isn't wired to anything that executes tool calls yet, because that thing doesn't exist yet.

**No hard delete.** Disabling a rule is a `PATCH` setting `enabled = false`, itself an event — consistent with the rest of the system, where history is never erased (tasks are cancelled, never deleted; events can't be updated or deleted at all). `match`/`applies_to`/`priority` exist in the schema now so the shape never needs to change later, but the UI (below) only edits `category` / `title` / `statement` / `effect` — the rest default and stay API-only for this phase, to keep the UI genuinely minimal per requirement 6.

### Migration 0008 — `project_context_entries`

```sql
CREATE TABLE project_context_entries (
  id            uuid PRIMARY KEY,
  project_id    uuid NOT NULL REFERENCES projects(id),
  kind          text NOT NULL,                -- architecture | convention | integration | gotcha | glossary
  title         text NOT NULL,
  body          text NOT NULL,                -- markdown, rendered as plain text for now (same call as task descriptions in Phase 1)
  priority      int NOT NULL DEFAULT 100,      -- render order within a project
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    uuid REFERENCES users(id),
  CHECK (kind IN ('architecture', 'convention', 'integration', 'gotcha', 'glossary'))
);
CREATE INDEX ON project_context_entries (project_id, priority);
```

Hard delete **is** supported here (unlike governance rules) — a context entry is closer to an editable note than an audit record, and the `project_context_entry.deleted` event (payload: id + title snapshot) still preserves the fact it existed, matching the append-only principle without needing the row itself to survive.

### What's deliberately *not* added

Per requirement 5 ("migrations only if required"), everything below from the original architecture sketch is skipped because nothing in this phase's scope needs it:

- `projects.status` (active/paused/archived) — not asked for this time; `archived_at` already exists and is untouched
- `policies.source_rule_id`, or the `policies` table itself — belongs to the MCP gateway, explicitly out of scope
- `artifacts` table, `artifacts.project_id` — belongs to agent runs, explicitly out of scope

## 2. Domain layer (pure, new files)

- `acc/domain/governance.py` — `RuleCategory` and `RuleEffect` enums (mirroring the CHECK constraints above, so an invalid value is a 422 at the API boundary, not a database error)
- `acc/domain/project_context.py` — `ContextEntryKind` enum
- `acc/domain/events.py` (extended, not replaced) — 7 new `EventType` members and their payload schemas:
  - `governance_rule.created`, `governance_rule.updated`, `governance_rule.disabled`, `governance_rule.enabled`
  - `project_context_entry.created`, `project_context_entry.updated`, `project_context_entry.deleted`

No changes to `task_status.py`, `agent_status.py`, or any existing domain module.

## 3. Commands (the only write path — same rule as everywhere else)

- `acc/commands/governance.py` — `create_rule`, `update_rule`, `set_rule_enabled` (used by both enable and disable)
- `acc/commands/project_context.py` — `create_entry`, `update_entry`, `delete_entry`

Each wraps its mutation and event in one `unit_of_work`, exactly like every Phase 1 command. No optimistic locking (`If-Match`) on either resource — a deliberate simplification: these are low-contention, single-operator-edited admin records, not something two people fight over mid-task the way a task's status can be. `PATCH` uses plain last-write-wins semantics.

## 4. Queries (read-only)

- `acc/queries/governance.py` — `list_rules(category?, enabled?)`, `get_rule(id)`
- `acc/queries/project_context.py` — `list_entries(project_slug)`

## 5. API endpoints

```
GET    /api/v1/governance-rules                    ?category=&enabled=
POST   /api/v1/governance-rules
GET    /api/v1/governance-rules/{id}
PATCH  /api/v1/governance-rules/{id}                title, statement, effect only
POST   /api/v1/governance-rules/{id}/enable
POST   /api/v1/governance-rules/{id}/disable

GET    /api/v1/projects/{slug}/context
POST   /api/v1/projects/{slug}/context
PATCH  /api/v1/projects/{slug}/context/{id}
DELETE /api/v1/projects/{slug}/context/{id}
```

All behind the same session + same-origin checks every `/api/v1` route already has. Registered alongside the existing routers in `create_app()` — no existing router's prefix or behavior changes.

**Decision point — access control.** ARCHITECTURE.md §10.5 says the Global Rules page should be admin-only. But no endpoint anywhere in Phase 1 currently checks `role`, and the one operator account that exists has `role = operator` with no way to become `admin` (the CLI's `create-operator` doesn't expose `--role`). Gating governance writes on `admin` right now would lock out the only account that exists. Two ways to resolve this, your call:

1. **(Recommended)** Leave governance endpoints open to any authenticated operator this phase, matching every other endpoint's current access model. Add real admin-gating later, once there's an actual way to become admin and a reason to (multi-user use).
2. Gate governance writes on `role = admin` now, and I also add `--role` to `create-operator` (or a `acc promote-operator` command) so you can make your account admin.

I'll go with (1) unless you say otherwise — it's one line to change later and keeps this phase consistent with itself.

## 6. Seed data

Extends the existing idempotent `acc seed` (never overwrites an edited/disabled rule, same guarantee it already gives agents) with the 13 rules from your original brief, verbatim, mapped to an `effect`:

| Category | Rule | Effect |
|---|---|---|
| Security | Never expose secrets | `deny` |
| Security | Never access protected credentials | `deny` |
| Security | Never bypass approval requirements | `deny` |
| Tools | Free tools can be used automatically | `allow` |
| Tools | Paid tools require approval | `require_approval` |
| Tools | Third-party services/APIs require approval before connection | `require_approval` |
| Tools | Unknown external tools require approval | `require_approval` |
| Code | Run tests before completing tasks | `advisory` |
| Code | Review changes before merge | `advisory` |
| Code | Never delete important files without approval | `require_approval` |
| Infrastructure | Production deployment requires approval | `require_approval` |
| Infrastructure | Database migrations require approval | `require_approval` |
| Infrastructure | Destructive commands require approval | `require_approval` |

`advisory` marks the two rules that aren't tool-call-shaped (they're process/workflow gates — "review before merge" isn't something a tool boundary can check). Per §10.3 these are the ones a future compiler would route to `workflow.exit_checks`, not `policies` rows; that compiler doesn't exist yet, so for now they're just visible, documented rules with no automated teeth — same honest state as everything else this phase (foundation, not enforcement).

## 7. UI changes

**Exactly one new page**, per requirement 6.

- New nav item: **Governance** (sidebar, alongside Overview/Agents/Tasks/Activity)
- Route `/governance`: rules grouped into the 4 category sections, each rule shown as a row with its `effect` badge (color-coded, same pattern as task-status badges), an enable/disable toggle, and the statement text
- "New rule" button → a dialog with exactly 3 fields: category (select), statement (textarea), effect (select). `match`, `applies_to`, `priority` are not exposed in the UI this phase — they default and stay API-editable only, matching the "foundation, not a full admin console" framing.

**Not built this phase, by design:** a project switcher, a context-entries editor, anything under `/projects/*`. The `project_context_entries` API exists and is tested; nothing in the UI calls it yet.

## 8. Testing plan

Same shape as every prior milestone:

- **Unit:** `RuleCategory`/`RuleEffect`/`ContextEntryKind` enums, event payload schemas (extending the existing catalogue tests)
- **Integration (real Postgres):** rule CRUD through the command layer with real events emitted; check constraints reject bad `category`/`effect`/`kind`; seed idempotency (rerunning `acc seed` never touches an edited rule, mirroring the existing agent-seed test); context entry CRUD scoped correctly to its project
- **API integration:** every new endpoint, happy path + at least one failure (bad category, unknown rule id, disabling twice, cross-project isolation for context entries)
- **Frontend:** the Governance page — list renders grouped by category, create dialog, enable/disable toggle — plus a unit test for the effect-badge color mapping
- **Live verification** through the running Docker stack, same as every phase so far, including confirming the 13 seed rules actually land correctly and `acc seed` stays idempotent against them

## 9. Migration impact summary

| | |
|---|---|
| New tables | `governance_rules`, `project_context_entries` |
| Altered tables | none |
| New indexes | 2 (one per new table) |
| Downgrade | both migrations drop cleanly (no other table references either new one) |
| Existing data | untouched |
| Existing API/UI behavior | unchanged — purely additive routes and one new nav item |
| Import-linter / architecture contracts | no changes needed — new modules land inside the already-covered `acc.domain` / `acc.commands` / `acc.queries` / `acc.api` packages |

## 10. Explicitly out of scope (unchanged from your instructions)

Claude Agent SDK, MCP gateway, sandbox runners, real AI agents, the `policies`/`workflows`/`approvals` tables, project switcher UI, context-entry UI, `docs/context/` files.

---

Waiting for your approval before writing any code. The one open call is §5's access-control decision (recommendation: option 1, open to any operator for now) — say if you want option 2 instead, otherwise I'll proceed with the recommendation.
