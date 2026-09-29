"use client";

import { PlayCircle } from "lucide-react";
import Link from "next/link";

import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { EmptyState } from "@/components/shell/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useRuns, type RunFilters } from "@/lib/api/hooks";
import { formatCostUsd, formatRelativeTime } from "@/lib/format";

export function RunList({
  filters,
  taskKey,
}: {
  filters?: RunFilters;
  /** Client-side filter by the task's human key (e.g. "ACC-142"), for embedding on the
   * task detail page. GET /runs only filters by task_id (a uuid) server-side, and
   * TaskOut only exposes the human key, not the task's id — unlike GET /events, which
   * already accepts a `task` key filter. Fine for a single-operator system's current
   * run volume; a server-side key filter (mirroring /events) is a natural follow-up if
   * that stops being true. */
  taskKey?: string;
}) {
  const { data: allRuns, isPending } = useRuns(filters);
  const runs = taskKey ? allRuns?.filter((run) => run.task_key === taskKey) : allRuns;

  if (isPending) return <Skeleton className="h-64 rounded-xl" />;

  if (!runs || runs.length === 0) {
    return (
      <EmptyState
        icon={PlayCircle}
        title="No runs yet"
        description="A run appears here once an agent starts working on a task."
      />
    );
  }

  return (
    <div className="divide-y rounded-xl border">
      {runs.map((run) => (
        <Link
          key={run.id}
          href={`/runs/${run.id}`}
          className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-muted/50"
        >
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">
              {run.task_key} <span className="font-normal text-muted-foreground">· {run.agent_slug}</span>
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Queued <time dateTime={run.queued_at}>{formatRelativeTime(run.queued_at)}</time>
              {run.current_step ? ` · ${run.current_step}` : ""}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <span className="text-xs text-muted-foreground">{formatCostUsd(run.cost_usd)}</span>
            <RunStatusBadge status={run.status} />
          </div>
        </Link>
      ))}
    </div>
  );
}
