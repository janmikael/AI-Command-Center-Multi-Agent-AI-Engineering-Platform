import { ArrowRight } from "lucide-react";

import { TaskStatusBadge } from "@/components/tasks/task-status-badge";
import type { TaskDetailOut } from "@/lib/api/hooks";
import { formatDateTime } from "@/lib/format";

export function StatusHistory({ history }: { history: TaskDetailOut["status_history"] }) {
  return (
    <ol className="space-y-3">
      {history.map((change, i) => (
        <li key={i} className="flex items-start gap-3 text-sm">
          <div className="mt-1.5 size-1.5 shrink-0 rounded-full bg-muted-foreground/40" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              {change.from_status && (
                <>
                  <TaskStatusBadge status={change.from_status} />
                  <ArrowRight className="size-3 text-muted-foreground" aria-hidden />
                </>
              )}
              <TaskStatusBadge status={change.to_status} />
            </div>
            {change.reason && <p className="mt-1 text-muted-foreground">{change.reason}</p>}
            <p className="mt-1 text-xs text-muted-foreground">
              {change.actor_label} · {formatDateTime(change.ts)}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
