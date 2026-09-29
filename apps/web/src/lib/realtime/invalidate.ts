import { queryKeys } from "@/lib/api/queryKeys";

/** The subset of the wire event shape this module actually needs. */
export interface InvalidationEvent {
  type?: string;
  task_key?: string | null;
  agent_slug?: string | null;
  run_id?: string | null;
}

/**
 * Maps one realtime event to the query-key prefixes it could have changed. Deliberately
 * broad rather than precise (e.g. `tasks.all` invalidates every task, not just one) —
 * TanStack Query only refetches what's actually mounted, so an over-broad invalidation
 * costs nothing extra in practice and stays trivially correct as new pages are added.
 *
 * Mirrors acc.realtime.hub.topics_for on the backend, which computes the same three
 * cases (activity / task / agent) from the same event fields. Governance and project
 * context events carry neither task_key nor agent_slug, so they're matched on `type`
 * instead — the governance UI has no per-project scoping this phase, so both event
 * families invalidate the same broad `governance.all` prefix.
 *
 * run_id (Phase 2.5): unlike task_key/agent_slug, this one *is* precise when present —
 * events.run_id is only ever set on run/tool-call/usage events (acc.commands.runs's own
 * _emit_run), and has been on the wire event since M1 (acc.realtime.publisher.as_wire_dict)
 * — so a run detail page's own query only refetches for events belonging to *that* run,
 * not every run tied to the same task. The list page (queryKeys.runs.all) still
 * refetches broadly, same reasoning as tasks.all above.
 */
export function queryKeysToInvalidate(event: InvalidationEvent): readonly (readonly unknown[])[] {
  // "activity" always matches server-side, so the feed and the aggregate always refresh.
  const keys: (readonly unknown[])[] = [queryKeys.overview, queryKeys.events.all];
  if (event.task_key) keys.push(queryKeys.tasks.all);
  if (event.agent_slug) keys.push(queryKeys.agents.all);
  if (event.type?.startsWith("governance_rule.") || event.type?.startsWith("project_context_entry."))
    keys.push(queryKeys.governance.all);
  if (event.run_id) keys.push(queryKeys.runs.all, queryKeys.runs.detail(event.run_id));
  return keys;
}

/** Used for {"type":"resync"}: we don't know what changed, so refresh everything. */
export function allQueryKeys(): readonly (readonly unknown[])[] {
  return [
    queryKeys.overview,
    queryKeys.events.all,
    queryKeys.tasks.all,
    queryKeys.agents.all,
    queryKeys.governance.all,
    queryKeys.runs.all,
  ];
}
