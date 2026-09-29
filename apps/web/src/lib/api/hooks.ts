"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";

type TaskStatus = components["schemas"]["TaskStatus"];
type TaskOut = components["schemas"]["TaskOut"];
type TaskDetailOut = components["schemas"]["TaskDetailOut"];
type AgentOut = components["schemas"]["AgentOut"];
type RuleCategory = components["schemas"]["RuleCategory"];
type GovernanceRuleOut = components["schemas"]["GovernanceRuleOut"];
type RunOut = components["schemas"]["RunOut"];

// ─── Reads ──────────────────────────────────────────────────────────────
export function useOverview() {
  return useQuery({
    queryKey: queryKeys.overview,
    queryFn: async () => unwrap(await api.GET("/api/v1/overview")),
    refetchInterval: 60_000, // belt-and-suspenders; realtime invalidation is the primary path
  });
}

export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents.all,
    queryFn: async () => (unwrap(await api.GET("/api/v1/agents"))).items,
  });
}

export function useAgent(slug: string) {
  return useQuery({
    queryKey: queryKeys.agents.detail(slug),
    queryFn: async () => unwrap(await api.GET("/api/v1/agents/{slug}", { params: { path: { slug } } })),
  });
}

export interface TaskFilters {
  status?: TaskStatus[];
  agent?: string;
  q?: string;
}

export function useTasks(filters: TaskFilters = {}) {
  return useQuery({
    queryKey: queryKeys.tasks.list(filters),
    queryFn: async () =>
      (
        unwrap(
          await api.GET("/api/v1/tasks", {
            params: { query: { status: filters.status, agent: filters.agent, q: filters.q } },
          })
        )
      ).items,
  });
}

export function useTask(key: string, options?: Partial<UseQueryOptions<TaskDetailOut>>) {
  return useQuery({
    queryKey: queryKeys.tasks.detail(key),
    queryFn: async () => unwrap(await api.GET("/api/v1/tasks/{key}", { params: { path: { key } } })),
    ...options,
  });
}

export function useTaskEvents(key: string) {
  return useInfiniteQuery({
    queryKey: queryKeys.tasks.events(key),
    initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/tasks/{key}/events", {
          params: { path: { key }, query: { before_seq: pageParam, limit: 30 } },
        })
      ),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export interface EventFilters {
  type?: string;
  agent?: string;
  task?: string;
  severity?: string;
}

export function useEventsFeed(filters: EventFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.events.list(filters),
    initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/events", {
          params: {
            query: {
              type: filters.type ? [filters.type] : undefined,
              agent: filters.agent,
              task: filters.task,
              severity: filters.severity ? [filters.severity] : undefined,
              before_seq: pageParam,
              limit: 30,
            },
          },
        })
      ),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export interface GovernanceRuleFilters {
  category?: RuleCategory;
  enabled?: boolean;
}

export function useGovernanceRules(filters: GovernanceRuleFilters = {}) {
  return useQuery({
    queryKey: queryKeys.governance.rules(filters),
    queryFn: async () =>
      (
        unwrap(
          await api.GET("/api/v1/governance-rules", {
            params: { query: { category: filters.category, enabled: filters.enabled } },
          })
        )
      ).items,
  });
}

export interface RunFilters {
  task_id?: string;
  agent_id?: string;
  status?: string;
}

export function useRuns(filters: RunFilters = {}) {
  return useQuery({
    queryKey: queryKeys.runs.list(filters),
    queryFn: async () =>
      (
        unwrap(
          await api.GET("/api/v1/runs", {
            params: {
              query: { task_id: filters.task_id, agent_id: filters.agent_id, status: filters.status },
            },
          })
        )
      ).items,
  });
}

export function useRun(id: string, options?: Partial<UseQueryOptions<RunOut>>) {
  return useQuery({
    queryKey: queryKeys.runs.detail(id),
    queryFn: async () => unwrap(await api.GET("/api/v1/runs/{run_id}", { params: { path: { run_id: id } } })),
    ...options,
  });
}

export function useRunToolCalls(id: string) {
  return useQuery({
    queryKey: queryKeys.runs.toolCalls(id),
    queryFn: async () =>
      (
        unwrap(
          await api.GET("/api/v1/runs/{run_id}/tool-calls", { params: { path: { run_id: id } } })
        )
      ).items,
  });
}

export function useRunUsage(id: string) {
  return useQuery({
    queryKey: queryKeys.runs.usage(id),
    queryFn: async () =>
      (unwrap(await api.GET("/api/v1/runs/{run_id}/usage", { params: { path: { run_id: id } } })))
        .items,
  });
}

