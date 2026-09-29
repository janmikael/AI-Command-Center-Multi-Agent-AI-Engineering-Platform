import type { components } from "@/lib/api/schema";

type TaskStatus = components["schemas"]["TaskStatus"];
type AgentStatus = components["schemas"]["AgentStatus"];
type RuleCategory = components["schemas"]["RuleCategory"];
type RuleEffect = components["schemas"]["RuleEffect"];

// ─── Relative time ──────────────────────────────────────────────────────
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31536000],
  ["month", 2592000],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];
const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** "2 minutes ago", "just now", "in 3 days" (clock skew). No dependency needed for Phase 1. */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const seconds = (new Date(iso).getTime() - now.getTime()) / 1000;
  if (Math.abs(seconds) < 45) return "just now";
  for (const [unit, secondsInUnit] of UNITS) {
    const value = seconds / secondsInUnit;
    if (Math.abs(value) >= 1) return rtf.format(Math.round(value), unit);
  }
  return rtf.format(Math.round(seconds / 60), "minute");
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

// ─── Task status ─────────────────────────────────────────────────────────
export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  backlog: "Backlog",
  planning: "Planning",
  working: "Working",
  review: "Review",
  awaiting_approval: "Awaiting approval",
  blocked: "Blocked",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** Tailwind classes per status, tuned for both color schemes (bg is a soft tint, text a strong hue). */
export const TASK_STATUS_COLOR: Record<TaskStatus, string> = {
  backlog: "bg-muted text-muted-foreground",
  planning: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  working: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  review: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  awaiting_approval: "bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300",
  blocked: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  completed: "bg-success/15 text-success",
  failed: "bg-destructive/15 text-destructive",
  cancelled: "bg-muted text-muted-foreground line-through decoration-1",
};

export const BOARD_COLUMNS: TaskStatus[] = ["backlog", "planning", "working", "review", "completed"];
export const OTHER_COLUMN_STATUSES: TaskStatus[] = ["awaiting_approval", "blocked", "failed", "cancelled"];

export const PRIORITY_LABEL: Record<number, string> = {
  1: "Urgent",
  2: "High",
  3: "Normal",
  4: "Low",
  5: "Lowest",
};

// ─── Agent status ────────────────────────────────────────────────────────
export const AGENT_STATUS_LABEL: Record<AgentStatus, string> = {
  working: "Working",
  idle: "Idle",
  offline: "Offline",
  disabled: "Disabled",
};

export const AGENT_STATUS_DOT: Record<AgentStatus, string> = {
  working: "bg-success",
  idle: "bg-sky-500",
  offline: "bg-muted-foreground/40",
  disabled: "bg-muted-foreground/40",
};

export function initials(name: string): string {
  const parts = name.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?").concat(parts[1]?.[0] ?? "").toUpperCase();
}

// ─── Events ──────────────────────────────────────────────────────────────
export const SEVERITY_COLOR: Record<string, string> = {
  debug: "text-muted-foreground",
  info: "text-foreground",
  warn: "text-warning",
  error: "text-destructive",
};

// ─── Governance ──────────────────────────────────────────────────────────
export const RULE_CATEGORY_LABEL: Record<RuleCategory, string> = {
  security: "Security",
  tools: "Tools",
  code: "Code",
  infrastructure: "Infrastructure",
};

export const RULE_EFFECT_LABEL: Record<RuleEffect, string> = {
  allow: "Allow",
  require_approval: "Requires approval",
  deny: "Deny",
  advisory: "Advisory",
};

export const RULE_EFFECT_COLOR: Record<RuleEffect, string> = {
  allow: "bg-success/15 text-success",
  require_approval: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  deny: "bg-destructive/15 text-destructive",
  advisory: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
};

// ─── Runs (Phase 2.5) ────────────────────────────────────────────────────
// RunOut.status is typed as a plain string by the API (acc.domain.agent_run.RunStatus is
// a backend-only StrEnum, not exposed as an OpenAPI enum) — Record<string, ...> with a
// fallback in RunStatusBadge, not Record<RunStatus, ...>, matching SEVERITY_COLOR's own
// same-reasoning pattern above.
export const RUN_STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  starting: "Starting",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
  timed_out: "Timed out",
};

export const RUN_STATUS_COLOR: Record<string, string> = {
  queued: "bg-muted text-muted-foreground",
  starting: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  running: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  succeeded: "bg-success/15 text-success",
  failed: "bg-destructive/15 text-destructive",
  cancelled: "bg-muted text-muted-foreground line-through decoration-1",
  timed_out: "bg-destructive/15 text-destructive",
};

/** Only queued/starting/running runs can be cancelled (acc.domain.agent_run.ACTIVE). */
export const ACTIVE_RUN_STATUSES = new Set(["queued", "starting", "running"]);

export function formatCostUsd(value: string | number): string {
  const amount = typeof value === "string" ? Number.parseFloat(value) : value;
  if (!Number.isFinite(amount)) return "—";
  // Six-decimal DB precision is real (acc.db.models.agent_run: Numeric(12,6)), but not
  // meaningful to a human at a glance — four decimals still distinguishes sub-cent runs
  // without the noise of the trailing digits.
  return `$${amount.toFixed(4)}`;
}

export function formatDurationMs(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}
