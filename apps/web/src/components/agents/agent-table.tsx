import Link from "next/link";

import { AgentStatusBadge } from "@/components/agents/agent-status-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import type { AgentOut } from "@/lib/api/hooks";
import { formatRelativeTime, initials } from "@/lib/format";

export function AgentTable({ agents }: { agents: AgentOut[] }) {
  return (
    <div className="overflow-hidden rounded-xl ring-1 ring-foreground/10">
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 font-medium">Agent</th>
            <th className="px-4 py-2.5 font-medium">Status</th>
            <th className="px-4 py-2.5 font-medium">Current task</th>
            <th className="px-4 py-2.5 font-medium">Last activity</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {agents.map((agent) => (
            <tr key={agent.slug} className="hover:bg-muted/40">
              <td className="px-4 py-3">
                <Link href={`/agents/${agent.slug}`} className="flex items-center gap-2.5">
                  <Avatar className="size-7">
                    <AvatarFallback className="text-xs">
                      {initials(agent.display_name)}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0">
                    <p className="truncate font-medium">{agent.display_name}</p>
                    <p className="truncate text-xs text-muted-foreground">{agent.role}</p>
                  </div>
                </Link>
              </td>
              <td className="px-4 py-3">
                <AgentStatusBadge status={agent.status} />
              </td>
              <td className="px-4 py-3">
                {agent.current_task ? (
                  <Link
                    href={`/tasks/${agent.current_task.key}`}
                    className="hover:underline"
                  >
                    {agent.current_task.key}
                  </Link>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-4 py-3 text-muted-foreground">
                {agent.last_activity_at ? formatRelativeTime(agent.last_activity_at) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
