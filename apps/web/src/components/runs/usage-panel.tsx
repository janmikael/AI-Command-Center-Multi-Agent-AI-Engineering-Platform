"use client";

import { Coins } from "lucide-react";

import { EmptyState } from "@/components/shell/empty-state";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRunUsage } from "@/lib/api/hooks";
import { formatCostUsd, formatDateTime } from "@/lib/format";

export function UsagePanel({ runId }: { runId: string }) {
  const { data: records, isPending } = useRunUsage(runId);

  if (isPending) return <Skeleton className="h-32 rounded-xl" />;

  if (!records || records.length === 0) {
    return (
      <EmptyState
        icon={Coins}
        title="No usage recorded yet"
        description="Token and cost figures appear here once the agent's session reports them."
      />
    );
  }

  return (
    <div className="divide-y rounded-xl border">
      {records.map((record) => (
        <div key={record.id} className="flex items-center justify-between gap-4 px-4 py-3">
          <div className="min-w-0">
            <p className="text-sm font-medium">
              {record.model}{" "}
              {record.cost_source === "estimated" && (
                <Badge variant="outline" className="border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-300">
                  Estimated
                </Badge>
              )}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {record.input_tokens.toLocaleString()} in · {record.output_tokens.toLocaleString()} out
              {record.cache_read_tokens > 0 && ` · ${record.cache_read_tokens.toLocaleString()} cache read`}
              {record.cache_write_tokens > 0 && ` · ${record.cache_write_tokens.toLocaleString()} cache write`}
              {" · "}
              <time dateTime={record.recorded_at}>{formatDateTime(record.recorded_at)}</time>
            </p>
          </div>
          <span className="shrink-0 text-sm font-medium">{formatCostUsd(record.cost_usd)}</span>
        </div>
      ))}
    </div>
  );
}
