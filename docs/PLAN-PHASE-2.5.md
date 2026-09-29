# Execution Plan: Phase 2.5 (Expose the Agent Execution Lifecycle in the Dashboard)

Status: **Awaiting approval. No code written.**
Parent documents: [ARCHITECTURE.md §3, §6](ARCHITECTURE.md), [PHASE-1-REPORT.md](PHASE-1-REPORT.md), [ARCHITECTURE-REVIEW-2026-09.md §4.8](ARCHITECTURE-REVIEW-2026-09.md)
Builds on: commit `d851388` (architecture review)
Date: 2026-09-29

## Scope, as given

Expose the existing AI execution lifecycle in the frontend before enabling real AI production usage. Runs page, run detail page, tool call timeline, agent messages, usage/cost display, file changes viewer, error details, governance denial visibility. Explain frontend changes, backend endpoints needed, security considerations, relationship to future real AI usage. Do not implement.

## 0. Reading the scope precisely

**0.1 This directly follows from the architecture review's own largest finding**, not a fresh idea: "the frontend has zero Phase 2 surface... everything built since M1 is currently visible only through the API, not the dashboard" (ARCHITECTURE-REVIEW-2026-09.md §4.8). Naming this "Phase 2.5" rather than folding it into M4.3 or M4.4 is the right call — it's a real, separate concern (dashboard work, not agent-execution work) and matches the precedent Phase 1.5 already set for a between-phases milestone that's additive and doesn't touch what came before.

**0.2 "Before enabling real AI production usage" is the reason this is happening now, and it changes what "done" means here.** This isn't a nice-to-have UI pass — it's the visibility layer the M4.3 live-verification pause is implicitly waiting on. Once a real Anthropic API key is configured and a real agent starts spending real money and touching real code, an operator needs to be able to watch that happen without dropping to `curl`. Everything in this plan is scoped to *observing* what the backend already does — nothing here adds a new execution capability, and nothing here is required for M4.3's live run to technically succeed. It's required for that run to be something a human can actually supervise.

**0.3 Almost everything needed on the backend already exists — checked against the real routers, not assumed.** `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/tool-calls`, `GET /runs/{id}/usage`, `GET /runs/{id}/file-changes`, `POST /runs/{id}/cancel` are all live today (M2–M4.2). `RunOut` already carries `error` (a JSONB the runner populates with `category`/`stop_reason`/`api_error_status` — M4.2) and every token/cost field. `ToolCallOut` already carries `rule_id`/`blocked_by_effect` — the exact fields a "governance denial visibility" view needs. This plan's backend section (§2) is short because of that; it's real, not a placeholder.

**0.4 The realtime mechanism needs no protocol change, following Phase 1's own established pattern exactly.** Every run/tool-call/usage event already carries `task_key` and `agent_slug` (PLAN-PHASE-2.md §0.5, confirmed still true by reading `commands/runs.py::_emit_run`), so it already reaches the existing global `"activity"` topic every connected client already subscribes to (`realtime/hub.py::topics_for` — checked directly, unchanged since Phase 1: `activity` / `task:{key}` / `agent:{slug}`, no `run:{id}` topic exists and none is needed). The frontend side of this is the same one file Phase 1.5 already extended for governance events: `src/lib/realtime/invalidate.ts`. No orchestrator, hub, or WebSocket protocol change is in scope.

**0.5 One real, small backend gap, found by checking rather than assumed: the wire event doesn't carry `run_id`.** `EventOut` (`acc/api/schemas.py`) has `task_key`/`task_title`/`agent_slug`/`agent_name` but no `run_id`, even though the underlying `events.run_id` column has existed since M1 (migration `0014`, added specifically for this). Today, `queryKeysToInvalidate` can only match a run-scoped event by `task_key` (correct but coarse — it would invalidate *every* run tied to that task, not just the one on screen) or by `type` prefix (`run.`/`tool_call.`/`usage.`, no id at all). Whether to add `run_id` to `EventOut` for precise invalidation, or accept the coarser task-scoped refetch (same "deliberately broad... costs nothing extra in practice" reasoning `invalidate.ts` already documents for every other event type), is a real design choice — see §5.

## 1. Frontend changes

Follows the existing `apps/web` conventions throughout — no new pattern introduced. Referenced against the actual current files, not a generic Next.js sketch.

### 1.1 New routes

- `src/app/(dashboard)/runs/page.tsx` — the Runs list. Filters (`task_id`, `agent_id`, `status`) map directly to `GET /runs`'s existing query params, same URL-driven-filter pattern the Tasks board already uses.
- `src/app/(dashboard)/runs/[id]/page.tsx` — the Run detail page. Mirrors `tasks/[key]/page.tsx`'s shape closely: a summary header (status badge, agent, task link, timing, cost), then `Tabs` for the distinct views your scope lists.

### 1.2 New components (`src/components/runs/`)

