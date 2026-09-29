"use client";

import { FileDiff } from "lucide-react";

import { EmptyState } from "@/components/shell/empty-state";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRunFileChanges } from "@/lib/api/hooks";

const CHANGE_TYPE_LABEL: Record<string, string> = {
  added: "Added",
  modified: "Modified",
  deleted: "Deleted",
  renamed: "Renamed",
};

const CHANGE_TYPE_COLOR: Record<string, string> = {
  added: "bg-success/15 text-success",
  modified: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  deleted: "bg-destructive/15 text-destructive",
  renamed: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
};

/**
 * Stats only (path, change type, ± line counts) — not diff content, which doesn't exist
 * anywhere in this system yet (ARCHITECTURE-REVIEW-2026-09.md §4.8, restated in
 * PLAN-PHASE-2.5.md §1.2). Reviewing the actual code still means looking at the pushed
 * branch directly; this is a viewer for what the backend has, not a diff viewer.
 */
export function FileChangesViewer({ runId }: { runId: string }) {
  const { data: changes, isPending } = useRunFileChanges(runId);

  if (isPending) return <Skeleton className="h-32 rounded-xl" />;

  if (!changes || changes.length === 0) {
    return (
      <EmptyState
        icon={FileDiff}
        title="No file changes yet"
        description="File changes appear here once the agent commits work to its branch."
      />
    );
  }

  return (
    <ul className="divide-y rounded-xl border">
      {changes.map((change) => (
        <li key={change.id} className="flex items-center justify-between gap-4 px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <Badge
              variant="outline"
              className={`border-transparent ${CHANGE_TYPE_COLOR[change.change_type] ?? "bg-muted text-muted-foreground"}`}
            >
              {CHANGE_TYPE_LABEL[change.change_type] ?? change.change_type}
            </Badge>
            <code className="truncate text-sm">{change.path}</code>
          </div>
          <div className="shrink-0 text-xs">
            {change.additions !== null && <span className="text-success">+{change.additions}</span>}
            {change.additions !== null && change.deletions !== null && " "}
            {change.deletions !== null && <span className="text-destructive">−{change.deletions}</span>}
          </div>
        </li>
      ))}
    </ul>
  );
}
