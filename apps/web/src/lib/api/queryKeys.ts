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
  governance: {
    all: ["governance"] as const,
    rules: (filters: object = {}) => ["governance", "rules", filters] as const,
  },
  runs: {
    all: ["runs"] as const,
    list: (filters: object = {}) => ["runs", "list", filters] as const,
    detail: (id: string) => ["runs", "detail", id] as const,
    toolCalls: (id: string) => ["runs", "detail", id, "tool-calls"] as const,
    usage: (id: string) => ["runs", "detail", id, "usage"] as const,
    fileChanges: (id: string) => ["runs", "detail", id, "file-changes"] as const,
  },
};
