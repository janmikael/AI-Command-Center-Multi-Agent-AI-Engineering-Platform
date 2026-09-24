import type { components } from "@/lib/api/schema";
import { AGENT_STATUS_DOT, AGENT_STATUS_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

type AgentStatus = components["schemas"]["AgentStatus"];

export function AgentStatusBadge({ status }: { status: AgentStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span className={cn("size-2 shrink-0 rounded-full", AGENT_STATUS_DOT[status])} aria-hidden />
      {AGENT_STATUS_LABEL[status]}
    </span>
  );
}
