"use client";

import { Activity, Bot, KanbanSquare } from "lucide-react";
import Link from "next/link";
import { use } from "react";

import { EventRow } from "@/components/activity/event-row";
import { AgentStatusBadge } from "@/components/agents/agent-status-badge";
import { EmptyState } from "@/components/shell/empty-state";
import { PageHeader } from "@/components/shell/page-header";
import { TaskStatusBadge } from "@/components/tasks/task-status-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgent } from "@/lib/api/hooks";
import { formatRelativeTime, initials } from "@/lib/format";

export default function AgentDetailPage({ params }: PageProps<"/agents/[slug]">) {
  const { slug } = use(params);
  const { data, isPending, isError } = useAgent(slug);

  if (isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-20 rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <EmptyState icon={Bot} title="Agent not found" description={`No agent named "${slug}".`} />
    );
  }

  const { agent, open_tasks: openTasks, recent_events: recentEvents } = data;

  return (
    <>
      <PageHeader title={agent.display_name} description={agent.role} />

      <Card className="mb-8">
        <CardContent className="flex flex-wrap items-center gap-4">
          <Avatar className="size-12">
            <AvatarFallback className="text-base">{initials(agent.display_name)}</AvatarFallback>
          </Avatar>
          <div className="flex flex-wrap items-center gap-4">
            <AgentStatusBadge status={agent.status} />
            <span className="text-sm text-muted-foreground">
              {agent.active_task_count} active task{agent.active_task_count === 1 ? "" : "s"}
            </span>
            <span className="text-sm text-muted-foreground">
              {agent.last_activity_at
                ? `Active ${formatRelativeTime(agent.last_activity_at)}`
                : "No activity yet"}
            </span>
          </div>
          <div className="ml-auto flex flex-wrap gap-1.5">
            {agent.capabilities.map((capability) => (
              <Badge key={capability} variant="secondary">
                {capability}
              </Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-8 lg:grid-cols-2">
        <section>
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">Open tasks</h2>
          {openTasks.length === 0 ? (
            <EmptyState icon={KanbanSquare} title="No open tasks" description="Nothing assigned right now." />
          ) : (
            <Card>
              <CardContent className="divide-y p-0">
                {openTasks.map((task) => (
                  <Link
                    key={task.key}
                    href={`/tasks/${task.key}`}
                    className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-muted/40"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{task.title}</p>
                      <p className="text-xs text-muted-foreground">{task.key}</p>
                    </div>
                    <TaskStatusBadge status={task.status} />
                  </Link>
                ))}
              </CardContent>
            </Card>
          )}
        </section>

        <section>
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">Recent activity</h2>
          {recentEvents.length === 0 ? (
            <EmptyState icon={Activity} title="No activity yet" description="Nothing logged yet." />
          ) : (
            <Card>
              <CardContent className="divide-y p-0 px-4">
                {recentEvents.map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </CardContent>
            </Card>
          )}
        </section>
      </div>
    </>
  );
}