| Component | Backed by | Notes |
|---|---|---|
| `run-status-badge.tsx` | `RunOut.status` | Same pattern as `task-status-badge.tsx` |
| `run-list.tsx` / `run-card.tsx` | `GET /runs` | Mirrors `kanban-board.tsx`'s data-fetching shape, but a plain filterable table/list, not a board — a run doesn't have operator-driven drag transitions, so a Kanban metaphor doesn't fit |
| `run-summary-header.tsx` | `GET /runs/{id}` | Status, agent, task (linked), branch_name (linked to nothing yet — no in-app diff viewer exists, see §3's "known gap"), queued/started/ended timestamps, cost/token totals |
| `run-error-panel.tsx` | `RunOut.error` | Renders the structured JSONB (`reason`, and when present, `category`/`stop_reason`/`api_error_status`) — only rendered when `status` is `failed`/`timed_out`/`cancelled` and `error` is non-null |
| `tool-call-timeline.tsx` | `GET /runs/{id}/tool-calls` | Chronological (already ordered by `started_at` server-side); each row shows tool, a truncated/collapsible view of `arguments`, `allowed`, and — this is "governance denial visibility" — when `allowed` is false, `denial_reason` plus `blocked_by_effect` rendered distinctly from a plain error (a `deny` vs `require_approval` badge, not just red text) |
| `agent-message-list.tsx` | `GET /runs/{id}/events?type=agent.message` (existing generic events endpoint, filtered — see §2.1) | The model's own narration, newest or oldest first (open decision, §5) |
| `usage-panel.tsx` | `GET /runs/{id}/usage` | Per-model-call rows (usually one, per M4.1's one-shot `query()` design) plus the running total already on `RunOut` itself; visually distinguishes `cost_source: "estimated"` (a partial/cancelled report, M4.2) from `"provider_reported"` |
| `file-changes-viewer.tsx` | `GET /runs/{id}/file-changes` | Stats only (path/change-type/±lines) — **not** diff content, which doesn't exist anywhere in this system yet (ARCHITECTURE-REVIEW-2026-09.md §4.8's own noted gap). This component is a viewer for what the backend actually has, not a diff viewer; see §3 for why that distinction matters here specifically. |
| `cancel-run-button.tsx` | `POST /runs/{id}/cancel` | The one write in this whole plan — everything else is read-only. Confirmation dialog before firing, same as any other destructive-ish action in this codebase |

### 1.3 Task detail page gets one small addition

`tasks/[key]/page.tsx` currently has no way to reach a task's run(s) at all — `TaskDetailOut` doesn't include them, and nothing links to `/runs`. Add a "Runs" section/tab fetching `GET /runs?task_id={id}` (already supports this filter, no backend change), each row linking to `/runs/{id}`. This is the natural discovery path — an operator watching a task move to `working` should be one click from watching the run, not required to know `/runs` exists.

### 1.4 Query keys and realtime (extends existing files, doesn't replace them)

```ts
// queryKeys.ts
runs: {
  all: ["runs"] as const,
  list: (filters: object = {}) => ["runs", "list", filters] as const,
  detail: (id: string) => ["runs", "detail", id] as const,
  toolCalls: (id: string) => ["runs", "detail", id, "tool-calls"] as const,
  usage: (id: string) => ["runs", "detail", id, "usage"] as const,
  fileChanges: (id: string) => ["runs", "detail", id, "file-changes"] as const,
},
```

```ts
// invalidate.ts — one new branch, same shape as the existing task_key/agent_slug/type checks
if (event.type?.startsWith("run.") || event.type?.startsWith("tool_call.") || event.type === "usage.recorded")
  keys.push(queryKeys.runs.all);
```

Coarse (invalidates every mounted run query, not just the one on screen) unless §0.5's `run_id`-on-the-wire-event change is made — consistent with `invalidate.ts`'s own documented philosophy elsewhere ("deliberately broad rather than precise... costs nothing extra in practice").

## 2. Backend endpoints needed

**Nothing new is required to build this.** One existing gap worth closing alongside it, and one genuinely new (small) endpoint:

### 2.1 New: a way to fetch agent.message events for one run

There's no `GET /runs/{id}/messages`. `agent.message` events exist (M4.1) and carry `run_id` on `EventRecord`, but the only read path is the generic `GET /events?task_id=...&type=...`, not run-scoped. Two options: (a) add `run_id` as a filter param to the existing `GET /events` endpoint (small, generic, arguably useful beyond just messages), or (b) add a dedicated `GET /runs/{id}/messages` (consistent with the tool-calls/usage/file-changes pattern, but a narrower single-purpose addition). Recommend (a) — it's the smaller, more broadly useful change and doesn't add a fourth near-identical nested-list endpoint. Flagged as a decision, not decided here — see §5.

### 2.2 Recommended: add `run_id` to `EventOut`

Closes §0.5's gap. `events.run_id` has existed since M1; this is a one-field schema addition (`api/schemas.py::EventOut`), threading it through the existing query-layer `EventRecord`→`EventOut` mapping (already carries the field internally, just never surfaced). No migration, no new column — the same class of "existing column never exposed" fix M3 and M4.1 each made once already. Enables precise (not just task-scoped) realtime invalidation for the run detail page specifically.

### 2.3 Explicitly not adding

No diff-content endpoint (ARCHITECTURE-REVIEW-2026-09.md §4.8 already named this as a known, deliberate gap — out of scope here too, for the same reason: it's a separate, real feature, not a visibility gap in what already exists). No new mutation beyond the existing `POST /runs/{id}/cancel`. No changes to `can_use_tool`, governance evaluation, or anything inside the runner — this plan is strictly the read/display side.

## 3. Security considerations

**3.1 No new attack surface, only new display surface.** Every endpoint this plan uses already exists, already requires `OperatorDep` (the same session-cookie auth as every other dashboard page), and already returns data the operator is already entitled to see via the API today. This plan makes visible what's already reachable, it doesn't widen who can reach it.

**3.2 Redaction already happened before this data ever reached the database.** `tool_calls.arguments`/`result_excerpt` are redacted in the runner (M4.2's layered redaction — secret env-var assignments, common token formats, password/key fields, bounded length) *before* they're ever sent to the internal API. The frontend displaying them as-is is safe by construction, not something this plan needs to add its own redaction pass for. Worth stating plainly rather than silently assuming: the dashboard is a second, independent audience for already-sanitized data, not a new place secrets could leak from.

**3.3 `arguments`/`result_excerpt` are still arbitrary, model-authored JSON/text, and need to be rendered defensively regardless of redaction.** Not a security boundary this system needs to enforce (single-operator, trusted viewer), but ordinary frontend hygiene: render as text/`<pre>`, never interpret as HTML, and bound the rendered size client-side too (matching the server's own `MAX_RESULT_EXCERPT`/`MAX_REPORTED_TEXT` caps) so a large or oddly-shaped tool result can't make the page unresponsive.

**3.4 The cancel button is the one place this plan adds a write, and it's not a new capability.** `POST /runs/{id}/cancel` already exists (M2) and is already reachable via the API by any signed-in operator; this plan only adds a UI button for something that was already one authenticated API call away. No new authorization logic needed.

**3.5 Governance-denial visibility is explicitly display-only.** Showing `blocked_by_effect`/`rule_id`/`denial_reason` on the timeline does not create any path for an operator to *override* a denial or act on a `require_approval` block from the UI — there is still no approval mechanism (ARCHITECTURE-REVIEW-2026-09.md §3's "Approval execution" section, unchanged by this plan). This is purely the same "preparation, not execution" distinction M4.2 already established for the data itself, now extended to its display.

## 4. Relationship to future real AI usage

This is, concretely, the dashboard-side precondition for resuming M4.3. The live-verification pause isn't only "no API key" — it's also that, even with a key, there is currently no way to *watch* a real run happen except `docker compose exec postgres psql` and manual `curl`, which is exactly what every M4.1/M4.2/M4.3-prep live check has had to do so far. Once this plan ships:

- Resuming M4.3's live run becomes something you can watch unfold in the dashboard in real time — task moves to `working`, the run appears, tool calls stream in, a governance denial (if one happens naturally) is visibly distinct from an ordinary error, cost accumulates on screen, the task lands in `review` with a real summary and real file-change stats visible without a terminal.
- It's also the visibility floor for *any* future real usage, not just M4.3's one task — the same pages are what "real AI production usage" (your own phrase) would be supervised through afterward, not a one-time verification aid.
- It does **not** unblock or change anything about M4.3 itself. No code in this plan touches the runner, the SDK adapter, governance evaluation, or the API key requirement. M4.3 remains paused exactly as it is until you decide otherwise; this is independent, additive work that happens to make that eventual run worth watching.

## 5. Open decisions needing your input before I code

- **`agent.message` read path** (§2.1): add `run_id` as a filter to the existing `GET /events`, or a dedicated `GET /runs/{id}/messages`? Recommend the `GET /events` filter — smaller, more generally useful.
- **`run_id` on `EventOut`** (§2.2/§0.5): add it for precise run-scoped realtime invalidation, or accept the coarser task-scoped refetch (zero backend change, matches this codebase's own stated "deliberately broad, costs nothing" philosophy elsewhere)? Recommend adding it — it's a small, well-justified, already-precedented fix, and a run detail page refetching every sibling run's queries on every event is a worse default to ship than to avoid for one extra field.
- **Agent message ordering**: newest-first (consistent with the Activity feed) or oldest-first (reads like a transcript, arguably more natural for "watching an agent work")? No strong recommendation — genuinely a UX preference.
- **Tool call timeline granularity**: interleave `agent.message` and tool calls into one single chronological narrative, or keep them as two separate lists/tabs (simpler to build, matches the "distinct items" framing your scope listed them as)? Recommend starting with two separate lists — simpler, and a merged timeline can be a follow-up once real usage shows whether the split is actually annoying in practice.

Waiting for your approval on this plan, and the decisions above, before writing any code.
