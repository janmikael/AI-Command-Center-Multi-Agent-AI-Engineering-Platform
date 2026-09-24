import { TASK_STATUS_LABEL } from "@/lib/format";

/** The fields describeEvent actually needs — matches both EventOut (REST) and the
 * WebSocket wire shape closely enough that either can be passed in. */
export interface DescribableEvent {
  type: string;
  task_key: string | null;
  agent_slug: string | null;
  payload: Record<string, unknown>;
}

function str(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" ? value : "";
}

/** A one-line, human sentence for an event row. Falls back to the raw type for anything
 * not explicitly handled (new event types degrade gracefully instead of rendering blank). */
export function describeEvent(event: DescribableEvent): string {
  const { type, payload, task_key: taskKey, agent_slug: agentSlug } = event;
  switch (type) {
    case "task.created":
      return `Created ${taskKey}: "${str(payload, "title")}"`;
    case "task.status_changed": {
      const from = str(payload, "from_status");
      const to = str(payload, "to_status");
      const fromLabel = TASK_STATUS_LABEL[from as keyof typeof TASK_STATUS_LABEL] ?? from;
      const toLabel = TASK_STATUS_LABEL[to as keyof typeof TASK_STATUS_LABEL] ?? to;
      const reason = str(payload, "reason");
      return `Moved ${taskKey} from ${fromLabel} to ${toLabel}${reason ? ` — ${reason}` : ""}`;
    }
    case "task.assigned": {
      const to = str(payload, "to_agent");
      return to ? `Assigned ${taskKey} to ${to}` : `Unassigned ${taskKey}`;
    }
    case "task.updated":
      return `Updated ${taskKey}`;
    case "agent.message":
      return str(payload, "text");
    case "agent.decision":
      return str(payload, "decision");
    case "agent.registered":
      return `${agentSlug} registered as ${str(payload, "role")}`;
    case "project.created":
      return `Created project ${str(payload, "name")}`;
    case "repository.added":
      return `Added repository ${str(payload, "name")}`;
    default:
      return type;
  }
}
