import { AlertTriangle } from "lucide-react";

/**
 * RunOut.error (PLAN-PHASE-2-M4.2.md §6): {reason: string, category?, stop_reason?,
 * api_error_status?} — structured, not just a flat string, since M4.2. Untyped on the
 * wire (a JSONB column, `{[key: string]: unknown}` in the generated schema), so this
 * reads defensively rather than assuming every key is present.
 */
export function RunErrorPanel({ error }: { error: Record<string, unknown> }) {
  const reason = typeof error.reason === "string" ? error.reason : null;
  const category = typeof error.category === "string" ? error.category : null;
  const stopReason = typeof error.stop_reason === "string" ? error.stop_reason : null;
  const apiErrorStatus =
    typeof error.api_error_status === "number" ? error.api_error_status : null;

  return (
    <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-destructive">{reason ?? "This run failed."}</p>
          {(category || stopReason || apiErrorStatus !== null) && (
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {category && (
                <>
                  <dt className="font-medium">Category</dt>
                  <dd>{category}</dd>
                </>
              )}
              {stopReason && (
                <>
                  <dt className="font-medium">Stop reason</dt>
                  <dd>{stopReason}</dd>
                </>
              )}
              {apiErrorStatus !== null && (
                <>
                  <dt className="font-medium">API status</dt>
                  <dd>{apiErrorStatus}</dd>
                </>
              )}
            </dl>
          )}
        </div>
      </div>
    </div>
  );
}
