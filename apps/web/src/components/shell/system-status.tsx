"use client";

import { useEffect, useState } from "react";

import type { Readiness } from "@/lib/api/client";
import { cn } from "@/lib/utils";

type Status = "checking" | "ready" | "degraded" | "unreachable";

const POLL_MS = 15_000;

const LABEL: Record<Status, string> = {
  checking: "Checking…",
  ready: "All systems ready",
  degraded: "Degraded",
  unreachable: "API unreachable",
};

const DOT: Record<Status, string> = {
  checking: "bg-muted-foreground/50",
  ready: "bg-success",
  degraded: "bg-warning",
  unreachable: "bg-destructive",
};

function describe(report: Readiness | null): string | undefined {
  if (!report) return undefined;
  return Object.entries(report.checks)
    .map(([name, check]) => `${name}: ${check.ok ? "ok" : `failing${check.detail ? ` (${check.detail})` : ""}`}`)
    .join("\n");
}

/** Polls /readyz. Phase 1 replaces polling with the WebSocket connection state. */
export function SystemStatus() {
  const [status, setStatus] = useState<Status>("checking");
  const [report, setReport] = useState<Readiness | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        const response = await fetch("/readyz", { cache: "no-store" });
        const body = (await response.json()) as Readiness;
        if (cancelled) return;
        setReport(body);
        setStatus(response.ok ? "ready" : "degraded");
      } catch {
        if (!cancelled) {
          setReport(null);
          setStatus("unreachable");
        }
      }
    }
    void check();
    const timer = setInterval(check, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div
      className="flex items-center gap-2 text-xs text-muted-foreground"
      title={describe(report)}
      role="status"
      aria-live="polite"
    >
      <span className={cn("size-2 rounded-full", DOT[status])} aria-hidden />
      <span>{LABEL[status]}</span>
    </div>
  );
}
