import type { components } from "@/lib/api/schema";

type TaskStatus = components["schemas"]["TaskStatus"];
type AgentStatus = components["schemas"]["AgentStatus"];

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
