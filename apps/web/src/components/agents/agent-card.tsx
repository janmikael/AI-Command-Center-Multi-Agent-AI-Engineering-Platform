import Link from "next/link";

import { AgentStatusBadge } from "@/components/agents/agent-status-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Card, CardContent } from "@/components/ui/card";
import type { AgentOut } from "@/lib/api/hooks";
import { formatRelativeTime, initials } from "@/lib/format";

export function AgentCard({ agent }: { agent: AgentOut }) {
  return (
    <Link href={`/agents/${agent.slug}`}>
      <Card className="h-full transition-colors hover:bg-muted/40">
        <CardContent className="flex items-start gap-3">
          <Avatar>
            <AvatarFallback>{initials(agent.display_name)}</AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <p className="truncate font-medium">{agent.display_name}</p>
              <AgentStatusBadge status={agent.status} />
            </div>
            <p className="truncate text-sm text-muted-foreground">{agent.role}</p>
            <p className="mt-2 truncate text-sm">
              {agent.current_task ? (
                <span>
                  Working on{" "}
                  <span className="font-medium text-foreground">{agent.current_task.key}</span>
                </span>
              ) : (
                <span className="text-muted-foreground">No active task</span>
              )}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {agent.last_activity_at
                ? `Active ${formatRelativeTime(agent.last_activity_at)}`
                : "No activity yet"}
            </p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