export function useRunFileChanges(id: string) {
  return useQuery({
    queryKey: queryKeys.runs.fileChanges(id),
    queryFn: async () =>
      (
        unwrap(
          await api.GET("/api/v1/runs/{run_id}/file-changes", { params: { path: { run_id: id } } })
        )
      ).items,
  });
}

// ─── Writes ─────────────────────────────────────────────────────────────
/** Every mutation invalidates broadly on success. The live WebSocket event (for every
 * other open tab/session) arrives moments later and invalidates the same way — this
 * local invalidation just makes the tab that made the change feel instant. */
function useInvalidateEverything() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: queryKeys.overview });
    void qc.invalidateQueries({ queryKey: queryKeys.tasks.all });
    void qc.invalidateQueries({ queryKey: queryKeys.agents.all });
    void qc.invalidateQueries({ queryKey: queryKeys.events.all });
    void qc.invalidateQueries({ queryKey: queryKeys.governance.all });
  };
}

type CreateTaskBody = components["schemas"]["TaskCreateIn"];

export function useCreateTask() {
  const invalidate = useInvalidateEverything();
  return useMutation({
    mutationFn: async (body: CreateTaskBody) =>
      unwrap(await api.POST("/api/v1/tasks", { body })),
    onSuccess: invalidate,
  });
}

type PatchTaskBody = components["schemas"]["TaskPatchIn"];

export function useUpdateTask() {
  const invalidate = useInvalidateEverything();
  return useMutation({
    mutationFn: async ({ key, version, body }: { key: string; version: number; body: PatchTaskBody }) =>
      unwrap(
        await api.PATCH("/api/v1/tasks/{key}", {
          params: { path: { key }, header: { "if-match": `"${version}"` } },
          body,
        })
      ),
    onSuccess: invalidate,
  });
}

export function useTransitionTask() {
  const invalidate = useInvalidateEverything();
  return useMutation({
    mutationFn: async ({
      key,
      version,
      to,
      reason,
    }: {
      key: string;
      version: number;
      to: TaskStatus;
      reason?: string;
    }) =>
      unwrap(
        await api.POST("/api/v1/tasks/{key}/transition", {
          params: { path: { key }, header: { "if-match": `"${version}"` } },
          body: { to, reason: reason || null },
        })
      ),
    onSuccess: invalidate,
  });
}

export function useAssignTask() {
  const invalidate = useInvalidateEverything();
  return useMutation({
    mutationFn: async ({
      key,
      version,
      agentSlug,
    }: {
      key: string;
      version: number;
      agentSlug: string | null;
    }) =>
      unwrap(
        await api.POST("/api/v1/tasks/{key}/assign", {
          params: { path: { key }, header: { "if-match": `"${version}"` } },
          body: { agent_slug: agentSlug },
        })
      ),
    onSuccess: invalidate,
  });
}

type CreateRuleBody = components["schemas"]["GovernanceRuleCreateIn"];

export function useCreateRule() {
  const invalidate = useInvalidateEverything();
  return useMutation({
    mutationFn: async (body: CreateRuleBody) =>
      unwrap(await api.POST("/api/v1/governance-rules", { body })),
    onSuccess: invalidate,
  });
}

type PatchRuleBody = components["schemas"]["GovernanceRulePatchIn"];

export function useUpdateRule() {
  const invalidate = useInvalidateEverything();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: PatchRuleBody }) =>
      unwrap(
        await api.PATCH("/api/v1/governance-rules/{rule_id}", {
          params: { path: { rule_id: id } },
          body,
        })
      ),
    onSuccess: invalidate,
  });
}

export function useSetRuleEnabled() {
  const invalidate = useInvalidateEverything();
  return useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) =>
      unwrap(
        enabled
          ? await api.POST("/api/v1/governance-rules/{rule_id}/enable", {
              params: { path: { rule_id: id } },
            })
          : await api.POST("/api/v1/governance-rules/{rule_id}/disable", {
              params: { path: { rule_id: id } },
            })
      ),
    onSuccess: invalidate,
  });
}

export function useCancelRun(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/runs/{run_id}/cancel", { params: { path: { run_id: id } } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.runs.detail(id) });
      void qc.invalidateQueries({ queryKey: queryKeys.runs.all });
      void qc.invalidateQueries({ queryKey: queryKeys.overview });
    },
  });
}

export type { AgentOut, GovernanceRuleOut, RuleCategory, RunOut, TaskDetailOut, TaskOut };
