import { queryKeys } from "@/lib/api/queryKeys";

/** The subset of the wire event shape this module actually needs. */
export interface InvalidationEvent {
  task_key?: string | null;
  agent_slug?: string | null;
}

/**
 * Maps one realtime event to the query-key prefixes it could have changed. Deliberately
 * broad rather than precise (e.g. `tasks.all` invalidates every task, not just one) —
 * TanStack Query only refetches what's actually mounted, so an over-broad invalidation
 * costs nothing extra in practice and stays trivially correct as new pages are added.
 *
 * Mirrors acc.realtime.hub.topics_for on the backend, which computes the same three
 * cases (activity / task / agent) from the same event fields.
 */
export function queryKeysToInvalidate(event: InvalidationEvent): readonly (readonly unknown[])[] {
  // "activity" always matches server-side, so the feed and the aggregate always refresh.
  const keys: (readonly unknown[])[] = [queryKeys.overview, queryKeys.events.all];
  if (event.task_key) keys.push(queryKeys.tasks.all);
  if (event.agent_slug) keys.push(queryKeys.agents.all);
  return keys;
}

/** Used for {"type":"resync"}: we don't know what changed, so refresh everything. */
export function allQueryKeys(): readonly (readonly unknown[])[] {
  return [queryKeys.overview, queryKeys.events.all, queryKeys.tasks.all, queryKeys.agents.all];
}
