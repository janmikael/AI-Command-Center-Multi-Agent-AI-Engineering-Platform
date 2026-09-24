"use client";

import { useRealtimeStatus } from "@/components/providers/realtime-provider";
import { cn } from "@/lib/utils";

const LABEL = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
} as const;

const DOT = {
  connecting: "bg-muted-foreground/50",
  live: "bg-success animate-pulse",
  reconnecting: "bg-warning",
} as const;

/** Reflects *this browser tab's* WebSocket connection — distinct from SystemStatus,
 * which reflects backend service health via /readyz. Both are useful, neither implies
 * the other (the API can be healthy while this tab's socket is still reconnecting). */
export function LiveIndicator() {
  const status = useRealtimeStatus();
  return (
    <div
      className="flex items-center gap-2 text-xs text-muted-foreground"
      role="status"
      aria-live="polite"
      title="Realtime connection for this browser tab"
    >
      <span className={cn("size-2 shrink-0 rounded-full", DOT[status])} aria-hidden />
      <span className="sr-only sm:not-sr-only">{LABEL[status]}</span>
    </div>
  );
}
