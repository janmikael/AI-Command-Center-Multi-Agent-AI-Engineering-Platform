/**
 * Central query key factory. Kept deliberately shallow (one extra level for filters/ids)
 * so a single `invalidateQueries({ queryKey: queryKeys.tasks.all })` — a prefix match —
 * reaches every variant: the list, every filtered list, every task's detail and its
 * events. See src/lib/realtime/invalidate.ts for where that prefix matching is used.
 */
export const queryKeys = {
  overview: ["overview"] as const,
  agents: {
    all: ["agents"] as const,
    detail: (slug: string) => ["agents", slug] as const,
  },
  tasks: {
    all: ["tasks"] as const,
    list: (filters: object = {}) => ["tasks", "list", filters] as const,
    detail: (key: string) => ["tasks", "detail", key] as const,
    events: (key: string) => ["tasks", "detail", key, "events"] as const,
  },
  events: {
    all: ["events"] as const,
    list: (filters: object = {}) => ["events", "list", filters] as const,
  },
};
