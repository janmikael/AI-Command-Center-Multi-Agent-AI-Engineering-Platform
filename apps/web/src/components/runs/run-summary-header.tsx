import Link from "next/link";

import { CancelRunButton } from "@/components/runs/cancel-run-button";
import { RunErrorPanel } from "@/components/runs/run-error-panel";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { Card, CardContent } from "@/components/ui/card";
import type { RunOut } from "@/lib/api/hooks";
import { ACTIVE_RUN_STATUSES, formatCostUsd, formatDateTime } from "@/lib/format";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium">{value}</dd>
    </div>
  );
}

export function RunSummaryHeader({ run }: { run: RunOut }) {
  const canCancel = ACTIVE_RUN_STATUSES.has(run.status);

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <RunStatusBadge status={run.status} />
              {run.branch_name && (
                <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{run.branch_name}</code>
              )}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              <Link href={`/tasks/${run.task_key}`} className="font-medium text-foreground underline-offset-2 hover:underline">
                {run.task_key}
              </Link>{" "}
              · {run.agent_slug}
              {run.current_step ? ` · ${run.current_step}` : ""}
            </p>
          </div>
          {canCancel && <CancelRunButton runId={run.id} />}
        </div>

        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Queued" value={formatDateTime(run.queued_at)} />
          <Stat label="Started" value={run.started_at ? formatDateTime(run.started_at) : "—"} />
          <Stat label="Ended" value={run.ended_at ? formatDateTime(run.ended_at) : "—"} />
          <Stat label="Cost" value={formatCostUsd(run.cost_usd)} />
          <Stat label="Input tokens" value={run.input_tokens.toLocaleString()} />
          <Stat label="Output tokens" value={run.output_tokens.toLocaleString()} />
          <Stat label="Cache read" value={run.cache_read_tokens.toLocaleString()} />
          <Stat label="Cache write" value={run.cache_write_tokens.toLocaleString()} />
        </dl>

        {run.result_summary && (
          <div>
            <p className="text-xs text-muted-foreground">Result</p>
            <p className="mt-0.5 text-sm whitespace-pre-wrap">{run.result_summary}</p>
          </div>
        )}

        {run.error && <RunErrorPanel error={run.error} />}
      </CardContent>
    </Card>
  );
}
