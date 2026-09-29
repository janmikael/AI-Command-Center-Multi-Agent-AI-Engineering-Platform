"use client";

import { CheckCircle2, ShieldAlert, Wrench } from "lucide-react";

import { EmptyState } from "@/components/shell/empty-state";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRunToolCalls } from "@/lib/api/hooks";
import { formatDateTime, formatDurationMs } from "@/lib/format";
import { cn } from "@/lib/utils";

// ─── Governance denial visibility (PLAN-PHASE-2.5.md scope item) ──────────
// "deny" and "require_approval" are mechanically identical outcomes today (both refuse
// the call — no approval mechanism exists yet, PLAN-PHASE-2-M4.2.md §3) but are shown
// with distinct badges here: blocked_by_effect is approval-state *preparation*, and this
// is the display-only extension of that same distinction, not a hint that the two
// behave differently yet.
const EFFECT_LABEL: Record<string, string> = {
  deny: "Denied",
  require_approval: "Needs approval",
};

const EFFECT_COLOR: Record<string, string> = {
  deny: "bg-destructive/15 text-destructive",
  require_approval: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
};

function ArgumentsView({ arguments_ }: { arguments_: Record<string, unknown> }) {
  const text = JSON.stringify(arguments_, null, 2);
  return (
    <details className="mt-1.5">
      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
        Arguments
      </summary>
      {/* Rendered as plain text, never HTML — model-authored content (PLAN-PHASE-2.5.md §3.3).
          Already redacted server-side before storage (PLAN-PHASE-2-M4.2.md's layered redaction). */}
      <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-muted p-2 text-xs whitespace-pre-wrap">
        {text}
      </pre>
    </details>
  );
}

export function ToolCallTimeline({ runId }: { runId: string }) {
  const { data: calls, isPending } = useRunToolCalls(runId);

  if (isPending) return <Skeleton className="h-48 rounded-xl" />;

  if (!calls || calls.length === 0) {
    return (
      <EmptyState
        icon={Wrench}
        title="No tool calls yet"
        description="Tool calls appear here as the agent reads, edits, and runs commands."
      />
    );
  }

  return (
    <ol className="divide-y rounded-xl border">
      {calls.map((call) => (
        <li key={call.id} className="px-4 py-3">
          <div className="flex items-start gap-2.5">
            {call.allowed ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
            ) : (
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <code className="text-sm font-medium">{call.tool}</code>
                {!call.allowed && call.blocked_by_effect && (
                  <Badge
                    variant="outline"
                    className={cn(
                      "border-transparent",
                      EFFECT_COLOR[call.blocked_by_effect] ?? "bg-destructive/15 text-destructive"
                    )}
                  >
                    {EFFECT_LABEL[call.blocked_by_effect] ?? call.blocked_by_effect}
                  </Badge>
                )}
                {call.exit_code !== null && call.exit_code !== 0 && (
                  <Badge variant="outline" className="border-transparent bg-destructive/15 text-destructive">
                    exit {call.exit_code}
                  </Badge>
                )}
                <time className="text-xs text-muted-foreground" dateTime={call.started_at}>
                  {formatDateTime(call.started_at)}
                </time>
                {call.duration_ms !== null && (
                  <span className="text-xs text-muted-foreground">{formatDurationMs(call.duration_ms)}</span>
                )}
              </div>

              {!call.allowed && call.denial_reason && (
                <p className="mt-1 text-sm text-destructive">{call.denial_reason}</p>
              )}

              <ArgumentsView arguments_={call.arguments} />

              {call.result_excerpt && (
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                    Result
                  </summary>
                  <pre className="mt-1 max-h-64 overflow-auto rounded-lg bg-muted p-2 text-xs whitespace-pre-wrap">
                    {call.result_excerpt}
                  </pre>
                </details>
              )}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}
